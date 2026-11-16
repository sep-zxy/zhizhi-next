"""Verify installed S04 chat confirmation against the isolated cloud database."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def verify(dsn: str, driver_path: Path, *, live: bool = False) -> dict[str, object]:
    driver = json.loads(driver_path.read_text(encoding="utf-8"))
    account_id = uuid.UUID(driver["account_id"])
    session_id = uuid.UUID(driver["session_id"])
    suggestion_id = uuid.UUID(driver["suggestion_id"])
    topic_id = uuid.UUID(driver["first_receipt"]["topic_id"])
    operation_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"growth-chat-decision:{account_id}:{suggestion_id}:confirm",
    )
    with connect(dsn) as conn:
        suggestion = conn.execute(
            "SELECT status, title, topic_id FROM growth_chat_suggestions "
            "WHERE account_id=%s AND session_id=%s AND suggestion_id=%s",
            (account_id, session_id, suggestion_id),
        ).fetchone()
        topics = conn.execute(
            "SELECT topic_id, title, revision FROM growth_topics WHERE account_id=%s "
            "AND deleted_at IS NULL ORDER BY title, topic_id",
            (account_id,),
        ).fetchall()
        operation = conn.execute(
            "SELECT status_code, response FROM growth_sync_operations "
            "WHERE account_id=%s AND operation_id=%s",
            (account_id, operation_id),
        ).fetchone()
        event_count = conn.execute(
            "SELECT count(*) AS total FROM growth_domain_events "
            "WHERE account_id=%s AND entity_type='chat_suggestion' "
            "AND entity_id=%s AND event_type='chat_topic_confirmed'",
            (account_id, suggestion_id),
        ).fetchone()["total"]
        update_count = conn.execute(
            "SELECT count(*) AS total FROM growth_domain_events "
            "WHERE account_id=%s AND entity_type='topic' "
            "AND entity_id=%s AND event_type='topic_user_updated'",
            (account_id, topic_id),
        ).fetchone()["total"]
    expected_mode = "live_observation" if live else "replay_not_live"
    renamed_topic = next((row for row in topics if row["topic_id"] == topic_id), None)
    checks = {
        "driver_complete": driver["mode"] == expected_mode
        and driver["status"] == "observed_pending_database_and_semantic_review",
        "same_topic_after_retry": driver["first_http_status"] == 200
        and driver["retry_http_status"] == 200
        and driver["retry_receipt"]["topic_id"] == str(topic_id),
        "ui_topic_count": len(driver["topics_before"]) == 1
        and len(driver["topics_after_ui"]) == 2
        and len(driver["topics_after_restart"]) == 2,
        "cloud_suggestion_confirmed": suggestion is not None
        and suggestion["status"] == "confirmed" and suggestion["topic_id"] == topic_id,
        "exactly_one_new_topic": len(topics) == 2
        and sum(row["topic_id"] == topic_id for row in topics) == 1,
        "one_idempotency_receipt": operation is not None
        and operation["status_code"] == 200
        and operation["response"].get("topic_id") == str(topic_id),
        "one_confirm_event": event_count == 1,
        "live_user_title": (not live) or (
            driver.get("rename_http_status") == 200
            and renamed_topic is not None
            and renamed_topic["title"] == "Web 认证与授权"
            and renamed_topic["revision"] == 2
            and update_count == 1
            and len(driver.get("topics_after_rename", [])) == 2
            and any(item["id"] == str(topic_id)
                    and item["title"].startswith("Web 认证与授权 · r2")
                    for item in driver["topics_after_rename"])
            and any(item["id"] == str(topic_id)
                    and item["title"].startswith("Web 认证与授权 · r2")
                    for item in driver["topics_after_restart"])
        ),
    }
    return {
        "mode": expected_mode,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "account_id": str(account_id),
        "session_id": str(session_id),
        "suggestion_id": str(suggestion_id),
        "topic_id": str(topic_id),
        "operation_id": str(operation_id),
        "topic_titles": [row["title"] for row in topics],
        "confirm_event_count": event_count,
        "rename_event_count": update_count,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    result = verify(args.dsn, args.driver, live=args.live)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(result["status"])
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
