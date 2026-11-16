"""Independently audit the installed two-Windows S02 conflict rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def local_state(path: Path, account: str, note: str) -> dict[str, Any]:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        identity = db.execute(
            "SELECT device_id, cursor FROM sync_accounts WHERE account_id=?", (account,)
        ).fetchone()
        cached = db.execute(
            "SELECT revision, payload_json FROM sync_entities WHERE account_id=? "
            "AND entity_type='note' AND entity_id=?", (account, note)
        ).fetchone()
        drafts = db.execute(
            "SELECT operation_id, base_revision, content_text, state "
            "FROM sync_note_drafts WHERE account_id=? AND note_id=? ORDER BY rowid",
            (account, note),
        ).fetchall()
        outbox = db.execute(
            "SELECT operation_id, method, path, state FROM sync_outbox "
            "WHERE account_id=? ORDER BY rowid", (account,)
        ).fetchall()
    return {
        "sqlite_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "device_id": identity["device_id"] if identity else None,
        "cursor": identity["cursor"] if identity else None,
        "note_revision": cached["revision"] if cached else None,
        "note_text": json.loads(cached["payload_json"])["content_text"] if cached else None,
        "drafts": [dict(row) for row in drafts],
        "outbox": [dict(row) for row in outbox],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    phases = ["create", "bootstrap", "offline-edit", "online-edit",
              "conflict-merge", "verify"]
    records = {name: read_json(run / f"{name}.json") for name in phases}
    created, offline, online, merged, final = (
        records[name] for name in ["create", "offline-edit", "online-edit",
                                      "conflict-merge", "verify"]
    )
    account, note = created["account_id"], created["note_id"]
    base, mac, win, resolved = (
        created[key] for key in ["base_text", "offline_text", "online_text", "merged_text"]
    )
    device_a = local_state(run / "device-a/.ahadiff/growth.sqlite", account, note)
    device_b = local_state(run / "device-b/.ahadiff/growth.sqlite", account, note)
    with psycopg.connect(args.dsn, row_factory=dict_row) as db:
        notes = db.execute(
            "SELECT note_id, topic_id, revision, content_text, content_hash "
            "FROM growth_notes WHERE account_id=%s AND note_id=%s", (account, note)
        ).fetchall()
        versions = db.execute(
            "SELECT revision, content_text, source_conflict_ids "
            "FROM growth_note_revisions WHERE account_id=%s AND note_id=%s "
            "ORDER BY revision", (account, note)
        ).fetchall()
        conflicts = db.execute(
            "SELECT conflict_id, base_revision, observed_server_revision, proposed_text, "
            "resolved_revision FROM growth_note_conflicts WHERE account_id=%s "
            "AND note_id=%s", (account, note)
        ).fetchall()
        operations = db.execute(
            "SELECT operation_id, status_code, response FROM growth_sync_operations "
            "WHERE account_id=%s", (account,)
        ).fetchall()
        events = db.execute(
            "SELECT event_type, revision FROM growth_change_feed WHERE account_id=%s "
            "AND entity_id=%s ORDER BY change_seq", (account, note)
        ).fetchall()
    versions_json = [{**row, "source_conflict_ids": [str(item)
                      for item in row["source_conflict_ids"]]} for row in versions]
    conflicts_json = [{**row, "conflict_id": str(row["conflict_id"])}
                      for row in conflicts]
    operations_by_id = {str(row["operation_id"]): row for row in operations}
    offline_id = offline["operation_ids"]["offline"]
    online_id = online["operation_ids"]["online"]
    merge_id = merged["operation_ids"]["merge"]
    checks = {
        "six_installed_phases_pass": all(record["status"] == "PASS"
                                         for record in records.values()),
        "one_candidate": all(record["candidate_sha256"]
                             == args.candidate_sha256.lower()
                             for record in records.values()),
        "two_distinct_windows_devices": created["device_b_id"] == device_b["device_id"]
        and records["bootstrap"]["device_a_id"] == device_a["device_id"]
        and device_a["device_id"] != device_b["device_id"],
        "outage_and_local_pending": offline["checks"]["cloud_stopped"]
        and offline["checks"]["offline_pending"]
        and any(row["operation_id"] == offline_id and row["state"] == "conflict"
                and row["content_text"] == mac for row in device_b["drafts"]),
        "one_note_final_revision_three": len(notes) == 1
        and notes[0]["revision"] == 3 and notes[0]["content_text"] == resolved
        and notes[0]["content_hash"] == hashlib.sha256(resolved.encode()).hexdigest(),
        "three_original_versions": [row["revision"] for row in versions] == [1, 2, 3]
        and [row["content_text"] for row in versions] == [base, win, resolved],
        "conflict_retains_offline_branch": len(conflicts) == 1
        and conflicts[0]["base_revision"] == 1
        and conflicts[0]["observed_server_revision"] == 2
        and conflicts[0]["proposed_text"] == mac
        and conflicts[0]["resolved_revision"] == 3,
        "merge_links_conflict": len(conflicts) == 1
        and versions_json[2]["source_conflict_ids"] == [str(conflicts[0]["conflict_id"])],
        "conflict_visible_in_installed_ui": merged["checks"]["conflict_reported"]
        and merged["checks"]["three_texts_visible"]
        and all(text in merged["conflict_originals"] for text in [base, mac, win]),
        "original_operation_results": offline_id in operations_by_id
        and operations_by_id[offline_id]["status_code"] == 409
        and online_id in operations_by_id
        and operations_by_id[online_id]["status_code"] == 200
        and merge_id in operations_by_id
        and operations_by_id[merge_id]["status_code"] == 200,
        "device_a_final_cache": device_a["note_revision"] == 3
        and device_a["note_text"] == resolved,
        "device_b_final_cache": device_b["note_revision"] == 3
        and device_b["note_text"] == resolved,
        "outbox_history": any(row["operation_id"] == online_id
                              and row["state"] == "acked" for row in device_a["outbox"])
        and any(row["operation_id"] == offline_id and row["state"] == "conflict"
                for row in device_b["outbox"])
        and any(row["operation_id"] == merge_id and row["state"] == "acked"
                for row in device_b["outbox"]),
        "single_note_creation_event": sum(row["event_type"] == "note_saved"
                                          for row in events) == 1,
        "final_ui_sync": final["checks"]["final_text_on_a"]
        and merged["checks"]["merge_revision_three"],
    }
    db_evidence = {
        "account_id": account, "topic_id": created["topic_id"], "note_id": note,
        "notes": [{**row, "note_id": str(row["note_id"]),
                   "topic_id": str(row["topic_id"])} for row in notes],
        "versions": versions_json, "conflicts": conflicts_json,
        "operations": [{"operation_id": str(row["operation_id"]),
                        "status_code": row["status_code"]} for row in operations],
        "events": events, "device_a": device_a, "device_b": device_b,
    }
    (run / "db-evidence.json").write_text(
        json.dumps(db_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    (run / "result.json").write_text(
        json.dumps({"status": status, "stage_status": "BLOCKED",
                    "mode": "two_windows_devices_local_cloud_rehearsal",
                    "candidate_sha256": args.candidate_sha256.lower(),
                    "checks": checks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run / "report.md").write_text(
        "# S02 双 Windows 设备笔记冲突预演\n\n"
        f"候选安装包 SHA-256：`{args.candidate_sha256.lower()}`。"
        "使用两份安装版工作区与本机隔离 PostgreSQL；设备 B 在真实云服务停机时"
        "排队 N-mac，设备 A 上线写入 N-win，B 恢复后收到固定 409，在安装版"
        "看到 N-base/N-mac/N-win 并手工提交 N-merged。A 再次同步读取 revision 3。\n\n"
        f"{sum(checks.values())}/{len(checks)} 项独立 UI、SQLite、PostgreSQL 审计："
        f"**{status}**。[机读结果](result.json)和[数据库摘录](db-evidence.json)"
        "保留正文、修订、冲突、操作 ID 及两端本机状态。六段原始 UI 回执为"
        " `create.json` 至 `verify.json`。初始笔记未关联真实模型卡片；"
        "两设备均为 Windows，云端在本机，且未注入创建响应丢失。"
        "故不计 S02 正式步骤，发布情境仍 BLOCKED。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(checks)
    print(f"S02_CONFLICT_AUDIT_PASS {sum(checks.values())}/{len(checks)}")


if __name__ == "__main__":
    main()
