"""Audit installed feedback rehearsal and preserve its replay-only boundary."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read_json(run / "preparation.json")
    ui = read_json(run / "artifacts" / "feedback-ui.json")
    requests = json.loads((run / "provider-requests.json").read_text(encoding="utf-8"))
    card_run = run.parent / prepared["source_run_id"]
    card_audit = read_json(card_run / "audit.json")
    card_ui = read_json(card_run / "artifacts" / "card-ui.json")
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        attempts = [dict(row) for row in db.execute(
            "SELECT attempt_id, answer_text, parent_attempt_id, status, feedback_json, feedback_origin "
            "FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid", (prepared["task_id"],)
        )]
        model_requests = [dict(row) for row in db.execute(
            "SELECT attempt_id, approved_payload_hash, approved_payload_text, status "
            "FROM feedback_model_requests ORDER BY created_at"
        )]
        responses = [dict(row) for row in db.execute(
            "SELECT attempt_id, provider_request_id, input_tokens, output_tokens, elapsed_ms "
            "FROM feedback_model_responses ORDER BY created_at"
        )]
        notes = [dict(row) for row in db.execute(
            "SELECT note_id, content_text FROM engineering_notes WHERE task_id=?",
            (prepared["task_id"],),
        )]
    turns = ui["turns"]
    checks = {
        "isolated_same_candidate": card_audit["status"] == "PASS"
        and card_audit["mode"] == ui["mode"] == prepared["mode"] == "replay_not_live"
        and card_audit["candidate_sha256"] == prepared["candidate_sha256"]
        and card_ui["executable_sha256"] == ui["executable_sha256"],
        "two_saved_answers_selected": len(turns) == len(attempts) == 2
        and [item["attempt_id"] for item in attempts] == prepared["attempt_ids"]
        and attempts[1]["parent_attempt_id"] == attempts[0]["attempt_id"],
        "exact_approved_prompts_sent": len(requests) == len(model_requests) == 2
        and all(turn["preview"]["ui_payload_exact_match"]
                and turn["preview"]["payload_sha256"] == request["prompt_sha256"]
                and turn["preview"]["approval_hash"] == saved["approved_payload_hash"]
                and turn["attempt_id"] == saved["attempt_id"]
                for turn, request, saved in zip(turns, requests, model_requests)),
        "native_schema_and_replay_model": len(requests) == 2
        and all(item["has_native_schema"] and item["stream"] is False
                and item["model"] == "acceptance-replay-model" for item in requests),
        "structured_feedback_saved": len(attempts) == 2
        and all(item["status"] == "feedback_ready" and item["feedback_json"]
                and item["feedback_origin"] == "live" for item in attempts)
        and all(turn["status"] == "feedback_ready" for turn in turns),
        "error_corrected_without_replacement": len(attempts) == 2
        and attempts[0]["answer_text"] == card_ui["chain"]["attempts"][0]["answer_text"]
        and attempts[1]["answer_text"] == card_ui["chain"]["attempts"][1]["answer_text"]
        and len(turns[0]["feedback"]["corrections"]) == 1
        and len(turns[1]["feedback"]["corrections"]) == 0,
        "scenario_feedback_facts": len(turns) == 2
        and all(turn["feedback"]["user_claims"] == [turn["original_answer"]]
                and any("executor" in fact for fact in turn["feedback"]["code_facts"])
                and any("thenCombine" in fact and "join" in fact
                        for fact in turn["feedback"]["code_facts"])
                and any("新建线程" in principle and "join" in principle
                        for principle in turn["feedback"]["general_principles"])
                for turn in turns)
        and "新建线程" in turns[0]["feedback"]["corrections"][0]
        and "join" in turns[0]["feedback"]["corrections"][0],
        "response_usage_persisted": len(responses) == 2
        and all(row["attempt_id"] == turn["attempt_id"]
                and row["provider_request_id"] == f"feedback-replay-{index}"
                and row["input_tokens"] == 56 and row["output_tokens"] == 39
                and row["elapsed_ms"] >= 0
                for index, (row, turn) in enumerate(zip(responses, turns), 1)),
        "note_preserved": len(notes) == 1
        and notes[0]["note_id"] == card_ui["note_id"]
        and notes[0]["content_text"] == card_ui["chain"]["notes"][0]["content_text"],
    }
    audit = {
        "run_id": prepared["run_id"],
        "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "scope": "Windows 安装版两次反馈预览、批准、持久化与原回答保护预演",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "task_id": prepared["task_id"],
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
