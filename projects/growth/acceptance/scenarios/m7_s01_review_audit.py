"""Audit an installed S01 review answer against isolated PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--require-same-candidate", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read_json(run / "preparation.json")
    ui = read_json(run / "artifacts" / "review-ui.json")
    source = run.parent / prepared["source_run_id"]
    source_audit = read_json(source / "audit.json")
    source_reme = read_json(source / "reme-recovery.json")
    account_id = uuid.UUID(prepared["account_id"])
    task_id = uuid.UUID(prepared["task_id"])
    review_id = uuid.UUID(ui["submission"]["review_id"])
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as local:
        local.row_factory = sqlite3.Row
        local_task = local.execute(
            "SELECT task_id,progress FROM growth_tasks WHERE task_id=?",
            (str(task_id),),
        ).fetchone()
        local_attempts = local.execute(
            "SELECT COUNT(*) FROM learning_attempts WHERE task_id=?", (str(task_id),),
        ).fetchone()[0]
        local_note = local.execute(
            "SELECT note_id,content_text FROM engineering_notes WHERE task_id=?",
            (str(task_id),),
        ).fetchone()
    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        events = cloud.execute(
            "SELECT review_id,task_id,schedule_revision,answer_text,answer,hint_level,"
            "actor_origin,rating,due_before,due_after,reviewed_at "
            "FROM growth_review_events WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchall()
        schedule = cloud.execute(
            "SELECT revision,due_at,last_hint_level,last_review_id,status "
            "FROM growth_review_schedules WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchone()
        cloud_task = cloud.execute(
            "SELECT question,progress FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchone()
        cloud_attempts = cloud.execute(
            "SELECT COUNT(*) AS n FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id),
        ).fetchone()["n"]
        cloud_note = cloud.execute(
            "SELECT note_id,content_text,author FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account_id, uuid.UUID(prepared["note_id"])),
        ).fetchone()
        memory_jobs = cloud.execute(
            "SELECT source_type,state,COUNT(*) AS n FROM growth_memory_jobs "
            "WHERE account_id=%s GROUP BY source_type,state", (account_id,),
        ).fetchall()
    event = events[0] if len(events) == 1 else None
    due = ui["due"][0] if len(ui.get("due", [])) == 1 else None
    submitted = ui["submission"]
    result = submitted["response"]
    clock = datetime.fromisoformat(ui["learning_clock"])
    expected_before = datetime.fromisoformat(due["due_at"]) if due else None
    expected_after = datetime.fromisoformat(result["due_at"])
    checks = {
        "replay_chain_and_candidate_boundary": source_audit["status"] == "PASS"
        and source_reme["status"] == "PASS"
        and ui["status"] == "observed"
        and prepared["mode"] == ui["mode"] == "replay_not_live"
        and prepared["source_candidate_sha256"] == source_audit["candidate_sha256"]
        and (prepared["source_candidate_sha256"] == prepared["review_candidate_sha256"])
        == args.require_same_candidate,
        "same_account_and_original_card": ui["account_id"] == prepared["account_id"]
        and ui["task_id"] == prepared["task_id"]
        and ui["cloud_origin"] == prepared["cloud_origin"]
        and ui["sync"]["outbox"]["pending"] == 0
        and ui["sync"]["outbox"]["conflict"] == 0
        and due is not None and due["task_id"] == str(task_id)
        and due["revision"] == 1 and bool(due["question"])
        and cloud_task is not None and cloud_task["question"] == due["question"],
        "clock_only_for_due_rehearsal": ui["before_due"] == "empty"
        and ui["clock_injections"]["due"] >= 1
        and ui["clock_injections"]["submit"] == 1
        and expected_before is not None and expected_before <= clock,
        "installed_ui_submitted_user_answer": submitted["answer_text"] == "否，join 可能等待。"
        and submitted["answer"] == "hard"
        and submitted["hint_level"] == 0
        and submitted["base_revision"] == 1
        and uuid.UUID(submitted["operation_id"]) is not None
        and ui["after_due"] == "empty",
        "cloud_saved_one_original_answer": event is not None
        and event["review_id"] == review_id
        and event["task_id"] == task_id
        and event["answer_text"] == submitted["answer_text"]
        and event["answer"] == "hard"
        and event["hint_level"] == 0
        and event["actor_origin"] == "user"
        and event["rating"] == 2,
        "revision_and_next_due_persisted": event is not None and schedule is not None
        and event["schedule_revision"] == schedule["revision"] == result["revision"] == 2
        and event["due_before"] == expected_before
        and event["reviewed_at"] == clock
        and event["due_after"] == schedule["due_at"] == expected_after
        and event["due_after"] > event["reviewed_at"]
        and schedule["last_review_id"] == review_id
        and schedule["last_hint_level"] == 0
        and schedule["status"] == "active",
        "learning_record_not_rewritten": local_task is not None
        and local_task["progress"] == "completed"
        and cloud_task is not None and cloud_task["progress"] == "completed"
        and local_attempts == cloud_attempts == 2
        and local_note is not None and cloud_note is not None
        and local_note["note_id"] == str(cloud_note["note_id"]) == prepared["note_id"]
        and local_note["content_text"] == cloud_note["content_text"]
        and cloud_note["author"] == "user",
        "review_not_projected_as_memory": sum(row["n"] for row in memory_jobs) == 5
        and {row["source_type"] for row in memory_jobs} == {"attempt", "note"},
    }
    audit = {
        "run_id": prepared["run_id"],
        "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "source_candidate_sha256": prepared["source_candidate_sha256"],
        "review_candidate_sha256": prepared["review_candidate_sha256"],
        "candidate_relation": "same" if args.require_same_candidate else "different",
        "account_id": str(account_id),
        "task_id": str(task_id),
        "review_id": str(review_id),
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False))
    if audit["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
