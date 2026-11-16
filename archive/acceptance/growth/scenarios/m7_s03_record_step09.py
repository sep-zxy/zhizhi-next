"""Audit and record live S03 N1 durability during a real ReMe outage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import socket
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import ReMeAdapter, run_once
from m7_s03_note_outage import N1


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def artifact(run: Path, name: str) -> dict[str, str]:
    path = run / "artifacts" / name
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


async def fail_jobs(dsn: str, reme_url: str) -> list[str | None]:
    async with httpx.AsyncClient(base_url=reme_url, timeout=2) as client:
        adapter = ReMeAdapter(client)
        outcomes: list[str | None] = []
        for _ in range(8):
            outcome = await run_once(dsn, adapter)
            outcomes.append(outcome)
            if outcome is None:
                break
        return outcomes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--reme-url", default="http://127.0.0.1:18775")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    result = read(run / "result.json")
    prep = read(run / "artifacts/s03-09-preparation.json")
    ui = read(run / "artifacts/s03-09-note-ui.json")
    outage = read(run / "artifacts/s03-09-reme-outage-process.json")
    if not result["checks"]["S03-08"] or "S03-09" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S03-08 未通过或 S03-09 已记录")
    if prep["candidate_sha256"] != manifest["candidate_sha256"] or prep["run_id"] != run.name or ui["mode"] != "live_observation" or ui["status"] != "observed_pending_audit":
        raise AssertionError("S03 N1 不属于当前正式候选")
    if datetime.fromisoformat(outage["stopped_at"]) >= datetime.fromisoformat(ui["started_at"]):
        raise AssertionError("ReMe 不是在写 N1 前停止")
    if outage["port"] != 18775 or reachable(18775) or not reachable(outage["cloud_port"]):
        raise AssertionError("ReMe 停止或成长云在线状态不符")
    config = (run / "fixture/repo/.ahadiff/config.toml").read_text(encoding="utf-8")
    if 'base_url = "https://api.deepseek.com"' not in config or 'model_name = "deepseek-flash"' not in config:
        raise AssertionError("模型 provider 配置被改变")
    if ui["note_text"] != N1 or ui["note_sha256"] != hashlib.sha256(N1.encode()).hexdigest() or ui["sync"]["account_id"] != prep["account_id"] or ui["sync"]["outbox"]["pending"] != 0 or ui["sync"]["outbox"]["conflict"] != 0:
        raise AssertionError("N1 安装版原文或业务同步回执不完整")
    account = uuid.UUID(prep["account_id"])
    note_id = uuid.UUID(ui["note_id"])
    task_id = uuid.UUID(prep["task_id"])
    with sqlite3.connect(run / "fixture/repo/.ahadiff/growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        local_note = db.execute("SELECT * FROM engineering_notes WHERE note_id=?", (str(note_id),)).fetchone()
        local_task = db.execute("SELECT module_id,module_index_revision FROM growth_tasks WHERE task_id=?", (str(task_id),)).fetchone()
    if local_note is None or local_note["content_text"] != N1 or local_note["revision"] != 1 or local_note["content_hash"] != ui["note_sha256"]:
        raise AssertionError("本机笔记原文/版本不一致")
    if local_task is None or local_task["module_id"] != prep["module_id"] or local_task["module_index_revision"] != prep["old_module_index_revision"]:
        raise AssertionError("旧模块卡片与 N1 关系不一致")
    with connect(args.dsn) as conn:
        cloud_note = conn.execute("SELECT task_id,revision,content_text,content_hash,author FROM growth_notes WHERE account_id=%s AND note_id=%s", (account, note_id)).fetchone()
        jobs_before = conn.execute("SELECT source_type,source_id,source_version,state,attempt_count FROM growth_memory_jobs WHERE account_id=%s ORDER BY source_type,source_id,source_version", (account,)).fetchall()
        attempts = conn.execute("SELECT attempt_id,status FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s ORDER BY created_at", (account, task_id)).fetchall()
        note_event = conn.execute("SELECT trace_id,occurred_at FROM growth_domain_events WHERE account_id=%s AND entity_type='note' AND entity_id=%s AND event_type='note_saved'", (account, note_id)).fetchone()
    if cloud_note is None or cloud_note["task_id"] != task_id or cloud_note["revision"] != 1 or cloud_note["content_text"] != N1 or cloud_note["content_hash"].strip() != ui["note_sha256"] or cloud_note["author"] != "user":
        raise AssertionError("ReMe 停机期间笔记没有独立上云")
    if len(attempts) != 2 or any(row["status"] != "feedback_ready" for row in attempts) or note_event is None:
        raise AssertionError("笔记领域事件或已完成回答丢失")
    outcomes = asyncio.run(fail_jobs(args.dsn, args.reme_url))
    with connect(args.dsn) as conn:
        jobs_after = conn.execute("SELECT source_type,source_id,source_version,state,attempt_count FROM growth_memory_jobs WHERE account_id=%s ORDER BY source_type,source_id,source_version", (account,)).fetchall()
        current = conn.execute("SELECT source_type,source_id FROM growth_memory_current WHERE account_id=%s", (account,)).fetchall()
        cloud_after = conn.execute("SELECT revision,content_text FROM growth_notes WHERE account_id=%s AND note_id=%s", (account, note_id)).fetchone()
    note_job = next((row for row in jobs_after if row["source_type"] == "note" and row["source_id"] == note_id), None)
    if len(jobs_before) != 5 or len(jobs_after) != 5 or note_job is None or note_job["source_version"] != 1 or note_job["state"] not in {"pending", "failed"}:
        raise AssertionError("ReMe 停服积压队列或笔记 job 不完整")
    if "failed" not in outcomes or current or cloud_after is None or cloud_after["revision"] != 1 or cloud_after["content_text"] != N1:
        raise AssertionError("真实 worker 未失败，或失败影响了业务笔记")
    if reachable(18775) or not reachable(outage["cloud_port"]):
        raise AssertionError("审计期间故障环境改变")
    audit = {"status": "PASS", "mode": "live_observation", "run_id": run.name,
             "candidate_sha256": manifest["candidate_sha256"], "account_id": prep["account_id"],
             "task_id": prep["task_id"], "note_id": ui["note_id"],
             "worker_outcomes": outcomes, "jobs_before": [dict(row) for row in jobs_before],
             "jobs_after": [dict(row) for row in jobs_after],
             "note_business_durable": True, "reme_port_down": True, "cloud_port_up": True}
    write(run / "artifacts/s03-09-outage-audit.json", audit)
    names = ["s03-09-preparation.json", "s03-09-reme-outage-process.json",
             "s03-09-note-ui.json", "s03-09-outage-audit.json"]
    step = {"schema_version": 1, "run_id": run.name, "case_id": "S03", "step_id": "S03-09",
            "status": "PASS", "started_at": outage["stopped_at"], "finished_at": ui["finished_at"],
            "device_id": manifest["environment"]["windows_node_id"],
            "trace_id": str(note_event["trace_id"]),
            "action": "停掉真实 ReMe 后在安装版手写 N1，并让真实 worker 在投影层失败",
            "expected": "笔记原文成功上云，ReMe job 待处理或失败，不影响 A1/A2 和笔记业务记录",
            "actual": {"note_id": ui["note_id"], "note_revision": 1,
                       "note_sha256": ui["note_sha256"], "worker_outcomes": outcomes,
                       "cloud_kind": "isolated_local_postgresql"},
            "artifacts": [artifact(run, name) for name in names]}
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"event_type": "note_saved", "entity_id": str(note_id),
                                 "trace_id": str(note_event["trace_id"]),
                                 "occurred_at": note_event["occurred_at"].isoformat()}, ensure_ascii=False) + "\n")
    chain = read(run / "chain.json")
    chain["note_id"] = ui["note_id"]
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\n## S03-09 真实 ReMe 停机与 N1 持久化\n\n"
                     "在 ReMe 进程停止、成长云运行时，安装版保存情境 N1 并取得本机隔离 PostgreSQL 回执。"
                     "真实 worker 处理积压任务时投影失败，笔记原文、版本和 A1/A2 反馈仍在业务库。"
                     "ReMe 恢复、macOS 拉取和新会话回源仍待后续步骤。\n")
    print(f"S03-09_PASS {ui['note_id']}")


if __name__ == "__main__":
    main()
