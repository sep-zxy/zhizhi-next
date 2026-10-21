"""Recover the exact S03 N1/A1/A2 chain through real ReMe and cloud backchecks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import (
    ReMeAdapter, current_attempts_for_paths, current_notes_for_paths,
    reme_attempt_path, reme_note_path, run_once,
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


async def run(args: argparse.Namespace) -> dict:
    root = args.run_dir.resolve()
    prep = load(root / "preparation.json")
    ui = load(root / "artifacts" / "note-ui.json")
    outage = load(root / "outage-audit.json")
    account = uuid.UUID(prep["account_id"])
    task_id = uuid.UUID(prep["task_id"])
    note_id = uuid.UUID(ui["note_id"])
    with connect(args.dsn) as conn:
        conn.execute(
            "UPDATE growth_memory_jobs SET next_retry_at=now() "
            "WHERE account_id=%s AND state='failed'", (account,),
        )
        attempt_rows = conn.execute(
            "SELECT attempt_id,answer_text FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s ORDER BY created_at",
            (account, task_id),
        ).fetchall()
    attempt_ids = [row["attempt_id"] for row in attempt_rows]
    paths = [reme_note_path(account, note_id)] + [
        reme_attempt_path(account, item) for item in attempt_ids
    ]
    evidence: dict = {
        "mode": "replay_not_live", "stage_status": "BLOCKED",
        "account_id": str(account), "task_id": str(task_id),
        "note_id": str(note_id), "expected_paths": paths,
    }
    checks: dict[str, bool] = {}
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
        evidence["worker_outcomes"] = outcomes
        with connect(args.dsn) as conn:
            jobs = conn.execute(
                "SELECT source_type,source_id,source_version,state,attempt_count "
                "FROM growth_memory_jobs WHERE account_id=%s "
                "ORDER BY source_type,source_id,source_version", (account,),
            ).fetchall()
            current = conn.execute(
                "SELECT source_type,source_id,source_version,reme_path "
                "FROM growth_memory_current WHERE account_id=%s "
                "ORDER BY source_type,source_id", (account,),
            ).fetchall()
            note = conn.execute(
                "SELECT revision,content_text FROM growth_notes "
                "WHERE account_id=%s AND note_id=%s", (account, note_id),
            ).fetchone()
        evidence["jobs_after_recovery"] = [dict(row) for row in jobs]
        evidence["current_after_recovery"] = [dict(row) for row in current]
        checks["durable_failed_jobs_recovered"] = len(jobs) == 5 and all(
            row["state"] in {"skipped", "succeeded"} for row in jobs
        ) and sum(row["state"] == "skipped" for row in jobs) == 2 and any(
            row["attempt_count"] >= 2 and row["state"] == "succeeded"
            for row in jobs
        )
        checks["one_current_projection_per_source"] = len(current) == 3 and (
            {row["reme_path"] for row in current} == set(paths)
        ) and all(row["source_version"] == (
            1 if row["source_type"] == "note" else 2
        ) for row in current)
        files = [args.workspace / Path(item) for item in paths]
        rebase = load(root.parent / prep["source_run_id"] / "preparation.json")
        feedback_run = root.parent / rebase["source_run_id"]
        feedback = load(feedback_run / "artifacts" / "feedback-ui.json")
        summaries = [turn["feedback"]["summary"] for turn in feedback["turns"]]
        checks["real_reme_files_preserve_n1_and_feedback"] = all(
            item.exists() for item in files
        ) and ui["note_text"] in files[0].read_text(encoding="utf-8") and all(
            summaries[index] in files[index + 1].read_text(encoding="utf-8")
            for index in range(2)
        )
        evidence["file_sha256"] = {
            paths[index]: hashlib.sha256(item.read_bytes()).hexdigest()
            for index, item in enumerate(files) if item.exists()
        }
        reindex = await client.post("/reindex", json={})
        reindex.raise_for_status()
        started = time.monotonic()
        found: list[str] = []
        for _ in range(30):
            found = await adapter.search_paths("TTL", limit=50)
            if set(paths) <= set(found):
                break
            await asyncio.sleep(1)
        evidence["search_wait_seconds"] = round(time.monotonic() - started, 3)
        evidence["search_paths"] = found
        checks["real_reme_search_finds_all_sources"] = set(paths) <= set(found)
        visible_notes = current_notes_for_paths(args.dsn, account, found)
        visible_attempts = current_attempts_for_paths(args.dsn, account, found)
        foreign_notes = current_notes_for_paths(args.dsn, uuid.uuid4(), found)
        foreign_attempts = current_attempts_for_paths(args.dsn, uuid.uuid4(), found)
        checks["account_version_backcheck"] = (
            {item["note_id"] for item in visible_notes} == {str(note_id)}
            and {item["attempt_id"] for item in visible_attempts}
            == {str(item) for item in attempt_ids}
            and all(item["historical"] for item in visible_attempts)
            and not foreign_notes and not foreign_attempts
        )
        checks["n1_business_unchanged"] = note is not None and (
            note["revision"] == 1 and note["content_text"] == ui["note_text"]
            and outage["status"] == "PASS"
        )
        with connect(args.dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET state='pending',next_retry_at=now() "
                "WHERE account_id=%s AND source_type='note' AND source_id=%s "
                "AND source_version=1 AND state='succeeded'", (account, note_id),
            )
        evidence["note_replay_outcome"] = await run_once(args.dsn, adapter)
        with connect(args.dsn) as conn:
            note_current = conn.execute(
                "SELECT source_version,reme_path FROM growth_memory_current "
                "WHERE account_id=%s AND source_type='note' AND source_id=%s",
                (account, note_id),
            ).fetchall()
            note_job = conn.execute(
                "SELECT state,attempt_count FROM growth_memory_jobs "
                "WHERE account_id=%s AND source_type='note' AND source_id=%s "
                "AND source_version=1", (account, note_id),
            ).fetchone()
        checks["same_job_replay_no_duplicate_current"] = (
            evidence["note_replay_outcome"] == "succeeded"
            and len(note_current) == 1
            and note_current[0]["source_version"] == 1
            and note_current[0]["reme_path"] == paths[0]
            and note_job["state"] == "succeeded"
        )

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    issuer = "https://growth-acceptance.invalid/auth/v1"
    access_token = jwt.encode({
        "iss": issuer, "aud": "authenticated", "sub": str(account),
        "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=5),
    }, private_key, algorithm="RS256")
    settings = CloudSettings(
        args.dsn, AuthVerifier(issuer=issuer, public_key_pem=public_pem),
        reme_url=args.reme_url,
    )
    headers = {"Authorization": f"Bearer {access_token}",
               "X-Device-Id": ui["sync"]["device_id"]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)),
        base_url="http://isolated-growth.test", timeout=20,
    ) as cloud:
        response = await cloud.get(
            "/v1/growth/context", params={"query": "TTL"}, headers=headers,
        )
    evidence["cloud_context_status"] = response.status_code
    if response.status_code == 200:
        payload = response.json()
        evidence["cloud_context_summary"] = {
            "memory_status": payload.get("memory_status"),
            "note_ids": [item["note_id"] for item in payload.get("notes", [])],
            "attempt_ids": [item["attempt_id"] for item in payload.get("attempts", [])],
        }
        checks["authenticated_cloud_context"] = (
            payload.get("memory_status") == "current"
            and str(note_id) in evidence["cloud_context_summary"]["note_ids"]
            and {str(item) for item in attempt_ids}
            <= set(evidence["cloud_context_summary"]["attempt_ids"])
            and all(item.get("historical") is True
                    for item in payload.get("attempts", []))
        )
    else:
        evidence["cloud_context_error"] = response.text[:500]
        checks["authenticated_cloud_context"] = False
    evidence["checks"] = checks
    evidence["status"] = "PASS" if all(checks.values()) else "FAIL"
    (root / "recovery-audit.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--reme-url", default="http://127.0.0.1:18775")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps({"status": result["status"], "checks": result["checks"]},
                     ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
