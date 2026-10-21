"""Audit exact S03 A1/A2, feedback and same-ID isolated cloud receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from m7_s03_feedback_replay_provider import A1, A2

from ahadiff.growth.cloud.db import connect


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    prepared = load(run / "preparation.json")
    answers = load(run / "artifacts" / "answers-ui.json")
    feedback = load(run / "artifacts" / "feedback-ui.json")
    synced = load(run / "artifacts" / "sync-ui.json")
    requests = json.loads((run / "provider-feedback.json").read_text(encoding="utf-8"))
    source = run.parent / prepared["source_run_id"]
    source_audit = load(source / "audit.json")
    source_ui = load(source / "artifacts" / "card-ui.json")
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        task = db.execute(
            "SELECT * FROM growth_tasks WHERE task_id=?", (prepared["task_id"],)
        ).fetchone()
        local = [dict(row) for row in db.execute(
            "SELECT * FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid",
            (prepared["task_id"],),
        )]
        model_requests = [dict(row) for row in db.execute(
            "SELECT * FROM feedback_model_requests ORDER BY created_at"
        )]
        responses = [dict(row) for row in db.execute(
            "SELECT * FROM feedback_model_responses ORDER BY created_at"
        )]
    account_id = uuid.UUID(prepared["account_id"])
    task_id = uuid.UUID(prepared["task_id"])
    with connect(args.dsn) as conn:
        cloud_task = conn.execute(
            "SELECT module_id, module_index_revision, source_refs FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id),
        ).fetchone()
        cloud = conn.execute(
            "SELECT attempt_id, answer_text, answer_hash, parent_attempt_id, hint_level, "
            "status, feedback FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s ORDER BY created_at",
            (account_id, task_id),
        ).fetchall()
        feed = conn.execute(
            "SELECT event_type FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='attempt' AND entity_id=ANY(%s) ORDER BY change_seq",
            (account_id, [uuid.UUID(row["attempt_id"]) for row in local]),
        ).fetchall()
    turns = feedback["turns"]
    refs = set(prepared["source_ref_ids"])
    expected_ids = [answers["first_attempt_id"], answers["second_attempt_id"]]
    checks = {
        "same_candidate_and_installed_exe": source_audit["status"] == "PASS"
        and prepared["candidate_sha256"] == source_audit["candidate_sha256"]
        and answers["executable_sha256"] == feedback["executable_sha256"]
        == synced["executable_sha256"] == source_ui["executable_sha256"],
        "module_card_preserved": task is not None
        and task["module_id"] == prepared["module_id"]
        and task["module_index_revision"] == prepared["module_index_revision"]
        and cloud_task is not None
        and str(cloud_task["module_id"]) == prepared["module_id"]
        and cloud_task["module_index_revision"].strip()
        == prepared["module_index_revision"]
        and set(cloud_task["source_refs"]) == refs,
        "exact_two_answers_and_parent": len(local) == len(cloud) == 2
        and [row["attempt_id"] for row in local] == expected_ids
        and [row["answer_text"] for row in local] == [A1, A2]
        and local[0]["parent_attempt_id"] is None
        and local[1]["parent_attempt_id"] == expected_ids[0]
        and [row["hint_level"] for row in local] == [0, 1],
        "approved_payloads_reached_provider": len(turns) == len(requests)
        == len(model_requests) == 2
        and all(
            turn["preview"]["ui_payload_exact_match"]
            and turn["preview"]["payload_sha256"] == request["prompt_sha256"]
            and turn["preview"]["approval_hash"] == saved["approved_payload_hash"]
            and request["answer_sha256"]
            == hashlib.sha256(turn["original_answer"].encode()).hexdigest()
            and set(request["source_ref_ids"]) == refs
            and request["has_native_schema"] and not request["stream"]
            for turn, request, saved in zip(turns, requests, model_requests, strict=False)
        ),
        "ttl_feedback_semantics_in_replay": len(turns) == 2
        and all("expiresAt" in turn["feedback"]["code_facts"][0]
                and set(turn["feedback"]["source_refs"]) == refs
                and turn["feedback"]["user_claims"] == [turn["original_answer"]]
                for turn in turns)
        and "后台线程" in turns[0]["feedback"]["corrections"][0]
        and "访问时" in turns[1]["feedback"]["summary"]
        and turns[1]["feedback"]["corrections"] == [],
        "feedback_saved_without_replacing_answers": len(local) == len(responses) == 2
        and all(row["status"] == "feedback_ready" and row["feedback_json"]
                and row["answer_text"] == expected
                for row, expected in zip(local, [A1, A2], strict=False))
        and all(response["attempt_id"] == attempt_id
                for response, attempt_id in zip(responses, expected_ids, strict=False)),
        "cloud_same_attempt_ids_and_revisions": len(cloud) == 2
        and all(str(row["attempt_id"]) == expected_ids[index]
                and row["answer_text"] == [A1, A2][index]
                and row["answer_hash"].strip()
                == hashlib.sha256([A1, A2][index].encode()).hexdigest()
                and row["status"] == "feedback_ready" and row["feedback"]
                and (str(row["parent_attempt_id"]) if row["parent_attempt_id"] else None)
                == ([None, expected_ids[0]][index])
                for index, row in enumerate(cloud)),
        "cloud_receipt_and_events": synced["status"] == "observed_pending_audit"
        and synced["sync"]["outbox"]["pending"] == 0
        and synced["sync"]["outbox"]["conflict"] == 0
        and [row["event_type"] for row in feed]
        == ["answer_saved", "answer_saved", "feedback_ready", "feedback_ready"],
    }
    audit = {
        "run_id": run.name, "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "replay_not_live", "stage_status": "BLOCKED",
        "scope": "S03 Windows 安装版 A1/A2 原文、反馈预览批准及隔离云回执",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "task_id": prepared["task_id"], "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False))
    if audit["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
