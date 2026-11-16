"""Verify installed topic maintenance, export, and reinstall evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import psycopg


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--reinstall", type=Path, required=True)
    parser.add_argument("--export-json", type=Path, required=True)
    parser.add_argument("--export-markdown", type=Path, required=True)
    parser.add_argument("--download-json", type=Path, required=True)
    parser.add_argument("--download-markdown", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, after, reinstall = (read_json(path) for path in
                                (args.before, args.after, args.reinstall))
    exported = read_json(args.export_json)
    markdown = args.export_markdown.read_text(encoding="utf-8")
    downloaded = read_json(args.download_json)
    downloaded_markdown = args.download_markdown.read_text(encoding="utf-8")
    account = before["account_id"]
    topic_ids = (before["old_topic_id"], before["new_topic_id"],
                 before["auth_topic_id"], before["permission_topic_id"])
    with psycopg.connect(args.dsn) as db:
        topics = db.execute(
            "SELECT topic_id, title, status, revision, parent_topic_id "
            "FROM growth_topics WHERE account_id=%s AND topic_id=ANY(%s)",
            (account, list(topic_ids)),
        ).fetchall()
        topics_by_id = {str(row[0]): row for row in topics}
        notes = db.execute(
            "SELECT note_id, topic_id, content_text, deleted_at "
            "FROM growth_notes WHERE account_id=%s AND note_id=ANY(%s)",
            (account, [before["old_note_id"], before["new_note_id"]]),
        ).fetchall()
        notes_by_id = {str(row[0]): row for row in notes}
        aliases = db.execute(
            "SELECT alias_topic_id, canonical_topic_id FROM growth_topic_aliases "
            "WHERE account_id=%s AND alias_topic_id=%s",
            (account, before["old_topic_id"]),
        ).fetchall()
        links = db.execute(
            "SELECT topic_id, entity_type, entity_id FROM growth_topic_links "
            "WHERE account_id=%s AND topic_id=ANY(%s)",
            (account, [before["auth_topic_id"], before["permission_topic_id"]]),
        ).fetchall()
        export_rows = db.execute(
            "SELECT snapshot_seq, json_payload, markdown_text FROM growth_exports "
            "WHERE account_id=%s AND export_id=%s",
            (account, before["export_id"]),
        ).fetchall()
        download_rows = db.execute(
            "SELECT snapshot_seq, json_payload, markdown_text FROM growth_exports "
            "WHERE account_id=%s AND export_id=%s",
            (account, after["download_export_id"]),
        ).fetchall()
    with sqlite3.connect(args.workspace / ".ahadiff" / "growth.sqlite") as db:
        local_project = db.execute(
            "SELECT count(*) FROM projects WHERE project_id=?",
            (exported["tables"]["projects"][0]["project_id"],),
        ).fetchone()[0]
        local_capture = db.execute(
            "SELECT snapshot_id FROM local_development_event_captures "
            "WHERE account_id=? AND event_id=?",
            (account, before["event_id"]),
        ).fetchone()
    expected_links = {
        (before["auth_topic_id"], "note", before["old_note_id"]),
        (before["permission_topic_id"], "note", before["new_note_id"]),
    }
    actual_links = {(str(row[0]), row[1], str(row[2])) for row in links}
    exported_notes = {item["note_id"]: item for item in exported["tables"]["notes"]}
    checks = {
        "installed_ui_before_after": before["status"] == after["status"] == "PASS",
        "same_candidate_reinstalled": reinstall["status"] == "PASS"
        and reinstall["installer_exit_code"] == 0
        and reinstall["installed_executable_sha256_before"]
        == reinstall["installed_executable_sha256_after"]
        == before["executable_sha256"] == after["executable_sha256"],
        "workspace_untouched_by_reinstall": reinstall["workspace_db_sha256_before"]
        == reinstall["workspace_db_sha256_after"],
        "four_topics": len(topics_by_id) == 4
        and topics_by_id[before["auth_topic_id"]][1:3] == ("身份认证", "active")
        and topics_by_id[before["permission_topic_id"]][1:4]
        == ("资源授权", "active", 3)
        and str(topics_by_id[before["auth_topic_id"]][4]) == before["new_topic_id"]
        and str(topics_by_id[before["permission_topic_id"]][4]) == before["new_topic_id"],
        "old_alias": len(aliases) == 1
        and str(aliases[0][1]) == before["new_topic_id"],
        "original_notes": len(notes_by_id) == 2
        and notes_by_id[before["old_note_id"]][2] == before["old_note_text"]
        and notes_by_id[before["new_note_id"]][2] == before["new_note_text"]
        and all(row[3] is None for row in notes_by_id.values()),
        "explicit_split_links": actual_links == expected_links,
        "export_original_notes": len(exported_notes) == 2
        and exported_notes[before["old_note_id"]]["content_text"]
        == before["old_note_text"]
        and exported_notes[before["new_note_id"]]["content_text"]
        == before["new_note_text"],
        "export_matches_cloud": len(export_rows) == 1
        and export_rows[0][1] == exported
        and export_rows[0][2] == markdown,
        "ui_download_matches_cloud": len(download_rows) == 1
        and download_rows[0][1] == downloaded
        and download_rows[0][2] == downloaded_markdown
        and after["checks"]["json_download"]
        and after["checks"]["markdown_download"],
        "markdown_readable": before["old_note_text"] in markdown
        and before["new_note_text"] in markdown,
        "local_project_and_capture_survive": local_project == 1
        and local_capture == (before["snapshot_id"],),
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "installed_windows_local_cloud_rehearsal",
        "account_id": account,
        "old_topic_id": before["old_topic_id"],
        "new_topic_id": before["new_topic_id"],
        "auth_topic_id": before["auth_topic_id"],
        "permission_topic_id": before["permission_topic_id"],
        "old_note_id": before["old_note_id"],
        "new_note_id": before["new_note_id"],
        "export_id": before["export_id"],
        "export_snapshot_seq": export_rows[0][0] if export_rows else None,
        "download_export_id": after["download_export_id"],
        "download_snapshot_seq": download_rows[0][0] if download_rows else None,
        "export_json_file_sha256": hashlib.sha256(args.export_json.read_bytes()).hexdigest(),
        "export_markdown_file_sha256": hashlib.sha256(args.export_markdown.read_bytes()).hexdigest(),
        "download_json_file_sha256": hashlib.sha256(args.download_json.read_bytes()).hexdigest(),
        "download_markdown_file_sha256": hashlib.sha256(
            args.download_markdown.read_bytes(),
        ).hexdigest(),
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result["status"] != "PASS":
        raise AssertionError(f"主题、导出或重装状态不一致：{checks}")
    print(f"S04_TOPIC_EXPORT_VERIFY_PASS {before['export_id']}")


if __name__ == "__main__":
    main()
