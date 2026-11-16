"""Audit installed S04 two-turn replay before topic confirmation."""

from __future__ import annotations

import argparse
import hashlib
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
    chat = read_json(run / "artifacts" / "chat-ui.json")
    provider = read_json(run / "provider-requests.json")
    source = read_json(run.parent / prepared["source_local_run_id"] / "result.json")
    source_ui = read_json(run.parent / prepared["source_local_run_id"] / "electron-s04.json")
    account = uuid.UUID(prepared["account_id"])
    session = uuid.UUID(chat["session_id"])
    suggestion_id = chat["turns"][1]["assistant_receipt"]["suggestion"]["suggestion_id"]
    with psycopg.connect(args.dsn, row_factory=dict_row) as cloud:
        messages = cloud.execute(
            "SELECT message_id,role,origin,content_text,model_name,provider_name,"
            "request_payload_hash,response_payload_hash FROM growth_chat_messages "
            "WHERE account_id=%s AND session_id=%s ORDER BY created_at,message_id",
            (account, session),
        ).fetchall()
        suggestion = cloud.execute(
            "SELECT title,status,topic_id FROM growth_chat_suggestions "
            "WHERE account_id=%s AND suggestion_id=%s",
            (account, uuid.UUID(suggestion_id)),
        ).fetchone()
        old_topic = cloud.execute(
            "SELECT title,status FROM growth_topics WHERE account_id=%s AND topic_id=%s",
            (account, uuid.UUID(prepared["old_topic_id"])),
        ).fetchone()
        old_note = cloud.execute(
            "SELECT content_text,revision FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(prepared["old_note_id"])),
        ).fetchone()
    turns = chat["turns"]
    hashes = [hashlib.sha256(item["preview"]["payload_text"].encode("utf-8")).hexdigest()
              for item in turns]
    expected_replies = [
        "认证说明请求属于谁，JWT 本身不代表所有接口权限。",
        "认证回答你是谁；授权再判断你能否修改指定文档。",
    ]
    checks = {
        "same_candidate_and_old_topic_source": source["status"] == "PASS"
        and source["candidate_sha256"] == prepared["candidate_sha256"]
        and source_ui["account_id"] == prepared["account_id"]
        and len(chat["topics_before"]) == len(chat["topics_after"]) == 1
        and chat["topics_before"] == chat["topics_after"],
        "installed_two_turns_and_acks": chat["mode"] == "replay_not_live"
        and chat["status"] == "observed_pending_semantic_review"
        and len(turns) == 2 and all(item["sync_state"] == "acked" for item in turns)
        and len(messages) == 4,
        "user_messages_persisted": len(messages) == 4
        and all(messages[i]["role"] == "user" and messages[i]["origin"] == "user"
                and messages[i]["content_text"] == turns[i // 2]["user_message"]
                for i in (0, 2)),
        "model_replies_persisted": len(messages) == 4
        and all(messages[i]["role"] == "assistant"
                and messages[i]["message_id"]
                == uuid.UUID(turns[i // 2]["assistant_receipt"]["message_id"])
                and messages[i]["content_text"] == expected_replies[i // 2]
                and messages[i]["model_name"] == "acceptance-replay-model"
                for i in (1, 3)),
        "approved_payloads_reached_provider": len(provider) == 2
        and all(provider[i]["prompt_sha256"] == hashes[i]
                == turns[i]["assistant_receipt"]["request_payload_hash"]
                == messages[2 * i + 1]["request_payload_hash"]
                and provider[i]["has_native_schema"]
                and provider[i]["stream"] is False for i in (0, 1)),
        "second_turn_keeps_first_context": len(provider) == 2
        and provider[0]["contains_u1"] and not provider[0]["contains_u2"]
        and provider[1]["contains_u1"] and provider[1]["contains_u2"]
        and turns[0]["assistant_receipt"]["content_text"]
        in turns[1]["preview"]["payload_text"],
        "suggestion_waited_for_user": turns[0]["assistant_receipt"]["suggestion"] is None
        and turns[1]["assistant_receipt"]["suggestion"]["suggestion_id"] == suggestion_id
        and chat["pending_suggestion_visible"]
        and suggestion is not None and suggestion["title"] == "Web 认证与授权",
        "old_learning_history_preserved": old_topic is not None
        and old_topic["title"] == "登录机制" and old_topic["status"] == "active"
        and old_note is not None
        and old_note["content_text"] == source_ui["note_text"]
        and old_note["revision"] == 1,
    }
    result = {
        "run_id": run.name, "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "candidate_sha256": prepared["candidate_sha256"],
        "cloud_kind": "isolated_local_postgresql",
        "account_id": prepared["account_id"], "session_id": chat["session_id"],
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
