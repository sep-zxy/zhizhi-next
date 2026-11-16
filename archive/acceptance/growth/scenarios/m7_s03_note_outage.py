"""Audit S03 N1 cloud durability while the real ReMe process is stopped."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import socket
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import ReMeAdapter, run_once


N1 = (
    "订单计价现在先经过 PriceCache，再按需要调用 PricingGateway。"
    "这个例子的 TTL 是访问时检查的：到期不会自动刷新，下一次 get 才重新加载。"
    "我不能只看到 expiresAt 就推断项目里存在定时清理线程。"
    "另外，这个 HashMap 实现是否适合并发使用，需要单独检查。"
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


async def try_worker(dsn: str, reme_url: str) -> list[str | None]:
    async with httpx.AsyncClient(base_url=reme_url, timeout=2) as client:
        adapter = ReMeAdapter(client)
        outcomes = []
        for _ in range(8):
            outcome = await run_once(dsn, adapter)
            outcomes.append(outcome)
            if outcome is None:
                break
        return outcomes


def reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--reme-url", default="http://127.0.0.1:18775")
    parser.add_argument("--cloud-port", type=int, required=True)
    parser.add_argument("--model-port", type=int, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    prep = load(run / "preparation.json")
    ui = load(run / "artifacts" / "note-ui.json")
    outage = load(run / "reme-outage-process.json")
    source_audit = load(run.parent / prep["source_run_id"] / "audit.json")
    account = uuid.UUID(prep["account_id"])
    task_id = uuid.UUID(prep["task_id"])
    note_id = uuid.UUID(ui["note_id"])
    with sqlite3.connect(Path(prep["workspace"]) / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        local = db.execute(
            "SELECT * FROM engineering_notes WHERE note_id=?", (str(note_id),)
        ).fetchone()
        task = db.execute(
            "SELECT module_id,module_index_revision FROM growth_tasks WHERE task_id=?",
            (str(task_id),),
        ).fetchone()
    with connect(args.dsn) as conn:
        cloud = conn.execute(
            "SELECT task_id,revision,content_text,content_hash,author FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account, note_id),
        ).fetchone()
        jobs_before = conn.execute(
            "SELECT source_type,source_id,source_version,state,attempt_count "
            "FROM growth_memory_jobs WHERE account_id=%s ORDER BY source_type,source_id,source_version",
            (account,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT attempt_id,status FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s ORDER BY created_at",
            (account, task_id),
        ).fetchall()
    reme_port = urlparse(args.reme_url).port
    if reme_port is None:
        raise ValueError("ReMe URL 缺少端口")
    reme_down = not reachable(reme_port)
    outcomes = asyncio.run(try_worker(args.dsn, args.reme_url)) if reme_down else []
    with connect(args.dsn) as conn:
        jobs_after = conn.execute(
            "SELECT source_type,source_id,source_version,state,attempt_count "
            "FROM growth_memory_jobs WHERE account_id=%s ORDER BY source_type,source_id,source_version",
            (account,),
        ).fetchall()
        current = conn.execute(
            "SELECT source_type,source_id FROM growth_memory_current WHERE account_id=%s",
            (account,),
        ).fetchall()
        cloud_after = conn.execute(
            "SELECT revision,content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, note_id),
        ).fetchone()
    note_job = next((row for row in jobs_after if row["source_type"] == "note"
                     and row["source_id"] == note_id), None)
    checks = {
        "same_candidate_rebase_source": source_audit["status"] == "PASS"
        and not source_audit["cross_candidate"]
        and source_audit["candidate_sha256"] == prep["candidate_sha256"],
        "installed_ui_exact_n1": ui["status"] == "observed_pending_audit"
        and ui["note_text"] == N1
        and ui["note_sha256"] == hashlib.sha256(N1.encode()).hexdigest(),
        "local_note_and_old_card": local is not None and task is not None
        and local["content_text"] == N1 and local["revision"] == 1
        and local["content_hash"] == ui["note_sha256"]
        and task["module_id"] == prep["module_id"]
        and task["module_index_revision"] == prep["old_module_index_revision"],
        "cloud_note_same_id_and_revision": cloud is not None
        and cloud["task_id"] == task_id and cloud["revision"] == 1
        and cloud["content_text"] == N1
        and cloud["content_hash"].strip() == ui["note_sha256"]
        and cloud["author"] == "user",
        "cloud_and_model_available": ui["sync"]["outbox"]["pending"] == 0
        and ui["sync"]["outbox"]["conflict"] == 0
        and reachable(args.cloud_port) and reachable(args.model_port),
        "real_reme_process_stopped": outage["port"] == reme_port and reme_down,
        "memory_job_queued_or_failed": len(jobs_before) == len(jobs_after) == 5
        and note_job is not None and note_job["source_version"] == 1
        and note_job["state"] in {"pending", "failed"}
        and any(outcome == "failed" for outcome in outcomes)
        and not current,
        "business_survives_failed_worker": cloud_after is not None
        and cloud_after["revision"] == 1 and cloud_after["content_text"] == N1
        and len(attempts) == 2
        and all(row["status"] == "feedback_ready" for row in attempts),
    }
    result = {
        "run_id": run.name, "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "replay_not_live", "stage_status": "BLOCKED",
        "scope": "S03 N1 安装版原文、云端持久化、真实 ReMe 停止与投影失败",
        "candidate_sha256": prep["candidate_sha256"],
        "account_id": prep["account_id"], "task_id": prep["task_id"],
        "note_id": str(note_id), "worker_outcomes": outcomes,
        "jobs_before": [dict(row) for row in jobs_before],
        "jobs_after": [dict(row) for row in jobs_after],
        "checks": checks,
    }
    (run / "outage-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "checks": checks}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
