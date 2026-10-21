"""Audit installed S01 publication against its isolated PostgreSQL database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--windows-local", action="store_true")
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read_json(run / (
        "artifacts/chat-preparation.json" if args.windows_local else "preparation.json"
    ))
    ui = read_json(run / "artifacts" / "cloud-ui.json")
    if args.windows_local:
        source = run
        manifest = read_json(run / "manifest.json")
        card_ui = read_json(run / "artifacts/card-ui.json")
        model_ui = read_json(run / "artifacts/model-ui.json")
        prepared.update({"run_id": manifest["run_id"],
                         "candidate_sha256": manifest["candidate_sha256"],
                         "source_run_id": manifest["run_id"]})
        feedback_audit = {"status": "PASS", "mode": "windows_local_live",
                          "candidate_sha256": manifest["candidate_sha256"]}
        feedback_ui = card_ui
    else:
        source = run.parent / prepared["source_run_id"]
        feedback_audit = read_json(source / "audit.json")
        feedback_ui = read_json(source / "artifacts" / "feedback-ui.json")
    account = uuid.UUID(ui["account_id"])
    task_id = uuid.UUID(prepared["task_id"])
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as local:
        local.row_factory = sqlite3.Row
        project = local.execute("SELECT project_id FROM projects").fetchone()
        local_attempts = [dict(row) for row in local.execute(
            "SELECT attempt_id, answer_text, parent_attempt_id, hint_level, feedback_json "
            "FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid",
            (prepared["task_id"],),
        )]
        local_note = local.execute(
            "SELECT note_id, content_text FROM engineering_notes WHERE task_id=?",
            (prepared["task_id"],),
        ).fetchone()
        policy = local.execute(
            "SELECT cloud_allowed,cloud_account_id FROM project_policies"
        ).fetchone()
        outbox = [dict(row) for row in local.execute(
            "SELECT operation_id,target_type,state FROM sync_outbox ORDER BY created_at,rowid"
        )]
    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        project_cloud = cloud.execute(
            "SELECT project_id FROM growth_projects WHERE account_id=%s", (account,)
        ).fetchone()
        snapshot = cloud.execute(
            "SELECT snapshot_id FROM growth_snapshots WHERE account_id=%s", (account,)
        ).fetchone()
        batch = cloud.execute(
            "SELECT origin,model_name FROM growth_opportunity_batches WHERE account_id=%s",
            (account,),
        ).fetchone()
        opportunity = cloud.execute(
            "SELECT opportunity_id FROM growth_opportunities WHERE account_id=%s", (account,)
        ).fetchone()
        task = cloud.execute(
            "SELECT task_id,progress,opportunity_id FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s", (account, task_id),
        ).fetchone()
        attempts = cloud.execute(
            "SELECT attempt_id,answer_text,parent_attempt_id,hint_level,feedback,feedback_origin "
            "FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s "
            "ORDER BY created_at,attempt_id", (account, task_id),
        ).fetchall()
        note = cloud.execute(
            "SELECT note_id,content_text,author FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account, uuid.UUID(prepared["note_id"])),
        ).fetchone()
        cloud_operations = {str(row["operation_id"]) for row in cloud.execute(
            "SELECT operation_id FROM growth_sync_operations WHERE account_id=%s", (account,)
        )}
        memory_jobs = cloud.execute(
            "SELECT source_type,state,COUNT(*) AS count FROM growth_memory_jobs "
            "WHERE account_id=%s GROUP BY source_type,state", (account,),
        ).fetchall()
        source_paths = [row["relative_path"] for row in cloud.execute(
            "SELECT relative_path FROM growth_source_refs WHERE account_id=%s", (account,)
        )]
    by_id = {str(row["attempt_id"]): row for row in attempts}
    checks = {
        "source_and_candidate": feedback_audit["status"] == "PASS"
        and feedback_audit["mode"] == ui["mode"] == prepared["mode"]
        and (ui["mode"] == "windows_local_live" if args.windows_local
             else ui["mode"] == "replay_not_live")
        and feedback_audit["candidate_sha256"] == prepared["candidate_sha256"]
        and feedback_ui["executable_sha256"] == ui["executable_sha256"]
        and (model_ui["analysis"]["mode"] == "live" and card_ui["status"] == "observed"
             if args.windows_local else True),
        "approved_full_chain_preview": ui["preview"]["count"] == 15
        and set(ui["preview"]["operation_kinds"]) == {
            "project", "feature", "snapshot", "analysis", "topic_decision",
            "task", "progress", "attempt", "feedback", "note",
        },
        "installed_sync_acknowledged": ui["status"] == "observed"
        and ui["summary"]["uploaded"] == 15
        and ui["summary"]["outbox"]["pending"] == 0
        and len(outbox) >= 15 and all(row["state"] == "acked" for row in outbox),
        "same_account_and_project": project is not None and project_cloud is not None
        and policy["cloud_allowed"] == 1 and policy["cloud_account_id"] == str(account)
        and str(project_cloud["project_id"]) == project["project_id"] == ui["project_id"],
        "same_snapshot_opportunity_card": snapshot is not None and opportunity is not None
        and task is not None and str(snapshot["snapshot_id"]) == ui["snapshot_id"]
        and str(opportunity["opportunity_id"]) == ui["opportunity_id"]
        and str(task["task_id"]) == ui["task_id"]
        and str(task["opportunity_id"]) == ui["opportunity_id"]
        and task["progress"] == "completed",
        "two_answers_and_feedback_exact": len(local_attempts) == len(attempts) == 2
        and all((remote := by_id.get(item["attempt_id"])) is not None
                and remote["answer_text"] == item["answer_text"]
                and (str(remote["parent_attempt_id"]) if remote["parent_attempt_id"] else None)
                == item["parent_attempt_id"]
                and remote["hint_level"] == item["hint_level"]
                and remote["feedback"] == json.loads(item["feedback_json"])
                for item in local_attempts),
        "same_user_note": local_note is not None and note is not None
        and str(note["note_id"]) == local_note["note_id"]
        and note["content_text"] == local_note["content_text"]
        and note["author"] == "user",
        "all_published_operation_ids_present": {row["operation_id"] for row in outbox}
        <= cloud_operations,
        "derived_memory_jobs_persisted": sum(row["count"] for row in memory_jobs) == 5
        and all(row["state"] in {"pending", "failed", "skipped", "succeeded"}
                for row in memory_jobs),
        "local_only_source_boundary": source_paths == ["src/demo/route/RoutePlanner.java"]
        and batch is not None and batch["origin"] == "live"
        and batch["model_name"] == (
            model_ui["preview"]["model_name"] if args.windows_local
            else "acceptance-replay-model"),
    }
    audit = {
        "run_id": prepared["run_id"],
        "mode": prepared["mode"],
        "cloud_kind": "isolated_local_postgresql",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "account_id": str(account),
        "task_id": str(task_id),
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (run / "postgres-evidence.json").write_text(
        json.dumps({
            "account_id": str(account),
            "project_id": str(project_cloud["project_id"]) if project_cloud else None,
            "snapshot_id": str(snapshot["snapshot_id"]) if snapshot else None,
            "opportunity_id": str(opportunity["opportunity_id"]) if opportunity else None,
            "task": {"task_id": str(task["task_id"]), "progress": task["progress"]} if task else None,
            "attempts": [{
                "attempt_id": str(row["attempt_id"]),
                "parent_attempt_id": str(row["parent_attempt_id"]) if row["parent_attempt_id"] else None,
                "hint_level": row["hint_level"],
                "has_feedback": row["feedback"] is not None,
            } for row in attempts],
            "note_id": str(note["note_id"]) if note else None,
            "published_operation_count": len({row["operation_id"] for row in outbox} & cloud_operations),
            "memory_jobs": [dict(row) for row in memory_jobs],
            "source_paths": source_paths,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False))
    if audit["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
