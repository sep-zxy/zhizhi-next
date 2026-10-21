"""Audit the live DeepSeek reply in the Windows-only S04 continuation."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    artifacts = args.run.resolve() / "artifacts"
    ui = read(artifacts / "s04-14-windows-memory-chat-live-ui-attempt2.json")
    finish = artifacts / "s04-07-answer-note-finish-ui.json"
    if not finish.exists():
        finish = artifacts / "s04-07-answer-note-live-ui.json"
    note = read(finish)
    deleted = read(artifacts / "s04-12-windows-temp-create.json")
    memory = read(artifacts / "s04-14-windows-reme-delete-rehearsal.json")
    account = uuid.UUID(ui["account_id"])
    session = uuid.UUID(ui["session_id"])
    with connect(args.dsn) as db:
        messages = db.execute(
            "SELECT message_id, role, origin, content_text, provider_name, model_name, "
            "provider_request_id, request_payload_hash, response_payload_hash, "
            "input_tokens, output_tokens, elapsed_ms "
            "FROM growth_chat_messages WHERE account_id=%s AND session_id=%s "
            "ORDER BY created_at, message_id", (account, session),
        ).fetchall()
        suggestions = db.execute(
            "SELECT count(*) AS n FROM growth_chat_suggestions "
            "WHERE account_id=%s AND session_id=%s", (account, session),
        ).fetchone()["n"]
        notes = db.execute(
            "SELECT note_id, content_text, deleted_at FROM growth_notes "
            "WHERE account_id=%s AND note_id IN (%s, %s)",
            (account, uuid.UUID(note["note_id"]), uuid.UUID(deleted["temp_note_id"])),
        ).fetchall()
    by_id = {str(row["note_id"]): row for row in notes}
    user, assistant = messages if len(messages) == 2 else (None, None)
    receipt = ui["assistant"]
    reply = receipt["content_text"]
    checks = {
        "same_candidate_and_account": ui["status"] == "observed_pending_audit"
        and ui["mode"] == "live_model_windows_local_cloud_rehearsal_not_formal"
        and ui["account_id"] == note["account_id"] == deleted["account_id"]
        and ui["executable_sha256"] == deleted["executable_sha256"],
        "current_memory_scoped_to_valid_evidence": ui["preview"]["memory_status"] == "current"
        and note["note_id"] in ui["preview"]["note_ids"]
        and note["attempt_id"] in ui["preview"]["attempt_ids"]
        and deleted["temp_note_id"] not in ui["preview"]["note_ids"]
        and ui["preview"]["historical_attempts"] is True
        and memory["status"] == "PASS",
        "cloud_notes_respect_tombstone": note["note_id"] in by_id
        and by_id[note["note_id"]]["content_text"] == note["note_text"]
        and by_id[note["note_id"]]["deleted_at"] is None
        and deleted["temp_note_id"] in by_id
        and by_id[deleted["temp_note_id"]]["deleted_at"] is not None,
        "live_provider_receipt_and_hash": receipt["provider_name"] == "deepseek"
        and receipt["model_name"] == "deepseek-flash"
        and receipt["request_payload_hash"] == ui["preview"]["payload_sha256"]
        and bool(receipt["response_payload_hash"])
        and receipt["input_tokens"] > 0 and receipt["output_tokens"] > 0
        and ui["sync_state"] == "acked",
        "reply_follows_valid_knowledge": all(term in reply for term in (
            "认证", "授权", "Bob", "所有者", "user.id", "doc.ownerId",
            "没有实现 JWT 验签"))
        and any(term in reply for term in ("比较", "等于", "一致", "==="))
        and deleted["temp_note_text"] not in reply
        and "登录" in reply and receipt["suggestion"] is None,
        "two_messages_durable_in_cloud": user is not None and assistant is not None
        and user["role"] == "user" and user["origin"] == "user"
        and user["content_text"] == ui["question"]
        and assistant["role"] == "assistant"
        and assistant["message_id"] == uuid.UUID(receipt["message_id"])
        and assistant["content_text"] == reply
        and assistant["provider_name"] == "deepseek"
        and assistant["model_name"] == "deepseek-flash"
        and assistant["request_payload_hash"] == receipt["request_payload_hash"]
        and assistant["response_payload_hash"] == receipt["response_payload_hash"],
        "no_unrequested_topic_suggestion": suggestions == 0,
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "windows_real_model_local_cloud_rehearsal_not_formal",
        "account_id": str(account), "session_id": str(session), "checks": checks,
        "provider_request_id": receipt["provider_request_id"],
        "input_tokens": receipt["input_tokens"],
        "output_tokens": receipt["output_tokens"],
    }
    target = artifacts / "s04-14-windows-memory-chat-live-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    if result["status"] != "PASS":
        raise AssertionError(f"真实模型记忆对话审计失败：{checks}")
    print("S04_MEMORY_CHAT_LIVE_REHEARSAL_PASS")


if __name__ == "__main__":
    main()
