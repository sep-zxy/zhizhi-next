"""Audit installed S03 restart and memory chat rehearsal in PostgreSQL."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect
from m7_s03_chat_replay_provider import QUESTION, REPLY
from m7_s03_note_outage import N1


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    prep = load(run / "preparation.json")
    ui = load(run / "artifacts" / "chat-ui.json")
    provider = load(run / "provider-request.json")
    source = run.parent / prep["source_run_id"]
    outage = load(source / "outage-audit.json")
    recovery = load(source / "recovery-audit.json")
    source_ui = load(source / "artifacts" / "note-ui.json")
    account = uuid.UUID(prep["account_id"])
    session = uuid.UUID(ui["session_id"])
    with connect(args.dsn) as conn:
        messages = conn.execute(
            "SELECT message_id,role,origin,content_text,provider_name,model_name,"
            "provider_request_id,request_payload_hash,response_payload_hash,"
            "input_tokens,output_tokens,elapsed_ms "
            "FROM growth_chat_messages WHERE account_id=%s AND session_id=%s "
            "ORDER BY created_at,message_id", (account, session),
        ).fetchall()
        suggestions = conn.execute(
            "SELECT COUNT(*) AS n FROM growth_chat_suggestions "
            "WHERE account_id=%s AND session_id=%s", (account, session),
        ).fetchone()["n"]
        note = conn.execute(
            "SELECT content_text,revision FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(prep["note_id"])),
        ).fetchone()
        attempts = conn.execute(
            "SELECT attempt_id,parent_attempt_id,answer_text,feedback "
            "FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(prep["task_id"])),
        ).fetchall()
    by_id = {str(item["attempt_id"]): item for item in attempts}
    original_id, corrected_id = prep["attempt_ids"]
    original = by_id.get(original_id)
    corrected = by_id.get(corrected_id)
    user, assistant = messages if len(messages) == 2 else (None, None)
    receipt = ui["assistant"]
    checks = {
        "same_candidate_audited_reme_source": outage["status"] == "PASS"
        and recovery["status"] == "PASS"
        and prep["candidate_sha256"] == load(source / "preparation.json")["candidate_sha256"]
        and ui["executable_sha256"] == source_ui["executable_sha256"]
        and prep["mode"] == ui["mode"] == provider["mode"] == "replay_not_live",
        "installed_restart_kept_n1": ui["status"] == "observed_pending_audit"
        and ui["reopened_after_projection"] and ui["reopened_note_exact"]
        and ui["sync"]["account_id"] == prep["account_id"]
        and ui["sync"]["outbox"]["pending"] == 0
        and note is not None and note["revision"] == 1
        and note["content_text"] == N1,
        "reme_memory_in_approved_payload": ui["preview"]["memory_status"]
        == provider["memory_status"] == "current"
        and prep["note_id"] in ui["preview"]["note_ids"]
        and set(prep["attempt_ids"]) <= set(ui["preview"]["attempt_ids"])
        and ui["preview"]["historical_attempts"]
        and provider["all_attempts_historical"]
        and set(provider["note_ids"]) == set(ui["preview"]["note_ids"])
        and set(provider["attempt_ids"]) == set(ui["preview"]["attempt_ids"]),
        "approved_text_reached_provider": ui["preview"]["payload_sha256"]
        == provider["prompt_sha256"] == receipt["request_payload_hash"],
        "a1_a2_parent_feedback_preserved": original is not None
        and corrected is not None and corrected["parent_attempt_id"]
        == original["attempt_id"]
        and "后台线程自动删除缓存" in original["answer_text"]
        and "没有下一次访问" in corrected["answer_text"]
        and original["feedback"] is not None and corrected["feedback"] is not None
        and provider["original_attempt_id"] == original_id
        and provider["correction_attempt_id"] == corrected_id,
        "reply_marks_old_error_as_past": receipt["content_text"] == provider["reply"]
        == REPLY and receipt["content_text"].startswith("你过去以为")
        and "后来你修正为" in receipt["content_text"]
        and "未访问就不会自动刷新" in receipt["content_text"]
        and receipt["suggestion"] is None,
        "two_messages_durable_in_cloud": user is not None and assistant is not None
        and ui["question"] == QUESTION
        and user["role"] == "user" and user["origin"] == "user"
        and user["content_text"] == QUESTION
        and assistant["role"] == "assistant"
        and assistant["message_id"] == uuid.UUID(receipt["message_id"])
        and assistant["content_text"] == REPLY
        and assistant["request_payload_hash"] == receipt["request_payload_hash"]
        and assistant["response_payload_hash"] == receipt["response_payload_hash"]
        and assistant["model_name"] == "acceptance-replay-model"
        and assistant["provider_request_id"] == "s03-chat-replay-1"
        and assistant["input_tokens"] == 67 and assistant["output_tokens"] == 48
        and assistant["elapsed_ms"] >= 0 and ui["sync_state"] == "acked",
        "memory_question_did_not_create_topic": suggestions == 0,
    }
    result = {
        "run_id": run.name, "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "stage_status": "BLOCKED", "candidate_sha256": prep["candidate_sha256"],
        "account_id": prep["account_id"], "session_id": ui["session_id"],
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
