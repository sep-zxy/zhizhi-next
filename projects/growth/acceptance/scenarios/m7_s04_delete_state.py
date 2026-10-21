"""Capture and verify the offline tombstone branch across two Windows devices."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import psycopg


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def local_state(workspace: Path, account: str, note: str, operation: str) -> dict:
    with sqlite3.connect(workspace / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        identity = db.execute(
            "SELECT device_id, cursor FROM sync_accounts WHERE account_id=?", (account,),
        ).fetchone()
        cached = db.execute(
            "SELECT revision, deleted_at, payload_json FROM sync_entities "
            "WHERE account_id=? AND entity_type='note' AND entity_id=?",
            (account, note),
        ).fetchone()
        draft = db.execute(
            "SELECT state, content_text, base_revision FROM sync_note_drafts "
            "WHERE account_id=? AND note_id=? AND operation_id=?",
            (account, note, operation),
        ).fetchone()
        outbox = db.execute(
            "SELECT state, method, path FROM sync_outbox "
            "WHERE account_id=? AND operation_id=?",
            (account, operation),
        ).fetchone()
    return {
        "workspace": str(workspace.resolve()),
        "device_id": identity["device_id"] if identity else None,
        "cursor": identity["cursor"] if identity else None,
        "cached_note": {
            "revision": cached["revision"],
            "deleted_at": cached["deleted_at"],
            "content_text": json.loads(cached["payload_json"])["content_text"],
        } if cached else None,
        "draft": dict(draft) if draft else None,
        "outbox": dict(outbox) if outbox else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("offline", "final"), required=True)
    parser.add_argument("--workspace-a", type=Path, required=True)
    parser.add_argument("--workspace-b", type=Path, required=True)
    parser.add_argument("--create", type=Path, required=True)
    parser.add_argument("--offline-edit", type=Path, required=True)
    parser.add_argument("--delete", type=Path)
    parser.add_argument("--recover", type=Path)
    parser.add_argument("--topic-view", type=Path)
    parser.add_argument("--export-ui", type=Path)
    parser.add_argument("--export-json", type=Path)
    parser.add_argument("--export-markdown", type=Path)
    parser.add_argument("--dsn")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    created = load(args.create)
    offline = load(args.offline_edit)
    account, note = created["account_id"], created["temp_note_id"]
    device_b = local_state(args.workspace_b, account, note, offline["operation_id"])
    result: dict = {
        "phase": args.phase,
        "account_id": account,
        "temp_note_id": note,
        "offline_operation_id": offline["operation_id"],
        "device_b": device_b,
    }
    if args.phase == "offline":
        checks = {
            "distinct_devices": created["device_id"] != device_b["device_id"],
            "cloud_was_unreachable": offline["cloud_available_during_edit"] is False,
            "cached_base_revision": device_b["cached_note"] is not None
            and device_b["cached_note"]["revision"] == 1
            and device_b["cached_note"]["deleted_at"] is None,
            "draft_pending_with_exact_text": device_b["draft"] is not None
            and device_b["draft"]["state"] == "pending"
            and device_b["draft"]["base_revision"] == 1
            and device_b["draft"]["content_text"] == offline["offline_text"],
            "outbox_pending": device_b["outbox"] is not None
            and device_b["outbox"]["state"] == "pending"
            and device_b["outbox"]["method"] == "PUT",
        }
    else:
        if not all((args.delete, args.recover, args.topic_view, args.export_ui,
                    args.export_json, args.export_markdown, args.dsn)):
            raise ValueError("最终核对需要删除、恢复、主题视图、导出及 PostgreSQL 信息")
        deleted, recovered = load(args.delete), load(args.recover)
        topic_view, export_ui = load(args.topic_view), load(args.export_ui)
        exported = load(args.export_json)
        export_markdown = args.export_markdown.read_text(encoding="utf-8")
        device_a = local_state(args.workspace_a, account, note, deleted["operation_id"])
        result["device_a"] = device_a
        with psycopg.connect(args.dsn) as db:
            note_rows = db.execute(
                "SELECT revision, content_text, deleted_at FROM growth_notes "
                "WHERE account_id=%s AND note_id=%s",
                (account, note),
            ).fetchall()
            feed = db.execute(
            "SELECT event_type, payload FROM growth_change_feed WHERE account_id=%s "
                "AND entity_type='note' AND entity_id=%s ORDER BY change_seq",
                (account, note),
            ).fetchall()
            export_rows = db.execute(
                "SELECT json_payload, markdown_text FROM growth_exports "
                "WHERE account_id=%s AND export_id=%s",
                (account, export_ui["download_export_id"]),
            ).fetchall()
        result["cloud"] = {
            "note_revision": note_rows[0][0] if note_rows else None,
            "note_text": note_rows[0][1] if note_rows else None,
            "deleted_at": note_rows[0][2].isoformat() if note_rows
            and note_rows[0][2] else None,
            "feed_count": len(feed),
            "feed_events": [{"event_type": row[0], "payload": row[1]}
                            for row in feed],
        }
        checks = {
            "ui_recovery": recovered["status"] == "PASS"
            and all(recovered["checks"].values()),
            "local_tombstone_both_devices": device_a["cached_note"] is not None
            and device_b["cached_note"] is not None
            and device_a["cached_note"]["deleted_at"] is not None
            and device_b["cached_note"]["deleted_at"] is not None,
            "cloud_tombstone": len(note_rows) == 1
            and note_rows[0][0] == 2
            and note_rows[0][1] == created["temp_note_text"]
            and note_rows[0][2] is not None,
            "offline_draft_recovery": device_b["draft"] is not None
            and device_b["draft"]["state"] == "recovery"
            and device_b["draft"]["content_text"] == offline["offline_text"],
            "old_upload_suppressed": device_b["outbox"] is not None
            and device_b["outbox"]["state"] == "deleted",
            "one_delete_event": sum(
                row[0] == "note_deleted" for row in feed
            ) == 1,
            "two_device_ids": device_a["device_id"] != device_b["device_id"]
            and device_a["device_id"] == created["device_id"],
            "deleted_note_hidden_from_normal_topic": topic_view["status"] == "PASS"
            and topic_view["checks"]["deleted_temp_not_normal_note"],
            "downloaded_export_has_tombstone": export_ui["status"] == "PASS"
            and len(export_rows) == 1
            and export_rows[0][0] == exported
            and export_rows[0][1] == export_markdown
            and any(item["note_id"] == note and item["deleted_at"] is not None
                    for item in exported["tables"]["notes"])
            and offline["offline_text"] not in json.dumps(exported, ensure_ascii=False)
            and offline["offline_text"] not in export_markdown,
        }
        result["deletion_export_id"] = export_ui["download_export_id"]
    result["checks"] = checks
    result["status"] = "PASS" if all(checks.values()) else "FAIL"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result["status"] != "PASS":
        raise AssertionError(f"离线删除状态不一致：{checks}")
    print(f"S04_DELETE_STATE_{args.phase.upper()}_PASS {note}")


if __name__ == "__main__":
    main()
