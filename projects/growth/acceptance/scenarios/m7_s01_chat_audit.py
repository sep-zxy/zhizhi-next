"""Audit installed S01 memory chat rehearsal against local PostgreSQL receipts."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read_json(run / "preparation.json")
    ui = read_json(run / "artifacts" / "chat-ui.json")
    provider = read_json(run / "provider-request.json")
    source = run.parent / prepared["source_run_id"]
    source_audit = read_json(source / "audit.json")
    source_reme = read_json(source / "reme-recovery.json")
    source_ui = read_json(source / "artifacts" / "cloud-ui.json")
    account = uuid.UUID(prepared["account_id"])
    session = uuid.UUID(ui["session_id"])
    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        messages = cloud.execute(
            "SELECT message_id,role,origin,content_text,provider_name,model_name,"
            "provider_request_id,request_payload_hash,response_payload_hash,"
            "input_tokens,output_tokens,elapsed_ms "
            "FROM growth_chat_messages WHERE account_id=%s AND session_id=%s "
            "ORDER BY created_at,message_id", (account, session),
        ).fetchall()
        suggestions = cloud.execute(
            "SELECT COUNT(*) AS n FROM growth_chat_suggestions "
            "WHERE account_id=%s AND session_id=%s", (account, session),
        ).fetchone()["n"]
        note = cloud.execute(
            "SELECT content_text,revision FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(prepared["note_id"])),
        ).fetchone()
        attempts = cloud.execute(
            "SELECT attempt_id,parent_attempt_id,answer_text,feedback "
            "FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(prepared["task_id"])),
        ).fetchall()
    by_id = {str(item["attempt_id"]): item for item in attempts}
    original_id, corrected_id = prepared["attempt_ids"]
    original = by_id.get(original_id)
    corrected = by_id.get(corrected_id)
    user, assistant = messages if len(messages) == 2 else (None, None)
    receipt = ui["assistant"]
    checks = {
        "same_candidate_and_audited_reme_source": source_audit["status"] == "PASS"
        and source_reme["status"] == "PASS"
        and source_audit["candidate_sha256"] == prepared["candidate_sha256"]
        and ui["executable_sha256"] == source_ui["executable_sha256"]
        and prepared["mode"] == ui["mode"] == provider["mode"] == "replay_not_live",
        "restart_and_same_account": ui["status"] == "observed_pending_audit"
        and ui["reopened_after_projection"] is True
        and ui["account_id"] == prepared["account_id"]
        and ui["cloud_origin"] == prepared["cloud_origin"]
        and ui["sync"]["account_id"] == prepared["account_id"]
        and ui["sync"]["outbox"]["pending"] == 0,
        "reme_memory_returned_to_approved_preview": ui["preview"]["memory_status"]
        == provider["memory_status"] == "current"
        and prepared["note_id"] in ui["preview"]["note_ids"]
        and set(prepared["attempt_ids"]) <= set(ui["preview"]["attempt_ids"])
        and ui["preview"]["historical_attempts"]
        and provider["all_attempts_historical"]
        and set(provider["note_ids"]) == set(ui["preview"]["note_ids"])
        and set(provider["attempt_ids"]) == set(ui["preview"]["attempt_ids"]),
        "approved_text_reached_provider": ui["preview"]["payload_sha256"]
        == provider["prompt_sha256"] == receipt["request_payload_hash"],
        "past_error_and_corrected_sources_preserved": original is not None
        and corrected is not None and note is not None
        and corrected["parent_attempt_id"] == original["attempt_id"]
        and "新建一个线程" in original["answer_text"]
        and "不会等待" in original["answer_text"]
        and "执行器决定" in corrected["answer_text"]
        and "join 会等待" in corrected["answer_text"]
        and "执行器安排" in note["content_text"]
        and original["feedback"] is not None and corrected["feedback"] is not None
        and provider["original_attempt_id"] == original_id
        and provider["correction_attempt_id"] == corrected_id,
        "reply_describes_old_error_as_past": receipt["content_text"] == provider["reply"]
        and receipt["content_text"].startswith("你过去认为")
        and "后来你修正为" in receipt["content_text"]
        and "join 可能等待并阻塞调用方" in receipt["content_text"]
        and receipt["suggestion"] is None,
        "chat_messages_durable_in_cloud": user is not None and assistant is not None
        and user["role"] == "user" and user["origin"] == "user"
        and user["content_text"] == ui["question"]
        and assistant["role"] == "assistant"
        and assistant["message_id"] == uuid.UUID(receipt["message_id"])
        and assistant["content_text"] == receipt["content_text"]
        and assistant["request_payload_hash"] == receipt["request_payload_hash"]
        and assistant["response_payload_hash"] == receipt["response_payload_hash"]
        and assistant["model_name"] == "acceptance-replay-model"
        and assistant["provider_request_id"] == "s01-chat-replay-1"
        and assistant["input_tokens"] == 63 and assistant["output_tokens"] == 45
        and assistant["elapsed_ms"] >= 0 and ui["sync_state"] == "acked",
        "memory_question_did_not_create_topic": suggestions == 0,
    }
    result = {
        "run_id": run.name, "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "candidate_sha256": prepared["candidate_sha256"],
        "cloud_kind": "isolated_local_postgresql",
        "account_id": prepared["account_id"], "session_id": ui["session_id"],
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
