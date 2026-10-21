"""Audit the same-candidate S04 Windows continuation without promoting formal steps."""

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
    chain = read(args.run / "chain.json")
    topic = read(artifacts / "s04-09-11-windows-topic-rehearsal.json")
    finish = artifacts / "s04-07-answer-note-finish-ui.json"
    if not finish.exists():
        finish = artifacts / "s04-07-answer-note-live-ui.json"
    note = read(finish)
    created = read(artifacts / "s04-12-windows-temp-create.json")
    deletion = read(artifacts / "s04-13-windows-deletion-audit.json")
    memory = read(artifacts / "s04-14-windows-reme-delete-rehearsal.json")
    chat = read(artifacts / "s04-14-windows-memory-chat-live-audit.json")
    reinstall = read(artifacts / "s04-15-windows-reinstall.json")
    ui = read(artifacts / "s04-15-windows-reinstall-export-ui.json")
    exported = read(artifacts / ui["download_filenames"][0])
    markdown = (artifacts / ui["download_filenames"][1]).read_text(encoding="utf-8")
    account = uuid.UUID(topic["account_id"])
    with connect(args.dsn) as db:
        stored = db.execute(
            "SELECT json_payload, markdown_text FROM growth_exports "
            "WHERE account_id=%s AND export_id=%s",
            (account, uuid.UUID(ui["download_export_id"])),
        ).fetchone()
    notes = {row["note_id"]: row for row in exported["tables"]["notes"]}
    tasks = {row["task_id"]: row for row in exported["tables"]["tasks"]}
    attempts = {row["attempt_id"]: row for row in exported["tables"]["attempts"]}
    aliases = {(row["alias_topic_id"], row["canonical_topic_id"])
               for row in exported["tables"]["topic_aliases"]}
    links = {(row["topic_id"], row["entity_type"], row["entity_id"])
             for row in exported["tables"]["topic_links"]}
    conversations = [row for row in exported["tables"]["chat_messages"]
                     if row["session_id"] == chat["session_id"]]
    checks = {
        "all_windows_rehearsal_audits_pass": all(read(artifacts / name)["status"] == "PASS"
                                                 for name in (
            "s04-08-windows-reme-context-rehearsal.json",
            "s04-09-11-windows-topic-rehearsal.json",
            "s04-12-windows-offline-audit.json",
            "s04-13-windows-deletion-audit.json",
            "s04-14-windows-reme-delete-rehearsal.json",
            "s04-14-windows-memory-chat-live-audit.json",
        )),
        "same_candidate_reinstalled": reinstall["status"] == "PASS"
        and all(reinstall["checks"].values()) and ui["status"] == "PASS"
        and all(ui["checks"].values())
        and ui["executable_sha256"] == topic["executable_sha256"],
        "download_matches_cloud_snapshot": stored is not None
        and stored["json_payload"] == exported
        and stored["markdown_text"] == markdown,
        "original_notes_and_answer_preserved": notes[chain["note_id"]]["content_text"]
        == "登录成功说明应用识别了用户身份。"
        and notes[note["note_id"]]["content_text"] == note["note_text"]
        and notes[chain["note_id"]]["deleted_at"] is None
        and notes[note["note_id"]]["deleted_at"] is None
        and attempts[note["attempt_id"]]["answer_text"] == note["answer_text"]
        and tasks[chain["task_id"]]["source_refs"] == note["source_refs"]
        and note["note_text"] in markdown,
        "topic_history_and_links_preserved": (chain["topic_id"], chain["new_topic_id"])
        in aliases
        and (topic["auth_topic_id"], "note", chain["note_id"]) in links
        and (topic["permission_topic_id"], "note", note["note_id"]) in links
        and (topic["permission_topic_id"], "task", chain["task_id"]) in links,
        "deleted_note_record_not_active": notes[created["temp_note_id"]]["deleted_at"]
        is not None and deletion["status"] == "PASS" and memory["status"] == "PASS"
        and created["offline_text"] not in markdown,
        "live_chat_persisted_in_export": chat["status"] == "PASS"
        and len(conversations) == 2
        and any(row["role"] == "user" and row["content_text"]
                == "我现在怎样区分登录和权限？" for row in conversations)
        and any(row["role"] == "assistant" and row["provider_name"] == "deepseek"
                and "认证" in row["content_text"] and "授权" in row["content_text"]
                for row in conversations),
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "windows_only_same_candidate_rehearsal_not_formal",
        "formal_s04_steps": "S04-01..S04-07",
        "candidate_sha256": reinstall["installer_sha256"],
        "account_id": str(account), "export_id": ui["download_export_id"],
        "checks": checks,
    }
    target = artifacts / "s04-15-windows-final-audit.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    if result["status"] != "PASS":
        raise AssertionError(f"S04 Windows 收尾审计失败：{checks}")
    print("S04_WINDOWS_FINAL_AUDIT_PASS")


if __name__ == "__main__":
    main()
