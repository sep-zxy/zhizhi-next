"""Audit installed S04 answer, fixed feedback, and hand-written note."""

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
    ui = read(run / "artifacts" / "answer-note-ui.json")
    reuse = read(run / "artifacts" / "reuse-audit.json")
    provider = read(run / "artifacts" / "feedback-provider-requests.json")
    account = uuid.UUID(ui["account_id"])
    attempt = uuid.UUID(ui["attempt_id"])
    note_id = uuid.UUID(ui["note_id"])
    task_id = uuid.UUID(ui["task_id"])
    topic_id = uuid.UUID(ui["topic_id"])
    with sqlite3.connect(Path(ui["workspace"]) / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        local_attempt = db.execute(
            "SELECT answer_text, status, feedback_json, actor_origin FROM learning_attempts "
            "WHERE attempt_id=?", (str(attempt),),
        ).fetchone()
        local_note = db.execute(
            "SELECT task_id, topic_id, content_text, author, source_refs_json "
            "FROM engineering_notes WHERE note_id=?", (str(note_id),),
        ).fetchone()
        local_task = db.execute(
            "SELECT progress FROM growth_tasks WHERE task_id=?", (str(task_id),),
        ).fetchone()
        pending = db.execute(
            "SELECT count(*) AS n FROM sync_outbox WHERE account_id=? AND state='pending'",
            (str(account),),
        ).fetchone()["n"]
    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        cloud_attempt = cloud.execute(
            "SELECT answer_text, status, feedback, feedback_origin, actor_origin "
            "FROM growth_learning_attempts WHERE account_id=%s AND attempt_id=%s",
            (account, attempt),
        ).fetchone()
        cloud_note = cloud.execute(
            "SELECT task_id, topic_id, content_text, author, source_refs "
            "FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, note_id),
        ).fetchone()
        cloud_task = cloud.execute(
            "SELECT progress FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, task_id),
        ).fetchone()
        old_note = cloud.execute(
            "SELECT content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(read(run / "preparation.json")["old_note_id"])),
        ).fetchone()
    checks = {
        "same_installed_chain": (
            reuse["status"] == "PASS" and reuse["task_id"] == ui["task_id"]
            and reuse["topic_id"] == ui["topic_id"]
        ),
        "exact_user_answer": (
            local_attempt is not None and cloud_attempt is not None
            and local_attempt["answer_text"] == cloud_attempt["answer_text"] == ui["answer_text"]
            and local_attempt["actor_origin"] == cloud_attempt["actor_origin"] == "user"
        ),
        "approved_feedback_request": (
            len(provider) == 1
            and provider[0]["prompt_sha256"] == ui["preview"]["payload_sha256"]
            and provider[0]["source_ref_ids"] == ui["source_refs"]
        ),
        "feedback_grounded_and_separate": (
            local_attempt["status"] == cloud_attempt["status"] == "feedback_ready"
            and cloud_attempt["feedback_origin"] == "live"
            and "身份认证" in cloud_attempt["feedback"]["summary"]
            and cloud_attempt["feedback"]["user_claims"] == [ui["answer_text"]]
        ),
        "card_completed": (
            local_task["progress"] == cloud_task["progress"] == "completed"
        ),
        "note_empty_then_user_written": (
            local_note is not None and cloud_note is not None
            and local_note["content_text"] == cloud_note["content_text"] == ui["note_text"]
            and local_note["author"] == cloud_note["author"] == "user"
        ),
        "note_same_topic_task_and_source": (
            local_note["task_id"] == str(task_id) and local_note["topic_id"] == str(topic_id)
            and cloud_note["task_id"] == task_id and cloud_note["topic_id"] == topic_id
            and json.loads(local_note["source_refs_json"]) == ui["source_refs"]
            and [str(item) for item in cloud_note["source_refs"]] == ui["source_refs"]
        ),
        "old_note_and_queue_preserved": (
            old_note is not None and old_note["content_text"]
            == "登录成功说明应用识别了用户身份。" and pending == 0
            and ui["final_sync"]["queued"] == 0
        ),
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "replay_not_live", "candidate_sha256": reuse["candidate_sha256"],
        "attempt_id": str(attempt), "note_id": str(note_id),
        "answer_sha256": hashlib.sha256(ui["answer_text"].encode()).hexdigest(),
        "note_sha256": hashlib.sha256(ui["note_text"].encode()).hexdigest(),
        "checks": checks,
    }
    target = run / "artifacts" / "answer-note-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    if result["status"] != "PASS":
        raise AssertionError(f"S04 回答笔记审计失败：{checks}")
    print(f"S04_ANSWER_NOTE_AUDIT_PASS {attempt} {note_id}")


if __name__ == "__main__":
    main()
