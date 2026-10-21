"""Verify installed UI offline writes and restart recovery in isolated SQLite."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def pending_operations(path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT operation_id, method, path, payload_json, state "
            "FROM sync_outbox WHERE state='pending' ORDER BY rowid"
        ).fetchall()
    return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    ui: dict[str, Any] = json.loads(
        (run / "electron-offline-write.json").read_text(encoding="utf-8")
    )
    fixture = run / "fixture" / "offline-growth.sqlite"
    source_pending = pending_operations(fixture)
    current = run / "workspace" / ".ahadiff" / "growth.sqlite"
    pending = pending_operations(current)
    with sqlite3.connect(current) as conn:
        conn.row_factory = sqlite3.Row
        draft = conn.execute(
            "SELECT task_id, latest_operation_id, content_text, state "
            "FROM sync_task_drafts WHERE task_id=?", (ui["task_id"],)
        ).fetchone()
        answer = conn.execute(
            "SELECT attempt_id, operation_id, task_id, parent_attempt_id, "
            "answer_text, state FROM sync_pending_attempts WHERE state='pending'"
        ).fetchone()
    draft_receipt = ui["draft_receipt"]
    answer_receipt = ui["answer_receipt"]
    checks = {
        "same_candidate": ui["installer_sha256"] == args.candidate_sha256.lower(),
        "source_has_no_pending_operations": source_pending == [],
        "three_installed_launches": ui["status"] == "PASS"
        and len(ui["checks"]) == 5 and all(ui["checks"].values()),
        "draft_operation_original": len(pending) == 2
        and pending[0]["operation_id"] == draft_receipt["operation_id"]
        and pending[0]["method"] == "PUT"
        and pending[0]["path"] == f"/v1/tasks/{ui['task_id']}/draft"
        and pending[0]["payload"]["content_text"] == ui["draft_text"],
        "answer_operation_original": len(pending) == 2
        and pending[1]["operation_id"] == answer_receipt["operation_id"]
        and pending[1]["method"] == "POST"
        and pending[1]["path"] == f"/v1/tasks/{ui['task_id']}/attempts"
        and pending[1]["payload"]["answer_text"] == ui["answer_text"]
        and pending[1]["payload"]["attempt_id"] == answer_receipt["attempt_id"]
        and pending[1]["payload"]["parent_attempt_id"]
        == ui["parent_attempt_id"],
        "pending_answer_persisted": answer is not None
        and answer["attempt_id"] == answer_receipt["attempt_id"]
        and answer["operation_id"] == answer_receipt["operation_id"]
        and answer["task_id"] == ui["task_id"]
        and answer["parent_attempt_id"] == ui["parent_attempt_id"]
        and answer["answer_text"] == ui["answer_text"]
        and answer["state"] == "pending",
        "current_draft_pending": draft is not None
        and draft["task_id"] == ui["task_id"]
        and draft["latest_operation_id"] == answer_receipt["operation_id"]
        and draft["content_text"] == ui["answer_text"]
        and draft["state"] == "pending",
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    result = {
        "run_id": run.name,
        "status": status,
        "stage_status": "BLOCKED",
        "mode": "replay_not_live",
        "candidate_sha256": args.candidate_sha256.lower(),
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "checks": checks,
        "operation_ids": [item["operation_id"] for item in pending],
    }
    (run / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run / "report.md").write_text(
        "# M7 S02 同候选安装版离线写入预演\n\n"
        f"候选安装包 SHA-256：`{args.candidate_sha256.lower()}`。从此前 M3 回放"
        "的[隔离 SQLite 素材](fixture/offline-growth.sqlite)开始，在安装版 Electron "
        "界面写入情境规定的 A0 原文，重启后载入，再写入情境规定的 A1 原文并再次重启。"
        "全过程不连接成长云。\n\n"
        f"七项 UI/SQLite 检查：**{status}**。[Electron 操作记录]"
        "(electron-offline-write.json)保存两次写入回执、三次启动及 UI 状态；"
        "[机读结果](result.json)核对原操作 ID、两条待同步队列、A0/A1 正文"
        "和父回答关联。素材卡片来自回放而非真实模型，且无 Mac 或外部云；"
        "不计 S02 正式步骤。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(checks)
    print(run)


if __name__ == "__main__":
    main()
