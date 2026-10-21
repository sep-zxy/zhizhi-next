"""Exercise a desktop-created note through PostgreSQL and a real local ReMe service."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from m1_cloud_smoke import ISSUER, now, save_json, token

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import (
    ReMeAdapter,
    current_notes_for_paths,
    reme_note_path,
    run_once,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "acceptance/runs/s04-ui-local-20260926-v2/electron-s04.json"


def read_business(dsn: str, account_id: uuid.UUID, note_id: uuid.UUID) -> dict[str, Any]:
    with connect(dsn) as conn:
        note = conn.execute(
            "SELECT account_id, note_id, revision, content_text, deleted_at "
            "FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account_id, note_id),
        ).fetchone()
        job = conn.execute(
            "SELECT action, state, attempt_count, source_version, last_error "
            "FROM growth_memory_jobs WHERE account_id=%s AND source_id=%s "
            "AND source_version=1",
            (account_id, note_id),
        ).fetchone()
        current = conn.execute(
            "SELECT source_version, reme_path, deleted_at FROM growth_memory_current "
            "WHERE account_id=%s AND source_id=%s",
            (account_id, note_id),
        ).fetchone()
        device = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s "
            "ORDER BY last_seen LIMIT 1",
            (account_id,),
        ).fetchone()
    assert note is not None and job is not None and device is not None
    return {"note": note, "job": job, "current": current, "device_id": device["device_id"]}


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    note = state["note"]
    return {
        "note_id": str(note["note_id"]),
        "revision": note["revision"],
        "note_sha256": hashlib.sha256(note["content_text"].encode()).hexdigest(),
        "note_deleted": note["deleted_at"] is not None,
        "job": state["job"],
        "current": state["current"],
        "device_id": str(state["device_id"]),
    }


async def exercise(
    phase: str, dsn: str, reme_url: str, source_file: Path, workspace: Path
) -> dict[str, Any]:
    source = json.loads(source_file.read_text(encoding="utf-8"))
    account_id = uuid.UUID(source["account_id"])
    note_id = uuid.UUID(source["note_id"])
    path = reme_note_path(account_id, note_id)
    before = read_business(dsn, account_id, note_id)
    assert before["note"]["content_text"] == source["note_text"]
    assert before["note"]["revision"] == 1
    assert before["note"]["deleted_at"] is None

    checks: dict[str, bool] = {}
    extra: dict[str, Any] = {}
    if phase == "outage":
        checks["service_really_unavailable"] = False
        try:
            async with httpx.AsyncClient(base_url=reme_url, timeout=2) as client:
                await client.post("/version", json={})
        except httpx.HTTPError:
            checks["service_really_unavailable"] = True
        assert checks["service_really_unavailable"]
        async with httpx.AsyncClient(base_url=reme_url, timeout=2) as client:
            outcome = await run_once(dsn, ReMeAdapter(client))
        after = read_business(dsn, account_id, note_id)
        checks["outage_failed_persistent_job"] = (
            outcome == "failed" and before["job"]["state"] == "pending"
            and after["job"]["state"] == "failed"
            and after["job"]["attempt_count"] == before["job"]["attempt_count"] + 1
            and after["job"]["last_error"] is not None
        )
        checks["business_note_unchanged"] = after["note"] == before["note"]
        extra["worker_outcome"] = outcome
    else:
        if phase == "recovery":
            assert before["job"]["state"] == "failed"
        else:
            assert before["job"]["state"] == "succeeded"
        async with httpx.AsyncClient(base_url=reme_url, timeout=20) as client:
            version = await client.post("/version", json={})
            version.raise_for_status()
            extra["reme_version"] = version.json()["answer"]
            adapter = ReMeAdapter(client)
            if phase == "recovery":
                with connect(dsn) as conn:
                    conn.execute(
                        "UPDATE growth_memory_jobs SET next_retry_at=now() "
                        "WHERE account_id=%s AND source_id=%s AND source_version=1",
                        (account_id, note_id),
                    )
                outcome = await run_once(dsn, adapter)
            else:
                outcome = "already_succeeded"
            after = read_business(dsn, account_id, note_id)
            checks["same_version_retry_succeeded"] = (
                outcome in {"succeeded", "already_succeeded"}
                and after["job"]["state"] == "succeeded"
                and after["job"]["attempt_count"] == 2
                and after["current"] is not None
                and after["current"]["source_version"] == 1
                and after["current"]["reme_path"] == path
            )
            checks["business_note_unchanged"] = after["note"] == before["note"]
            projected_file = workspace / Path(path)
            checks["real_reme_file_contains_note"] = (
                projected_file.exists()
                and source["note_text"] in projected_file.read_text(encoding="utf-8")
            )
            extra["projected_file_sha256"] = (
                hashlib.sha256(projected_file.read_bytes()).hexdigest()
                if projected_file.exists() else None
            )
            reindex = await client.post("/reindex", json={})
            reindex.raise_for_status()
            search_started = time.monotonic()
            paths: list[str] = []
            for _ in range(30):
                paths = await adapter.search_paths("登录成功")
                if path in paths:
                    break
                await asyncio.sleep(1)
            extra["index_wait_seconds"] = round(time.monotonic() - search_started, 3)
            extra["reme_search_paths"] = paths
            checks["real_reme_search_finds_projection"] = path in paths
            visible = current_notes_for_paths(dsn, account_id, paths)
            foreign = current_notes_for_paths(dsn, uuid.uuid4(), paths)
            checks["account_and_version_backcheck"] = (
                len(visible) == 1 and visible[0]["note_id"] == str(note_id)
                and visible[0]["content_text"] == source["note_text"] and foreign == []
            )

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        app = create_app(CloudSettings(
            dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem),
            reme_url=reme_url,
        ))
        headers = {
            "Authorization": f"Bearer {token(private_key, account_id)}",
            "X-Device-Id": str(after["device_id"]),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as cloud:
            response = await cloud.get(
                "/v1/growth/context", params={"query": "登录成功"}, headers=headers
            )
        payload = response.json()
        extra["context_status"] = response.status_code
        extra["context_note_ids"] = [item["note_id"] for item in payload.get("notes", [])]
        extra["context_topic_ids"] = [item["topic_id"] for item in payload.get("topics", [])]
        checks["real_cloud_context_returns_projected_note"] = (
            response.status_code == 200 and payload.get("memory_status") == "current"
            and any(item["note_id"] == str(note_id)
                    and item["content_text"] == source["note_text"]
                    for item in payload.get("notes", []))
            and source["topic_id"] in extra["context_topic_ids"]
        )
        extra["worker_outcome"] = outcome

    return {
        "phase": phase, "mode": "local_live_reme",
        "source_run": source_file.parent.name,
        "account_id": str(account_id), "reme_path": path,
        "started_from": public_state(before), "finished_with": public_state(after),
        "checks": checks, "extra": extra,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "finished_at": now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["outage", "recovery", "verify"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--reme-url", default="http://127.0.0.1:18773")
    parser.add_argument("--source-file", type=Path, default=SOURCE)
    parser.add_argument(
        "--workspace", type=Path,
        default=ROOT / "acceptance/runs/m4-reme-live-20260926-v2/workspace",
    )
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id:
        parser.error("run-id must be a single directory name")
    run_dir = ROOT / "acceptance/runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(exercise(
        args.phase, args.dsn, args.reme_url,
        args.source_file.resolve(), args.workspace.resolve(),
    ))
    save_json(run_dir / f"{args.phase}.json", result)
    print(f"{args.phase}: {result['status']} {result['checks']}")
    if result["status"] != "PASS":
        raise AssertionError(f"ReMe {args.phase} live business checks failed")


if __name__ == "__main__":
    main()
