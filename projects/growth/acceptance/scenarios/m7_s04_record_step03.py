"""Record the installed S04 topic confirmation, rename, and idempotent retry."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from m7_s04_record_step02 import artifact, read, write

from ahadiff.growth.cloud.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    chain = read(run / "chain.json")
    driver_path = run / "artifacts" / "s04-03-confirm-live-ui.json"
    audit_path = run / "artifacts" / "s04-03-cloud-audit.json"
    driver, audit = read(driver_path), read(audit_path)
    chat = read(run / "artifacts" / "s04-02-chat-live-ui.json")
    steps = [json.loads(line) for line in (run / "steps.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != digest:
        raise AssertionError("安装包与 S04 候选不一致")
    if [item["step_id"] for item in steps] != ["S04-01", "S04-02"] \
            or any(item["status"] != "PASS" for item in steps):
        raise AssertionError("S04-01/02 必须先通过，且不得重复记录 S04-03")
    if driver["mode"] != "live_observation" or audit["mode"] != "live_observation" \
            or audit["status"] != "PASS" or not all(audit["checks"].values()):
        raise AssertionError("确认、重命名与数据库复核未通过")
    if Path(driver["workspace"]).resolve() != (run / "fixture" / "repo").resolve() \
            or driver["session_id"] != chain["chat_session_id"] \
            or driver["suggestion_id"] != chain["pending_suggestion_id"] \
            or driver["account_id"] != chat["account_id"]:
        raise AssertionError("确认动作没有延续 S04-02 的账号、会话与建议")
    if driver["suggestion_id"] != chat["turns"][1]["assistant_receipt"][
            "suggestion"]["suggestion_id"]:
        raise AssertionError("确认的不是第二轮模型建议")
    account = uuid.UUID(driver["account_id"])
    topic_id = uuid.UUID(driver["first_receipt"]["topic_id"])
    suggestion_id = uuid.UUID(driver["suggestion_id"])
    operation_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"growth-chat-decision:{account}:{suggestion_id}:confirm",
    )
    with connect(args.dsn) as conn:
        topic = conn.execute(
            "SELECT title, revision, status FROM growth_topics "
            "WHERE account_id=%s AND topic_id=%s",
            (account, topic_id),
        ).fetchone()
        old = conn.execute(
            "SELECT title, revision FROM growth_topics "
            "WHERE account_id=%s AND topic_id=%s",
            (account, uuid.UUID(chain["topic_id"])),
        ).fetchone()
        count = conn.execute(
            "SELECT count(*) AS total FROM growth_topics WHERE account_id=%s "
            "AND deleted_at IS NULL", (account,),
        ).fetchone()["total"]
        suggestion = conn.execute(
            "SELECT status, topic_id FROM growth_chat_suggestions "
            "WHERE account_id=%s AND suggestion_id=%s",
            (account, suggestion_id),
        ).fetchone()
        events = conn.execute(
            "SELECT event_id, trace_id, entity_type, entity_id, event_type, "
            "payload, occurred_at FROM growth_domain_events "
            "WHERE account_id=%s AND entity_id IN (%s, %s) "
            "AND event_type IN ('chat_topic_confirmed', 'topic_user_created', "
            "'topic_user_updated') ORDER BY occurred_at",
            (account, suggestion_id, topic_id),
        ).fetchall()
        operation = conn.execute(
            "SELECT status_code, response FROM growth_sync_operations "
            "WHERE account_id=%s AND operation_id=%s",
            (account, operation_id),
        ).fetchone()
    checks = {
        "user_title_and_one_topic": count == 2 and topic is not None
        and topic["title"] == "Web 认证与授权" and topic["revision"] == 2
        and topic["status"] == "active",
        "old_topic_unchanged": old is not None and old["title"] == "登录机制"
        and old["revision"] == 1,
        "suggestion_confirmed_once": suggestion is not None
        and suggestion["status"] == "confirmed" and suggestion["topic_id"] == topic_id
        and len(events) == 3
        and {item["event_type"] for item in events} == {
            "chat_topic_confirmed", "topic_user_created", "topic_user_updated",
        },
        "same_idempotent_retry": driver["first_http_status"] == 200
        and driver["retry_http_status"] == 200
        and driver["retry_receipt"]["topic_id"] == str(topic_id)
        and operation is not None and operation["status_code"] == 200
        and operation["response"]["topic_id"] == str(topic_id),
        "installed_ui_after_restart": len(driver["topics_after_restart"]) == 2
        and any(item["id"] == str(topic_id)
                and item["title"].startswith("Web 认证与授权 · r2")
                for item in driver["topics_after_restart"]),
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    record = {
        "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S04",
        "step_id": "S04-03", "status": "PASS",
        "started_at": driver["started_at"], "finished_at": driver["finished_at"],
        "device_id": steps[0]["device_id"],
        "trace_id": str(events[0]["trace_id"]),
        "related_trace_ids": [str(item["trace_id"]) for item in events[1:]],
        "action": "安装版确认第二轮建议，重命名为 Web 认证与授权，重启并重试确认",
        "expected": "T-new 只创建一次；旧主题保留；确认重试返回原 topic_id",
        "actual": {"cloud_kind": "isolated_local_postgresql",
                   "suggestion_id": str(suggestion_id), "topic_id": str(topic_id),
                   "title": topic["title"], "revision": topic["revision"],
                   "decision_operation_id": str(operation_id), "checks": checks},
        "artifacts": [artifact(run, path) for path in (driver_path, audit_path)],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(dict(event), ensure_ascii=False, default=str) + "\n")
    chain["new_topic_id"] = str(topic_id)
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n## S04-03 Windows 安装版主题确认\n\n"
                     "第二轮真实模型建议经用户界面确认，只创建一个 T-new；"
                     "用户将标题改为“Web 认证与授权”。重启后两主题仍在，"
                     "同一确认操作重试返回原 topic_id。"
                     "数据库事件与操作回执见 `artifacts/s04-03-*`。\n")
    print(f"S04-03_PASS {topic_id}")


if __name__ == "__main__":
    main()
