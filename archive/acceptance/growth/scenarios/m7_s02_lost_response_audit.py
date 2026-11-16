"""Audit one installed-UI note creation after the server response was dropped."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    ui: dict[str, Any] = json.loads(
        (run / "lost-response-v2.json").read_text(encoding="utf-8")
    )
    account = ui["account_id"]
    note = ui["first_request"]["note_id"]
    operation = ui["first_request"]["operation_id"]
    with psycopg.connect(args.dsn, row_factory=dict_row) as db:
        notes = db.execute(
            "SELECT note_id, revision, content_text, content_hash FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account, note)
        ).fetchall()
        versions = db.execute(
            "SELECT revision, content_text FROM growth_note_revisions "
            "WHERE account_id=%s AND note_id=%s", (account, note)
        ).fetchall()
        operations = db.execute(
            "SELECT status_code, response FROM growth_sync_operations "
            "WHERE account_id=%s AND operation_id=%s", (account, operation)
        ).fetchall()
        events = db.execute(
            "SELECT event_type FROM growth_change_feed WHERE account_id=%s "
            "AND entity_id=%s", (account, note)
        ).fetchall()
    checks = {
        "ui_retry_pass": ui["status"] == "PASS" and all(ui["checks"].values()),
        "server_committed_before_drop": ui["committed_status"] == 201,
        "same_original_ids": ui["first_request"] == ui["retry_request"],
        "same_business_result": ui["committed_result"] == ui["retry_result"],
        "one_target_note": len(notes) == 1 and notes[0]["revision"] == 1
        and notes[0]["content_text"] == ui["text"]
        and notes[0]["content_hash"] == hashlib.sha256(ui["text"].encode()).hexdigest(),
        "one_revision": len(versions) == 1 and versions[0]["revision"] == 1
        and versions[0]["content_text"] == ui["text"],
        "one_idempotency_result": len(operations) == 1
        and operations[0]["status_code"] == 201
        and operations[0]["response"] == ui["retry_result"],
        "one_creation_event": sum(item["event_type"] == "note_saved"
                                  for item in events) == 1,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    (run / "lost-response-result.json").write_text(
        json.dumps({"status": status, "stage_status": "BLOCKED",
                    "mode": "installed_windows_local_cloud_rehearsal",
                    "note_id": note, "operation_id": operation,
                    "checks": checks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    earlier_failure = (
        "首次脚本用 JSON 字段顺序比较返回值，造成"
        "[误判 FAIL](lost-response.json)；修正为字段比较后用新笔记重跑。"
        if (run / "lost-response.json").is_file() else ""
    )
    (run / "lost-response-report.md").write_text(
        "# S02 创建笔记后响应丢失预演\n\n"
        "安装版首次请求创建指定 N-base 笔记后，测试代理等待服务端提交完成，"
        "再丢弃返回给界面的响应；同一界面保留输入与 operation_id/note_id，"
        "重试取得相同的业务结果。"
        f"独立 PostgreSQL 审计 {sum(checks.values())}/{len(checks)} 项："
        f"**{status}**。[UI 记录](lost-response-v2.json)及"
        "[机读结果](lost-response-result.json)留存原操作、单条笔记、单个版本"
        "和单条创建事件。" + earlier_failure +
        "此笔记与前一冲突预演的笔记不同，且只在 Windows/本机云运行，"
        "不能合并算作 S02 正式链。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(checks)
    print(f"S02_LOST_RESPONSE_AUDIT_PASS {sum(checks.values())}/{len(checks)}")


if __name__ == "__main__":
    main()
