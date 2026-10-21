"""Audit the real ReMe cleanup after the Windows-only S04 delete rehearsal."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from ahadiff.growth.cloud.memory import ReMeAdapter, run_once


async def exercise(run: Path, dsn: str, credentials_path: Path, reme_url: str) -> dict:
    artifacts = run / "artifacts"
    created_path = artifacts / "s04-12-windows-temp-create.json"
    if not created_path.exists():
        created_path = artifacts / "delete-create.json"
    created = json.loads(created_path.read_text(
        encoding="utf-8"))
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    account = uuid.UUID(created["account_id"])
    note_id = uuid.UUID(created["temp_note_id"])
    if credentials["account_id"] != str(account):
        raise ValueError("云凭据与情境账号不一致")
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as local:
        identity = local.execute(
            "SELECT device_id FROM sync_accounts WHERE account_id=?", (str(account),)
        ).fetchone()
    if identity is None:
        raise ValueError("设备 A 没有同步身份")
    outcomes: list[str] = []
    async with httpx.AsyncClient(base_url=reme_url, timeout=25) as reme:
        adapter = ReMeAdapter(reme)
        for _ in range(20):
            outcome = await run_once(dsn, adapter)
            if outcome is None:
                break
            outcomes.append(outcome)
        reindex = await reme.post("/reindex", json={})
        reindex.raise_for_status()
        search_paths = await adapter.search_paths(
            "有了 JWT 就不需要资源权限检查", limit=20)
    with psycopg.connect(dsn, row_factory=dict_row) as cloud:
        jobs = cloud.execute(
            "SELECT source_version, action, state, attempt_count FROM growth_memory_jobs "
            "WHERE account_id=%s AND source_type='note' AND source_id=%s "
            "ORDER BY source_version", (account, note_id),
        ).fetchall()
        current = cloud.execute(
            "SELECT source_version, deleted_at, reme_path FROM growth_memory_current "
            "WHERE account_id=%s AND source_type='note' AND source_id=%s",
            (account, note_id),
        ).fetchone()
    headers = {"Authorization": f"Bearer {credentials['access_token']}",
               "X-Device-Id": identity[0]}
    cloud_url = credentials.get("cloud_url") or f"http://127.0.0.1:{credentials['port']}"
    async with httpx.AsyncClient(base_url=cloud_url, timeout=25) as client:
        response = await client.get(
            "/v1/growth/context",
            params={"query": "我现在怎样区分登录和权限？", "limit": 20},
            headers=headers,
        )
    context = response.json()
    context_note_ids = [item.get("note_id") for item in context.get("notes", [])]
    checks = {
        "prior_upsert_processed": any(row["source_version"] == 1
                                      and row["action"] == "upsert"
                                      and row["state"] in {"skipped", "succeeded"}
                                      for row in jobs),
        "delete_job_succeeded": any(row["source_version"] == 2
                                    and row["action"] == "delete"
                                    and row["state"] == "succeeded" for row in jobs),
        "current_revision_is_deleted": current is not None
        and current["source_version"] == 2 and current["deleted_at"] is not None,
        "reme_search_excludes_deleted": not any(str(note_id) in path for path in search_paths),
        "context_excludes_deleted": response.status_code == 200
        and str(note_id) not in context_note_ids
        and created["temp_note_text"] not in json.dumps(context, ensure_ascii=False),
        "context_keeps_valid_note": any(
            "我以前把" in item.get("content_text", "") for item in context.get("notes", [])),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "windows_local_cloud_real_reme_rehearsal_not_formal",
        "account_id": str(account), "temp_note_id": str(note_id),
        "worker_outcomes": outcomes, "jobs": jobs, "current": current,
        "reme_search_paths": search_paths, "context_note_ids": context_note_ids,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--reme-url", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    result = asyncio.run(exercise(run, args.dsn, args.credentials, args.reme_url))
    target = run / "artifacts" / "s04-14-windows-reme-delete-rehearsal.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                      encoding="utf-8")
    if result["status"] != "PASS":
        raise AssertionError(f"ReMe 删除回源断言失败：{result['checks']}")
    print(f"S04_DELETED_MEMORY_REHEARSAL_PASS {result['temp_note_id']}")


if __name__ == "__main__":
    main()
