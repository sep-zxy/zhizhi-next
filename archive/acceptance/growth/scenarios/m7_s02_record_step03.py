"""Audit the paused A0 draft in installed UI, SQLite, and isolated PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


A0 = "最后应该显示 ab 的结果，因为返回时还要检查请求编号。"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def artifact(run: Path, name: str) -> dict[str, str]:
    path = run / "artifacts" / name
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    result = read(run / "result.json")
    publish = read(run / "artifacts/s02-03-publish-ui.json")
    ui = read(run / "artifacts/s02-03-draft-pause-ui.json")
    card = read(run / "artifacts/s02-02-card-ui.json")
    if not result["checks"]["S02-02"] or "S02-03" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S02-02 未通过或 S02-03 已记录")
    if publish["status"] != ui["status"] != "observed" or publish["task_id"] != ui["task_id"] != card["task_id"]:
        raise AssertionError("安装版同步与 A0 不是同一卡片")
    if ui["draft_text"] != A0 or ui["paused_card"]["status"] != "paused" or ui["final_sync"]["outbox"]["pending"] != 0:
        raise AssertionError("A0、暂停或同步归零状态不符")
    if publish["account_id"] != ui["account_id"] or publish["sync"]["account_id"] != ui["account_id"]:
        raise AssertionError("本机隔离云账号切换")
    account_id = uuid.UUID(ui["account_id"])
    task_id = uuid.UUID(ui["task_id"])
    operation_id = uuid.UUID(ui["draft_receipt"]["operation_id"])
    with sqlite3.connect(run / "fixture/repo/.ahadiff/growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        local_task = db.execute("SELECT task_id,progress FROM growth_tasks WHERE task_id=?",
                                (str(task_id),)).fetchone()
        local_draft = db.execute("SELECT * FROM sync_task_drafts WHERE account_id=? AND task_id=?",
                                 (str(account_id), str(task_id))).fetchone()
        outbox = db.execute("SELECT * FROM sync_outbox WHERE account_id=? AND operation_id=?",
                            (str(account_id), str(operation_id))).fetchone()
        local_attempts = db.execute("SELECT count(*) FROM learning_attempts WHERE task_id=?",
                                    (str(task_id),)).fetchone()[0]
        pending_attempts = db.execute("SELECT count(*) FROM sync_pending_attempts WHERE task_id=?",
                                      (str(task_id),)).fetchone()[0]
        events = [dict(row) for row in db.execute(
            "SELECT * FROM growth_events WHERE entity_id=? ORDER BY created_at,event_id",
            (str(task_id),))]
    with connect(args.dsn) as cloud:
        task = cloud.execute(
            "SELECT task_id,progress,revision,opportunity_id,topic_id FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id)).fetchone()
        draft = cloud.execute(
            "SELECT revision,content_text,submitted_attempt_id FROM growth_task_drafts "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id)).fetchone()
        attempts = cloud.execute(
            "SELECT count(*) AS total FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id)).fetchone()["total"]
        operation = cloud.execute(
            "SELECT status_code,response FROM growth_sync_operations "
            "WHERE account_id=%s AND operation_id=%s", (account_id, operation_id)).fetchone()
        feed = cloud.execute(
            "SELECT entity_type,revision,payload FROM growth_change_feed "
            "WHERE account_id=%s AND entity_id=%s ORDER BY change_seq",
            (account_id, task_id)).fetchall()
    checks = {
        "one_published_card": task is not None and str(task["task_id"]) == ui["task_id"]
        and str(task["opportunity_id"]) == card["opportunity_id"]
        and str(task["topic_id"]) == card["topic_id"],
        "cloud_paused": task is not None and task["progress"] == "paused"
        and task["revision"] >= 3,
        "cloud_a0_draft_revision_one": draft is not None and draft["revision"] == 1
        and draft["content_text"] == A0 and draft["submitted_attempt_id"] is None,
        "no_submitted_answer": attempts == 0 and local_attempts == 0 and pending_attempts == 0
        and ui["synced_task"]["remote_draft"]["submitted_attempt_id"] is None,
        "local_a0_durable": local_draft is not None and local_draft["content_text"] == A0
        and local_draft["state"] == "synced" and local_draft["latest_operation_id"] == str(operation_id)
        and local_draft["submitted_attempt_id"] is None,
        "draft_operation_acknowledged": outbox is not None and outbox["state"] == "acked"
        and outbox["path"] == f"/v1/tasks/{task_id}/draft"
        and operation is not None and operation["status_code"] == 200,
        "local_paused": local_task is not None and local_task["progress"] == "paused"
        and ui["local_chain"]["task"]["progress"] == "paused",
        "cloud_feed_keeps_draft_and_progress": {row["entity_type"] for row in feed}
        >= {"task", "task_draft"},
        "same_isolated_account": publish["cloud_origin"] == ui["cloud_origin"]
        and publish["account_id"] == ui["account_id"],
    }
    audit = {
        "status": "PASS" if all(checks.values()) else "FAIL", "mode": "live_observation",
        "cloud_kind": "isolated_local_postgresql", "account_id": str(account_id),
        "task_id": str(task_id), "draft_operation_id": str(operation_id),
        "cloud_task_revision": task["revision"] if task else None,
        "cloud_draft_revision": draft["revision"] if draft else None,
        "cloud_feed_types": [row["entity_type"] for row in feed], "checks": checks,
    }
    write(run / "artifacts/s02-03-sync-audit.json", audit)
    if audit["status"] != "PASS":
        raise AssertionError(checks)
    names = ["s02-03-publish-ui.json", "s02-03-draft-pause-ui.json",
             "s02-03-sync-audit.json"]
    step = {
        "schema_version": 1, "run_id": run.name, "case_id": "S02", "step_id": "S02-03",
        "status": "PASS", "started_at": publish["started_at"],
        "finished_at": ui["finished_at"],
        "device_id": read(run / "manifest.json")["environment"]["windows_node_id"],
        "trace_id": next((row["trace_id"] for row in events
                          if row["event_type"] == "task_progress_changed"
                          and 'paused' in row["payload_json"]), None),
        "related_trace_ids": [row["trace_id"] for row in events
                              if row["event_type"] == "task_progress_changed"],
        "action": "Windows 安装版输入 A0 草稿、暂停卡片、等待隔离云同步并退出",
        "expected": "云端草稿修订 1 与暂停进度持久化，A0 未产生正式回答",
        "actual": {"task_id": str(task_id), "draft_revision": draft["revision"],
                   "task_revision": task["revision"], "progress": task["progress"],
                   "answer_count": attempts, "cloud_kind": "isolated_local_postgresql"},
        "artifacts": [artifact(run, name) for name in names],
    }
    if step["trace_id"] is None:
        raise AssertionError("暂停事件 trace_id 缺失")
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in events:
            if event["event_type"] == "task_progress_changed":
                output.write(json.dumps(event, ensure_ascii=False) + "\n")
    chain = read(run / "chain.json")
    chain.update({"account_id": str(account_id), "draft_operation_id": str(operation_id)})
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\n## S02-03 Windows A0 草稿与暂停\n\n"
                     "同一安装版先向隔离本机 PostgreSQL 发布项目与真实卡片；随后开始卡片，"
                     "在同步任务编辑器保存 A0 草稿，点击暂停，等待同步并退出。SQLite 与 PostgreSQL "
                     "同账号同任务显示草稿 revision 1、进度 paused、正式回答 0 条。"
                     "macOS 续学和外部云仍待真实环境验收。\n")
    print(f"S02-03_PASS {task_id}")


if __name__ == "__main__":
    main()
