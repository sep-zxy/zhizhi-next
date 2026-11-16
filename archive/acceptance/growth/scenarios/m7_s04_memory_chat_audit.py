"""Audit the S04 post-deletion chat receipt against PostgreSQL and source evidence."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect
from m7_s04_memory_chat_provider import QUESTION, REPLY


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    artifacts = run / "artifacts"
    setup = read(artifacts / "cloned-s04-setup.json")
    answer = read(artifacts / "answer-note-ui.json")
    created = read(artifacts / "temp-create.json")
    final = read(artifacts / "final-audit.json")
    ui = read(artifacts / "memory-chat-ui.json")
    provider = read(artifacts / "memory-chat-provider-request.json")
    account = uuid.UUID(setup["account_id"])
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
        note = db.execute(
            "SELECT revision, content_text, deleted_at FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(answer["note_id"])),
        ).fetchone()
        temporary = db.execute(
            "SELECT revision, deleted_at FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(created["temp_note_id"])),
        ).fetchone()
    user, assistant = messages if len(messages) == 2 else (None, None)
    receipt = ui["assistant"]
    checks = {
        "same_candidate_reopened_note": (
            final["status"] == "PASS"
            and ui["status"] == "observed_pending_audit"
            and ui["mode"] == provider["mode"] == "replay_not_live"
            and ui["executable_sha256"] == read(
                artifacts / "final-export-ui.json"
            )["executable_sha256"]
            and ui["reopened_note_exact"]
            and ui["sync"]["account_id"] == setup["account_id"]
            and ui["sync"]["outbox"]["pending"] == 0
            and note is not None and note["content_text"] == answer["note_text"]
            and note["deleted_at"] is None
            and temporary is not None and temporary["deleted_at"] is not None
        ),
        "approved_payload_is_current_memory": (
            ui["preview"]["memory_status"] == provider["memory_status"] == "current"
            and answer["note_id"] in ui["preview"]["note_ids"]
            and answer["attempt_id"] in ui["preview"]["attempt_ids"]
            and created["temp_note_id"] not in ui["preview"]["note_ids"]
            and set(ui["preview"]["note_ids"]) == set(provider["note_ids"])
            and set(ui["preview"]["attempt_ids"]) == set(provider["attempt_ids"])
            and provider["historical_attempts"]
        ),
        "approved_text_reached_provider": (
            ui["preview"]["payload_sha256"]
            == provider["prompt_sha256"] == receipt["request_payload_hash"]
        ),
        "reply_uses_valid_knowledge": (
            ui["question"] == QUESTION
            and receipt["content_text"] == provider["reply"] == REPLY
            and "Bob 即使已登录" in REPLY
            and "所有者" in REPLY and "FORBIDDEN" in REPLY
            and "没有证明 JWT 验签" in REPLY
            and created["temp_note_text"] not in REPLY
            and receipt["suggestion"] is None
        ),
        "two_messages_durable_in_cloud": (
            user is not None and assistant is not None
            and user["role"] == "user" and user["origin"] == "user"
            and user["content_text"] == QUESTION
            and assistant["role"] == "assistant"
            and assistant["message_id"] == uuid.UUID(receipt["message_id"])
            and assistant["content_text"] == REPLY
            and assistant["request_payload_hash"] == receipt["request_payload_hash"]
            and assistant["response_payload_hash"] == receipt["response_payload_hash"]
            and assistant["provider_name"] == "local"
            and assistant["model_name"] == "acceptance-replay-model"
            and assistant["provider_request_id"] == "s04-memory-chat-replay-1"
            and assistant["input_tokens"] == 75 and assistant["output_tokens"] == 51
            and assistant["elapsed_ms"] >= 0 and ui["sync_state"] == "acked"
        ),
        "no_new_topic_suggestion": suggestions == 0,
    }
    result = {
        "run_id": run.name, "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "stage_status": "BLOCKED", "account_id": str(account),
        "session_id": str(session), "checks": checks,
    }
    (artifacts / "memory-chat-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result["status"] != "PASS":
        raise AssertionError(f"S04 记忆对话审计失败：{checks}")
    print("S04_MEMORY_CHAT_AUDIT_PASS")


if __name__ == "__main__":
    main()
