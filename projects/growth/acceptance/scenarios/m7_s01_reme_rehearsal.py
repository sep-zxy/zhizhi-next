"""Check the installed S01 cloud chain through a real, recoverable ReMe service."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import (
    ReMeAdapter,
    current_attempts_for_paths,
    current_notes_for_paths,
    reme_attempt_path,
    reme_note_path,
    run_once,
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def business(dsn: str, account: uuid.UUID, task: uuid.UUID) -> dict:
    with connect(dsn) as conn:
        task_row = conn.execute(
            "SELECT progress FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, task),
        ).fetchone()
        attempts = conn.execute(
            "SELECT attempt_id,answer_text,feedback,status FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s ORDER BY created_at,attempt_id",
            (account, task),
        ).fetchall()
        notes = conn.execute(
            "SELECT note_id,content_text,revision FROM growth_notes "
            "WHERE account_id=%s AND task_id=%s AND deleted_at IS NULL",
            (account, task),
        ).fetchall()
        jobs = conn.execute(
            "SELECT source_type,source_id,source_version,state,attempt_count "
            "FROM growth_memory_jobs WHERE account_id=%s "
            "ORDER BY source_type,source_id,source_version",
            (account,),
        ).fetchall()
        current = conn.execute(
            "SELECT source_type,source_id,source_version,reme_path "
            "FROM growth_memory_current WHERE account_id=%s ORDER BY source_type,source_id",
            (account,),
        ).fetchall()
    return {
        "progress": task_row["progress"] if task_row else None,
        "attempts": {str(a["attempt_id"]): {
            "answer_sha256": hashlib.sha256(a["answer_text"].encode()).hexdigest(),
            "feedback_sha256": hashlib.sha256(
                json.dumps(a["feedback"], ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest() if a["feedback"] else None,
            "status": a["status"],
        } for a in attempts},
        "notes": {str(n["note_id"]): {
            "content_sha256": hashlib.sha256(n["content_text"].encode()).hexdigest(),
            "revision": n["revision"],
        } for n in notes},
        "jobs": [{**j, "source_id": str(j["source_id"])} for j in jobs],
        "current": [{**c, "source_id": str(c["source_id"])} for c in current],
    }


async def run(args: argparse.Namespace) -> dict:
    root = args.run.resolve()
    prepared = read_json(root / "preparation.json")
    ui = read_json(root / "artifacts" / "cloud-ui.json")
    audit = read_json(root / "audit.json")
    assert audit["status"] == "PASS" and prepared["mode"] == "replay_not_live"
    account = uuid.UUID(ui["account_id"])
    task = uuid.UUID(ui["task_id"])
    note = uuid.UUID(prepared["note_id"])
    attempts = [uuid.UUID(value) for value in prepared["attempt_ids"]]
    paths = [reme_note_path(account, note)] + [reme_attempt_path(account, a) for a in attempts]
    before = business(args.dsn, account, task)
    baseline = (
        read_json(root / "reme-outage.json")["business_before"]
        if args.phase == "recovery" else before
    )
    checks: dict[str, bool] = {}
    evidence: dict = {"mode": "replay_not_live", "phase": args.phase,
                      "cloud_kind": "isolated_local_postgresql", "account_id": str(account),
                      "task_id": str(task), "business_before": before,
                      "expected_paths": paths}

    if args.phase == "outage":
        checks["five_projection_jobs_queued"] = len(before["jobs"]) == 5 and all(
            job["state"] in {"pending", "skipped"} for job in before["jobs"]
        ) and not before["current"]
        checks["business_chain_complete"] = before["progress"] == "completed" and (
            set(before["attempts"]) == {str(a) for a in attempts}
        ) and set(before["notes"]) == {str(note)}
        try:
            async with httpx.AsyncClient(base_url=args.reme_url, timeout=2) as client:
                await client.post("/version", json={})
            checks["reme_unavailable"] = False
        except httpx.HTTPError:
            checks["reme_unavailable"] = True
        assert checks["reme_unavailable"]
        async with httpx.AsyncClient(base_url=args.reme_url, timeout=2) as client:
            outcomes = []
            for _ in range(5):
                outcome = await run_once(args.dsn, ReMeAdapter(client))
                outcomes.append(outcome)
                if outcome == "failed":
                    break
        after = business(args.dsn, account, task)
        checks["failed_job_durable"] = "failed" in outcomes and sum(
            j["state"] == "failed" for j in after["jobs"]
        ) == 1 and not after["current"]
        checks["business_write_survives_outage"] = (
            before["progress"] == after["progress"]
            and before["attempts"] == after["attempts"]
            and before["notes"] == after["notes"]
        )
        evidence.update({"worker_outcomes": outcomes, "business_after": after})
    else:
        assert before["progress"] == baseline["progress"]
        with connect(args.dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET next_retry_at=now() "
                "WHERE account_id=%s AND state='failed'", (account,),
            )
        async with httpx.AsyncClient(base_url=args.reme_url, timeout=25) as client:
            version = await client.post("/version", json={})
            version.raise_for_status()
            evidence["reme_version"] = version.json().get("answer")
            adapter = ReMeAdapter(client)
            outcomes = []
            for _ in range(12):
                outcome = await run_once(args.dsn, adapter)
                if outcome is None:
                    break
                outcomes.append(outcome)
            after = business(args.dsn, account, task)
            evidence["worker_outcomes"] = outcomes
            evidence["business_after"] = after
            checks["all_jobs_terminal"] = len(after["jobs"]) == 5 and all(
                j["state"] in {"skipped", "succeeded"} for j in after["jobs"]
            ) and sum(j["state"] == "skipped" for j in after["jobs"]) == 2
            checks["latest_versions_projected"] = len(after["current"]) == 3 and (
                {c["reme_path"] for c in after["current"]} == set(paths)
            ) and all(c["source_version"] == (1 if c["source_type"] == "note" else 2)
                      for c in after["current"])
            checks["business_chain_unchanged"] = (
                baseline["progress"] == after["progress"]
                and baseline["attempts"] == after["attempts"]
                and baseline["notes"] == after["notes"]
            )
            files = [args.workspace / Path(p) for p in paths]
            feedback_run = root.parent / prepared["source_run_id"]
            feedback_record = read_json(
                feedback_run / "artifacts" / "s01-feedback-live-ui.json"
            )
            feedback = [turn["feedback"]["summary"]
                        for turn in feedback_record["turns"]]
            checks["real_reme_files_include_note_and_feedback"] = all(
                file.exists() for file in files
            ) and all(
                feedback[i] in files[i + 1].read_text(encoding="utf-8")
                for i in range(2)
            ) and "thenCombine" in files[0].read_text(encoding="utf-8")
            evidence["file_sha256"] = {
                paths[i]: hashlib.sha256(file.read_bytes()).hexdigest()
                for i, file in enumerate(files) if file.exists()
            }
            reindex = await client.post("/reindex", json={})
            reindex.raise_for_status()
            found: list[str] = []
            started = time.monotonic()
            for _ in range(30):
                found = await adapter.search_paths("执行器", limit=50)
                if set(paths) <= set(found):
                    break
                await asyncio.sleep(1)
            evidence["index_wait_seconds"] = round(time.monotonic() - started, 3)
            evidence["search_paths"] = found
            checks["real_reme_search_finds_chain"] = set(paths) <= set(found)
            visible_notes = current_notes_for_paths(args.dsn, account, found)
            visible_attempts = current_attempts_for_paths(args.dsn, account, found)
            foreign_notes = current_notes_for_paths(args.dsn, uuid.uuid4(), found)
            foreign_attempts = current_attempts_for_paths(args.dsn, uuid.uuid4(), found)
            checks["account_and_version_backcheck"] = (
                {n["note_id"] for n in visible_notes} == {str(note)}
                and {a["attempt_id"] for a in visible_attempts} == {str(a) for a in attempts}
                and all(a["historical"] for a in visible_attempts)
                and not foreign_notes and not foreign_attempts
            )
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        issuer = "https://growth-acceptance.invalid/auth/v1"
        from datetime import UTC, datetime, timedelta
        access_token = jwt.encode({
            "iss": issuer, "aud": "authenticated", "sub": str(account),
            "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=5),
        }, private_key, algorithm="RS256")
        settings = CloudSettings(
            args.dsn, AuthVerifier(issuer=issuer, public_key_pem=public_pem),
            reme_url=args.reme_url,
        )
        headers = {"Authorization": f"Bearer {access_token}",
                   "X-Device-Id": ui["summary"]["device_id"]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings)),
            base_url="http://isolated-growth.test", timeout=20,
        ) as client:
            response = await client.get(
                "/v1/growth/context", params={"query": "执行器"}, headers=headers,
            )
        evidence["cloud_context_status"] = response.status_code
        if response.status_code == 200:
            payload = response.json()
            evidence["cloud_context_summary"] = {
                "memory_status": payload.get("memory_status"),
                "note_ids": [n["note_id"] for n in payload.get("notes", [])],
                "attempt_ids": [a["attempt_id"] for a in payload.get("attempts", [])],
                "topic_ids": [t["topic_id"] for t in payload.get("topics", [])],
            }
            checks["authenticated_cloud_context"] = (
                payload.get("memory_status") == "current"
                and str(note) in evidence["cloud_context_summary"]["note_ids"]
                and {str(a) for a in attempts} <= set(
                    evidence["cloud_context_summary"]["attempt_ids"]
                )
                and all(a.get("historical") is True for a in payload.get("attempts", []))
            )
        else:
            checks["authenticated_cloud_context"] = False

    evidence["checks"] = checks
    evidence["status"] = "PASS" if all(checks.values()) else "FAIL"
    (root / f"reme-{args.phase}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["outage", "recovery"])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--reme-url", default="http://127.0.0.1:18775")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps({"phase": result["phase"], "status": result["status"],
                      "checks": result["checks"]}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
