"""Verify one cloud event and one installed-app local capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import psycopg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--ui", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event.read_text(encoding="utf-8"))
    ui = json.loads(args.ui.read_text(encoding="utf-8"))
    local = args.workspace / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(local) as db:
        capture = db.execute(
            "SELECT device_id, binding_id, snapshot_id, expected_head_sha "
            "FROM local_development_event_captures "
            "WHERE account_id=? AND event_id=?",
            (event["account_id"], event["event_id"]),
        ).fetchall()
        snapshot = db.execute(
            "SELECT head_sha, resolved_base_sha, patch_text "
            "FROM snapshots WHERE snapshot_id=?",
            (ui["snapshot_id"],),
        ).fetchone()
        sources = db.execute(
            "SELECT relative_path, blob_hash, content FROM source_refs "
            "WHERE snapshot_id=? ORDER BY relative_path",
            (ui["snapshot_id"],),
        ).fetchall()
        receipt_count = db.execute(
            "SELECT count(*) FROM growth_events WHERE entity_type='development_event' "
            "AND entity_id=? AND event_type='development_event_captured'",
            (event["event_id"],),
        ).fetchone()[0]
    with psycopg.connect(args.dsn) as db:
        cloud_events = db.execute(
            "SELECT status, target_device_id, expected_head_sha "
            "FROM growth_development_events WHERE account_id=%s AND event_id=%s",
            (event["account_id"], event["event_id"]),
        ).fetchall()
        feed_count = db.execute(
            "SELECT count(*) FROM growth_change_feed "
            "WHERE account_id=%s AND entity_type='development_event' AND entity_id=%s",
            (event["account_id"], event["event_id"]),
        ).fetchone()[0]
    paths = [row[0] for row in sources]
    checks = {
        "one_local_capture": len(capture) == 1
        and capture[0] == (event["device_id"], event["binding_id"],
                           ui["snapshot_id"], event["head_sha"]),
        "snapshot_head": snapshot is not None and snapshot[0] == event["head_sha"],
        "snapshot_patch": snapshot is not None and "canEdit" in snapshot[2],
        "changed_sources": paths == ["src/access.ts", "src/document.ts"]
        and all(hashlib.sha256(row[2]).hexdigest() == row[1] for row in sources)
        and all((args.workspace / row[0]).read_bytes() == row[2] for row in sources),
        "one_local_receipt": receipt_count == 1,
        "one_cloud_event": len(cloud_events) == 1
        and cloud_events[0][0] == "targeted"
        and str(cloud_events[0][1]) == event["device_id"]
        and cloud_events[0][2].strip() == event["head_sha"],
        "one_cloud_change": feed_count == 1,
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "local_cloud_rehearsal",
        "event_id": event["event_id"],
        "snapshot_id": ui["snapshot_id"],
        "head_sha": event["head_sha"],
        "base_sha": snapshot[1] if snapshot else None,
        "source_paths": paths,
        "local_receipt_count": receipt_count,
        "cloud_change_count": feed_count,
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result["status"] != "PASS":
        raise AssertionError(f"事件双端状态不一致：{checks}")
    print(f"S04_EVENT_VERIFY_PASS {event['event_id']} {ui['snapshot_id']}")


if __name__ == "__main__":
    main()
