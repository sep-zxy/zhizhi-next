"""Audit one installed S04 replay chain across SQLite, PostgreSQL, and provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read(run / "preparation.json")
    chat = read(run / "audit.json")
    confirmed = read(run / "artifacts" / "confirm-audit.json")
    code = read(run / "artifacts" / "code-result.json")
    event = read(run / "artifacts" / "event-audit.json")
    ui = read(run / "artifacts" / "reuse-ui.json")
    provider = read(run / "artifacts" / "opportunity-provider-requests.json")
    account = uuid.UUID(prepared["account_id"])
    topic = uuid.UUID(confirmed["topic_id"])
    proposal = uuid.UUID(ui["proposal_id"])
    task = uuid.UUID(ui["task_id"])
    workspace = Path(prepared["workspace"])
    with sqlite3.connect(workspace / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        local_topic = db.execute(
            "SELECT topic_id, title FROM growth_topics WHERE topic_id=?", (str(topic),)
        ).fetchone()
        local_proposal = db.execute(
            "SELECT topic_id, status FROM topic_proposals WHERE proposal_id=?",
            (str(proposal),),
        ).fetchone()
        local_task = db.execute(
            "SELECT topic_id, opportunity_id, question FROM growth_tasks WHERE task_id=?",
            (str(task),),
        ).fetchone()
        local_event = db.execute(
            "SELECT payload_json FROM growth_events WHERE entity_type='topic_proposal' "
            "AND entity_id=? AND event_type='topic_confirmed'", (str(proposal),),
        ).fetchone()
        cached_topic = db.execute(
            "SELECT payload_json FROM sync_entities WHERE account_id=? "
            "AND entity_type='topic' AND entity_id=?",
            (str(account), str(topic)),
        ).fetchone()
        pending = db.execute(
            "SELECT count(*) AS n FROM sync_outbox WHERE account_id=? AND state='pending'",
            (str(account),),
        ).fetchone()["n"]
    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        topics = cloud.execute(
            "SELECT topic_id, title FROM growth_topics WHERE account_id=%s "
            "AND deleted_at IS NULL", (account,),
        ).fetchall()
        cloud_proposal = cloud.execute(
            "SELECT topic_id, status FROM growth_topic_proposals "
            "WHERE account_id=%s AND proposal_id=%s", (account, proposal),
        ).fetchone()
        cloud_task = cloud.execute(
            "SELECT topic_id, opportunity_id, question FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s", (account, task),
        ).fetchone()
        cloud_proposal_event = cloud.execute(
            "SELECT payload FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='topic_proposal' AND entity_id=%s "
            "AND event_type='topic_confirmed'", (account, proposal),
        ).fetchone()
        topic_events = cloud.execute(
            "SELECT event_type FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='topic' AND entity_id=%s", (account, topic),
        ).fetchall()
        source = cloud.execute(
            "SELECT relative_path FROM growth_source_refs WHERE account_id=%s "
            "AND source_ref_id=%s", (account, uuid.UUID(provider[0]["source_ref_id"])),
        ).fetchone()
        old_note = cloud.execute(
            "SELECT content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(prepared["old_note_id"])),
        ).fetchone()
    checks = {
        "same_installed_candidate": (
            chat["status"] == confirmed["status"] == event["status"] == "PASS"
            and prepared["candidate_sha256"] == chat["candidate_sha256"]
            and ui["executable_sha256"] == read(run / "artifacts" / "chat-ui.json")
            ["executable_sha256"]
        ),
        "real_code_event_snapshot": (
            code["status"] == "PASS" and ui["snapshot_id"] == event["snapshot_id"]
            and source is not None and source["relative_path"] == "src/access.ts"
        ),
        "approved_source_reached_provider": (
            len(provider) == 1 and provider[0]["prompt_sha256"]
            == ui["preview"]["payload_sha256"]
            and provider[0]["source_ref_id"] in ui["preview"]["source_ref_ids"]
        ),
        "new_topic_creation_disabled": ui["new_topic_confirm_disabled"] is True,
        "one_chat_topic_not_duplicated": (
            {str(row["topic_id"]) for row in topics}
            == {prepared["old_topic_id"], str(topic)}
            and len(topic_events) == 1
        ),
        "local_proposal_reuses_topic": (
            local_topic is not None and local_topic["topic_id"] == str(topic)
            and local_proposal is not None and local_proposal["topic_id"] == str(topic)
            and local_proposal["status"] == "confirmed"
            and json.loads(local_event["payload_json"])["reuse_existing"] is True
        ),
        "cloud_proposal_reuses_topic": (
            cloud_proposal is not None and cloud_proposal["topic_id"] == topic
            and cloud_proposal["status"] == "confirmed"
            and cloud_proposal_event["payload"]["reuse_existing"] is True
        ),
        "same_card_topic_and_opportunity": (
            local_task is not None and cloud_task is not None
            and local_task["topic_id"] == str(topic)
            and cloud_task["topic_id"] == topic
            and local_task["opportunity_id"] == ui["opportunity_id"]
            and str(cloud_task["opportunity_id"]) == ui["opportunity_id"]
            and local_task["question"] == cloud_task["question"]
            and "Bob" in cloud_task["question"]
        ),
        "incremental_topic_and_history_survive": (
            cached_topic is not None
            and json.loads(cached_topic["payload_json"])["title"] == "Web 认证与授权"
            and old_note is not None
            and old_note["content_text"] == "登录成功说明应用识别了用户身份。"
            and pending == 0 and ui["final_sync"]["queued"] == 0
        ),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    result = {
        "run_id": run.name, "status": status, "mode": "replay_not_live",
        "candidate_sha256": prepared["candidate_sha256"],
        "account_id": str(account), "topic_id": str(topic),
        "proposal_id": str(proposal), "task_id": str(task), "checks": checks,
        "provider_prompt_sha256": provider[0]["prompt_sha256"],
        "old_note_sha256": hashlib.sha256(old_note["content_text"].encode()).hexdigest()
        if old_note else None,
    }
    target = run / "artifacts" / "reuse-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    if status != "PASS":
        raise AssertionError(f"S04 主题复用安装版审计失败：{checks}")
    print(f"S04_REUSE_AUDIT_PASS {topic} {task}")


if __name__ == "__main__":
    main()
