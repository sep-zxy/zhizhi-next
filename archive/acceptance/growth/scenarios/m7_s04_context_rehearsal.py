"""Project S04 user evidence to real ReMe and read scoped cloud context."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from ahadiff.growth.cloud.memory import ReMeAdapter, run_once


async def exercise(run: Path, dsn: str, credentials_path: Path, reme_url: str,
                   *, formal_source: bool = False) -> dict:
    if formal_source:
        chain = json.loads((run / "chain.json").read_text(encoding="utf-8"))
        finish = run / "artifacts" / "s04-07-answer-note-finish-ui.json"
        if not finish.exists():
            finish = run / "artifacts" / "s04-07-answer-note-live-ui.json"
        ui = json.loads(finish.read_text(encoding="utf-8"))
        prepared = {"account_id": ui["account_id"],
                    "workspace": str(run / "fixture" / "repo"),
                    "new_topic_id": chain["new_topic_id"]}
    else:
        prepared = json.loads((run / "preparation.json").read_text(encoding="utf-8"))
        ui = json.loads((run / "artifacts" / "answer-note-ui.json").read_text(
            encoding="utf-8"))
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    account = uuid.UUID(prepared["account_id"])
    note_id = uuid.UUID(ui["note_id"])
    attempt_id = uuid.UUID(ui["attempt_id"])
    if credentials["account_id"] != str(account):
        raise ValueError("ReMe 回源使用的云账号与 S04 工作区不一致")
    with sqlite3.connect(Path(prepared["workspace"]) / ".ahadiff" / "growth.sqlite") as db:
        device_row = db.execute(
            "SELECT device_id FROM sync_accounts WHERE account_id=?", (str(account),)
        ).fetchone()
    if device_row is None:
        raise ValueError("S04 目标设备尚未同步")
    outcomes: list[str] = []
    async with httpx.AsyncClient(base_url=reme_url, timeout=25) as reme:
        version = await reme.post("/version", json={})
        version.raise_for_status()
        if version.json().get("answer") != "0.4.1.12":
            raise AssertionError("本机 ReMe 版本不符")
        adapter = ReMeAdapter(reme)
        for _ in range(12):
            outcome = await run_once(dsn, adapter)
            if outcome is None:
                break
            outcomes.append(outcome)
        reindex = await reme.post("/reindex", json={})
        reindex.raise_for_status()
        paths: list[str] = []
        started = time.monotonic()
        for _ in range(30):
            paths = await adapter.search_paths("Bob 文档所有者 授权", limit=20)
            if any(str(note_id) in item for item in paths):
                break
            await asyncio.sleep(1)
    with psycopg.connect(dsn, row_factory=dict_row) as cloud:
        jobs = cloud.execute(
            "SELECT source_type, source_id, source_version, state, attempt_count "
            "FROM growth_memory_jobs WHERE account_id=%s ORDER BY source_type, source_id, "
            "source_version", (account,),
        ).fetchall()
        currents = cloud.execute(
            "SELECT source_type, source_id, source_version, reme_path "
            "FROM growth_memory_current WHERE account_id=%s ORDER BY source_type, source_id",
            (account,),
        ).fetchall()
    headers = {"Authorization": f"Bearer {credentials['access_token']}",
               "X-Device-Id": device_row[0]}
    query = "Bob 文档所有者 授权"
    cloud_url = credentials.get("cloud_url") or f"http://127.0.0.1:{credentials['port']}"
    async with httpx.AsyncClient(base_url=cloud_url, timeout=25) as client:
        context_response = await client.get(
            "/v1/growth/context", params={"query": query, "limit": 20}, headers=headers,
        )
        context = context_response.json()
    current_keys = {(row["source_type"], str(row["source_id"])) for row in currents}
    note_results = context.get("notes", [])
    attempt_results = context.get("attempts", [])
    checks = {
        "real_reme_v04112": version.status_code == 200,
        "projection_jobs_completed": (
            all(row["state"] in {"succeeded", "skipped"} for row in jobs)
            and ("note", str(note_id)) in current_keys
            and ("attempt", str(attempt_id)) in current_keys
        ),
        "reme_search_finds_current_note": any(str(note_id) in item for item in paths),
        "context_api_scoped_current": (
            context_response.status_code == 200
            and context.get("memory_status") == "current"
            and any(item.get("note_id") == str(note_id) for item in note_results)
            and any(item.get("attempt_id") == str(attempt_id) for item in attempt_results)
        ),
        "context_contains_confirmed_topic": (
            any(item.get("topic_id") == prepared.get("new_topic_id", ui["topic_id"])
                for item in context.get("topics", []))
        ),
        "context_omits_secrets_and_full_code": (
            credentials["access_token"] not in json.dumps(context, ensure_ascii=False)
            and "function canEdit" not in json.dumps(context, ensure_ascii=False)
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "installed_windows_real_reme_local_cloud_rehearsal",
        "account_id": str(account), "note_id": str(note_id),
        "attempt_id": str(attempt_id), "worker_outcomes": outcomes,
        "reindex_status": reindex.status_code,
        "search_wait_seconds": round(time.monotonic() - started, 3),
        "search_paths": paths, "jobs": jobs, "current": currents,
        "context_status": context_response.status_code, "context": context,
        "checks": checks,
    }
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--reme-url", required=True)
    parser.add_argument("--formal-source", action="store_true",
                        help="从正式运行读取 S04-07 的真实本机证据，仅做 Windows 子链预演")
    args = parser.parse_args()
    run = args.run.resolve()
    result = asyncio.run(exercise(run, args.dsn, args.credentials, args.reme_url,
                                  formal_source=args.formal_source))
    target = run / "artifacts" / ("s04-08-windows-reme-context-rehearsal.json"
                                 if args.formal_source else "context-reme-audit.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                      encoding="utf-8")
    if result["status"] != "PASS":
        raise AssertionError(f"S04 ReMe 上下文审计失败：{result['checks']}")
    print(f"S04_CONTEXT_REME_PASS {result['note_id']} {result['attempt_id']}")


if __name__ == "__main__":
    main()
