"""Exercise note revision, deletion and duplicate jobs against real ReMe."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
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
from ahadiff.growth.cloud.memory import ReMeAdapter, current_notes_for_paths, reme_note_path, run_once

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "acceptance/runs/s04-ui-local-20260926-v2/electron-s04.json"
FIRST_TEXT = "M4LIFECYCLE 第一版：访问时检查缓存。"
SECOND_TEXT = "M4LIFECYCLE 第二版：访问时惰性失效，没有后台刷新。"


def state(dsn: str, account_id: uuid.UUID, note_id: uuid.UUID) -> dict[str, Any]:
    with connect(dsn) as conn:
        note = conn.execute(
            "SELECT revision, content_text, deleted_at FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account_id, note_id)
        ).fetchone()
        current = conn.execute(
            "SELECT source_version, reme_path, deleted_at FROM growth_memory_current "
            "WHERE account_id=%s AND source_id=%s", (account_id, note_id)
        ).fetchone()
        jobs = conn.execute(
            "SELECT source_version, action, state, attempt_count FROM growth_memory_jobs "
            "WHERE account_id=%s AND source_id=%s ORDER BY source_version",
            (account_id, note_id),
        ).fetchall()
    return {
        "revision": note["revision"] if note else None,
        "note_sha256": hashlib.sha256(note["content_text"].encode()).hexdigest() if note else None,
        "note_deleted": note["deleted_at"] is not None if note else None,
        "current": current,
        "jobs": jobs,
    }


async def wait_search(adapter: ReMeAdapter, path: str, *, present: bool) -> tuple[bool, list[str]]:
    paths: list[str] = []
    for _ in range(30):
        paths = await adapter.search_paths("M4LIFECYCLE")
        if (path in paths) == present:
            return True, paths
        await asyncio.sleep(1)
    return False, paths


async def exercise(run_dir: Path, dsn: str, reme_url: str) -> dict[str, Any]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    account_id = uuid.UUID(source["account_id"])
    topic_id = uuid.UUID(source["topic_id"])
    note_id = uuid.uuid4()
    path = reme_note_path(account_id, note_id)
    projected_file = run_dir / "workspace" / Path(path)
    with connect(dsn) as conn:
        device = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s "
            "ORDER BY last_seen LIMIT 1", (account_id,)
        ).fetchone()
    assert device is not None
    device_id = device["device_id"]
    save_json(run_dir / "chain.json", {
        "source_run": SOURCE.parent.name, "database_name": dsn.split("dbname=", 1)[1].split()[0],
        "account_id": str(account_id), "device_id": str(device_id),
        "topic_id": str(topic_id), "note_id": str(note_id), "reme_path": path,
    })
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(
        dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem), reme_url=reme_url
    ))
    headers = {
        "Authorization": f"Bearer {token(private_key, account_id)}",
        "X-Device-Id": str(device_id), "X-Trace-Id": str(uuid.uuid4()),
    }
    steps: list[dict[str, Any]] = []

    def record(label: str, passed: bool, actual: Any) -> None:
        item = {"step": label, "at": now(), "verdict": "PASS" if passed else "FAIL",
                "actual": actual}
        steps.append(item)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        if not passed:
            raise AssertionError(f"{label}: {actual}")

    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://growth.test") as cloud,
        httpx.AsyncClient(base_url=reme_url, timeout=20) as reme,
    ):
        adapter = ReMeAdapter(reme)
        created = await cloud.post("/v1/notes", headers=headers, json={
            "operation_id": str(uuid.uuid4()), "note_id": str(note_id),
            "topic_id": str(topic_id), "content_text": FIRST_TEXT,
        })
        snapshot = state(dsn, account_id, note_id)
        record("create_enqueues_version_1", (
            created.status_code == 201 and snapshot["revision"] == 1
            and snapshot["jobs"] == [{"source_version": 1, "action": "upsert",
                                      "state": "pending", "attempt_count": 0}]
        ), {"http_status": created.status_code, "state": snapshot})

        outcome = await run_once(dsn, adapter)
        indexed = await reme.post("/reindex", json={})
        indexed.raise_for_status()
        found, paths = await wait_search(adapter, path, present=True)
        snapshot = state(dsn, account_id, note_id)
        record("real_reme_version_1", (
            outcome == "succeeded" and projected_file.exists()
            and FIRST_TEXT in projected_file.read_text(encoding="utf-8")
            and found and snapshot["current"] is not None
            and snapshot["current"]["source_version"] == 1
        ), {"worker": outcome, "indexed": found, "paths": paths, "state": snapshot})

        revised = await cloud.put(f"/v1/notes/{note_id}/revisions", headers=headers,
                                  json={"operation_id": str(uuid.uuid4()),
                                        "base_revision": 1, "content_text": SECOND_TEXT})
        hidden_before_projection = current_notes_for_paths(dsn, account_id, [path])
        snapshot = state(dsn, account_id, note_id)
        record("revision_hides_old_projection", (
            revised.status_code == 200 and snapshot["revision"] == 2
            and hidden_before_projection == []
            and snapshot["jobs"][1]["state"] == "pending"
        ), {"http_status": revised.status_code, "state": snapshot,
            "visible_before_projection": hidden_before_projection})

        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET state='running', "
                "lease_until=now()-interval '1 second' "
                "WHERE account_id=%s AND source_id=%s AND source_version=2",
                (account_id, note_id),
            )
        outcome = await run_once(dsn, adapter)
        snapshot = state(dsn, account_id, note_id)
        file_text = projected_file.read_text(encoding="utf-8") if projected_file.exists() else ""
        visible = current_notes_for_paths(dsn, account_id, [path])
        record("expired_lease_updates_real_reme", (
            outcome == "succeeded" and SECOND_TEXT in file_text
            and FIRST_TEXT not in file_text
            and "growth_source_version: 2" in file_text
            and snapshot["current"] is not None
            and snapshot["current"]["source_version"] == 2
            and snapshot["jobs"][1]["attempt_count"] == 1
            and len(visible) == 1 and visible[0]["content_text"] == SECOND_TEXT
        ), {"worker": outcome, "state": snapshot,
            "file_sha256": hashlib.sha256(file_text.encode()).hexdigest(),
            "visible_note_ids": [item["note_id"] for item in visible]})

        removed = await cloud.request("DELETE", f"/v1/notes/{note_id}", headers=headers,
                                      json={"operation_id": str(uuid.uuid4()),
                                            "base_revision": 2})
        hidden_after_delete = current_notes_for_paths(dsn, account_id, [path])
        snapshot = state(dsn, account_id, note_id)
        record("delete_hides_before_projection", (
            removed.status_code == 200 and snapshot["revision"] == 3
            and snapshot["note_deleted"] and hidden_after_delete == []
            and snapshot["jobs"][2]["state"] == "pending"
        ), {"http_status": removed.status_code, "state": snapshot})

        outcome = await run_once(dsn, adapter)
        indexed = await reme.post("/reindex", json={})
        indexed.raise_for_status()
        absent, paths = await wait_search(adapter, path, present=False)
        snapshot = state(dsn, account_id, note_id)
        record("real_reme_delete_removes_projection", (
            outcome == "succeeded" and not projected_file.exists() and absent
            and snapshot["current"] is not None
            and snapshot["current"]["source_version"] == 3
            and snapshot["current"]["deleted_at"] is not None
        ), {"worker": outcome, "indexed_absent": absent, "paths": paths,
            "state": snapshot})

        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET state='pending', next_retry_at=now() "
                "WHERE account_id=%s AND source_id=%s AND source_version=1",
                (account_id, note_id),
            )
        outcome = await run_once(dsn, adapter)
        snapshot = state(dsn, account_id, note_id)
        record("old_version_cannot_resurrect", (
            outcome == "skipped" and not projected_file.exists()
            and snapshot["jobs"][0]["state"] == "skipped"
            and snapshot["current"]["source_version"] == 3
        ), {"worker": outcome, "state": snapshot})

        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET state='pending', next_retry_at=now() "
                "WHERE account_id=%s AND source_id=%s AND source_version=3",
                (account_id, note_id),
            )
        outcome = await run_once(dsn, adapter)
        snapshot = state(dsn, account_id, note_id)
        record("duplicate_real_delete_is_idempotent", (
            outcome == "succeeded" and not projected_file.exists()
            and snapshot["jobs"][2]["state"] == "succeeded"
            and snapshot["jobs"][2]["attempt_count"] == 2
            and snapshot["current"]["source_version"] == 3
        ), {"worker": outcome, "state": snapshot})

    return {"status": "PASS", "checks": len(steps), "finished_at": now(),
            "final_state": state(dsn, account_id, note_id)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--reme-url", required=True)
    args = parser.parse_args()
    if Path(args.run_id).name != args.run_id:
        parser.error("run-id must be a single directory name")
    run_dir = ROOT / "acceptance/runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = asyncio.run(exercise(run_dir, args.dsn, args.reme_url))
    except Exception as exc:
        result = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}",
                  "finished_at": now()}
        save_json(run_dir / "lifecycle-result.json", result)
        raise
    save_json(run_dir / "lifecycle-result.json", result)
    print(f"{result['status']}: {result['checks']} real ReMe lifecycle checks")


if __name__ == "__main__":
    main()
