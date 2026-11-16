"""Audit a real-model S01 chain copied into an isolated local cloud."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read(run / "preparation.json")
    ui = read(run / "artifacts/cloud-ui.json")
    source = run.parent / prepared["source_run_id"]
    manifest = read(source / "manifest.json")
    card = read(source / "artifacts/s01-card-live-ui.json")
    feedback = read(source / "artifacts/s01-feedback-live-ui.json")
    steps = [json.loads(line) for line in (source / "steps.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()]
    account = uuid.UUID(ui["account_id"])
    task_id = uuid.UUID(prepared["task_id"])

    with sqlite3.connect(run / "fixture/repo/.ahadiff/growth.sqlite") as local:
        local.row_factory = sqlite3.Row
        project = local.execute("SELECT project_id FROM projects").fetchone()
        local_attempts = [dict(row) for row in local.execute(
            "SELECT attempt_id, answer_text, parent_attempt_id, hint_level, feedback_json "
            "FROM learning_attempts WHERE task_id=? ORDER BY created_at, rowid",
            (prepared["task_id"],),
        )]
        local_note = local.execute(
            "SELECT note_id, content_text FROM engineering_notes WHERE task_id=?",
            (prepared["task_id"],),
        ).fetchone()
        policy = local.execute(
            "SELECT cloud_allowed, cloud_account_id FROM project_policies"
        ).fetchone()
        outbox = [dict(row) for row in local.execute(
            "SELECT operation_id, target_type, state FROM sync_outbox "
            "ORDER BY created_at, rowid"
        )]

    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        project_cloud = cloud.execute(
            "SELECT project_id FROM growth_projects WHERE account_id=%s", (account,)
        ).fetchone()
        snapshot = cloud.execute(
            "SELECT snapshot_id FROM growth_snapshots WHERE account_id=%s", (account,)
        ).fetchone()
        batch = cloud.execute(
            "SELECT origin, model_name FROM growth_opportunity_batches WHERE account_id=%s",
            (account,),
        ).fetchone()
        opportunity = cloud.execute(
            "SELECT opportunity_id FROM growth_opportunities WHERE account_id=%s",
            (account,),
        ).fetchone()
        task = cloud.execute(
            "SELECT task_id, progress, opportunity_id FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s", (account, task_id),
        ).fetchone()
        attempts = cloud.execute(
            "SELECT attempt_id, answer_text, parent_attempt_id, hint_level, "
            "feedback, feedback_origin FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s ORDER BY created_at, attempt_id",
            (account, task_id),
        ).fetchall()
        note = cloud.execute(
            "SELECT note_id, content_text, author FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(prepared["note_id"])),
        ).fetchone()
        operations = {str(row["operation_id"]) for row in cloud.execute(
            "SELECT operation_id FROM growth_sync_operations WHERE account_id=%s",
            (account,),
        )}
        jobs = cloud.execute(
            "SELECT source_type, state, COUNT(*) AS count FROM growth_memory_jobs "
            "WHERE account_id=%s GROUP BY source_type, state", (account,),
        ).fetchall()
        source_paths = [row["relative_path"] for row in cloud.execute(
            "SELECT relative_path FROM growth_source_refs WHERE account_id=%s", (account,)
        )]

    remote_by_id = {str(row["attempt_id"]): row for row in attempts}
    checks = {
        "same_candidate_and_real_model_chain": (
            manifest["candidate_sha256"] == prepared["candidate_sha256"]
            and prepared["source_model"] == "real_deepseek"
            and card["executable_sha256"] == feedback["executable_sha256"]
            == ui["executable_sha256"] == prepared["executable_sha256"]
            and [step["step_id"] for step in steps] == [f"S01-{i:02}" for i in range(1, 9)]
            and all(step["status"] == "PASS" for step in steps)
        ),
        "installed_ui_approved_full_preview": (
            ui["mode"] == prepared["mode"] == "replay_not_live"
            and ui["status"] == "observed"
            and ui["preview"]["count"] == 15
            and set(ui["preview"]["operation_kinds"]) == {
                "project", "feature", "snapshot", "analysis", "topic_decision",
                "task", "progress", "attempt", "feedback", "note",
            }
        ),
        "all_operations_acknowledged": (
            ui["summary"]["uploaded"] == 15
            and ui["summary"]["outbox"]["pending"] == 0
            and len(outbox) == 15
            and all(row["state"] == "acked" for row in outbox)
            and {row["operation_id"] for row in outbox} <= operations
        ),
        "same_account_project_and_snapshot": (
            project is not None and project_cloud is not None and snapshot is not None
            and policy["cloud_allowed"] == 1
            and policy["cloud_account_id"] == str(account)
            and str(project_cloud["project_id"]) == project["project_id"] == ui["project_id"]
            and str(snapshot["snapshot_id"]) == ui["snapshot_id"]
        ),
        "same_opportunity_and_completed_card": (
            batch is not None and batch["origin"] == "live"
            and batch["model_name"] == "deepseek-flash"
            and opportunity is not None and task is not None
            and str(opportunity["opportunity_id"]) == ui["opportunity_id"]
            == card["opportunity_id"] == prepared["opportunity_id"]
            and str(task["task_id"]) == ui["task_id"] == card["task_id"]
            and str(task["opportunity_id"]) == ui["opportunity_id"]
            and task["progress"] == "completed"
        ),
        "two_original_answers_and_feedback_preserved": (
            len(local_attempts) == len(attempts) == 2
            and {item["attempt_id"] for item in local_attempts} == set(prepared["attempt_ids"])
            and all(
                (remote := remote_by_id.get(item["attempt_id"])) is not None
                and remote["answer_text"] == item["answer_text"]
                and (str(remote["parent_attempt_id"]) if remote["parent_attempt_id"] else None)
                == item["parent_attempt_id"]
                and remote["hint_level"] == item["hint_level"]
                and remote["feedback"] == json.loads(item["feedback_json"])
                for item in local_attempts
            )
        ),
        "user_note_preserved": (
            local_note is not None and note is not None
            and local_note["note_id"] == str(note["note_id"]) == prepared["note_id"]
            and local_note["content_text"] == note["content_text"]
            and note["author"] == "user"
        ),
        "memory_jobs_durable": (
            sum(row["count"] for row in jobs) == 5
            and all(row["state"] in {"pending", "failed", "skipped", "succeeded"} for row in jobs)
        ),
        "only_approved_source_path_uploaded": (
            source_paths == ["src/demo/route/RoutePlanner.java"]
        ),
    }
    audit = {
        "run_id": prepared["run_id"],
        "mode": "replay_not_live",
        "cloud_kind": "isolated_local_postgresql",
        "source_model": "real_deepseek",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "account_id": str(account),
        "task_id": str(task_id),
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False))
    if audit["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
