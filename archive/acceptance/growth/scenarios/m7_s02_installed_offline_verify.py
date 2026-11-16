"""Check installed Electron offline observations against the persisted SQLite queue."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-queue", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    ui: dict[str, Any] = json.loads(
        (run / "electron-offline.json").read_text(encoding="utf-8")
    )
    source: dict[str, Any] = json.loads(
        args.source_queue.read_text(encoding="utf-8")
    )
    fixture = run / "fixture" / "offline-growth.sqlite"
    fixture_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    with sqlite3.connect(fixture) as conn:
        conn.row_factory = sqlite3.Row
        fixture_outbox = [
            (row["operation_id"], row["state"])
            for row in conn.execute(
                "SELECT operation_id, state FROM sync_outbox ORDER BY rowid"
            )
        ]
    with sqlite3.connect(run / "workspace" / ".ahadiff" / "growth.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        operations = [dict(row) for row in conn.execute(
            "SELECT operation_id, method, path, payload_json, state "
            "FROM sync_outbox ORDER BY rowid"
        )]
        note_drafts = [dict(row) for row in conn.execute(
            "SELECT note_id, operation_id, content_text, state FROM sync_note_drafts"
        )]
        task_drafts = [dict(row) for row in conn.execute(
            "SELECT task_id, latest_operation_id, content_text, state "
            "FROM sync_task_drafts"
        )]
        answers = [dict(row) for row in conn.execute(
            "SELECT attempt_id, operation_id, answer_text, state "
            "FROM sync_pending_attempts"
        )]
    source_operations = source["operations"]
    for item in operations:
        item["payload"] = json.loads(item.pop("payload_json"))
    ids = ui["expected_ids"]
    ui_checks = ui["checks"]
    checks = {
        "same_candidate": ui["installer_sha256"] == args.candidate_sha256.lower(),
        "source_fixture_has_same_pending_ids": fixture_outbox == [
            (item["operation_id"], "pending") for item in source["operations"]
        ],
        "two_installed_launches": ui["status"] == "PASS"
        and set(ui_checks) == {"launch_1", "launch_2"}
        and all(all(checks.values()) for checks in ui_checks.values()),
        "three_original_operations_pending": len(operations) == len(source_operations) == 3
        and all(
            actual == expected
            for actual, expected in zip(operations, source_operations, strict=True)
        ),
        "note_original_pending": len(note_drafts) == 1
        and note_drafts[0]["note_id"] == ids["note_id"]
        and note_drafts[0]["operation_id"] == ids["note_operation_id"]
        and note_drafts[0]["content_text"] == source["note_drafts"][0]["content_text"]
        and note_drafts[0]["state"] == "pending",
        "answer_original_pending": len(answers) == 1
        and answers[0]["attempt_id"] == ids["attempt_id"]
        and answers[0]["operation_id"] == ids["answer_operation_id"]
        and answers[0]["answer_text"] == source["pending_answers"][0]["answer_text"]
        and answers[0]["state"] == "pending",
        "current_draft_matches_answer": len(task_drafts) == 1
        and task_drafts[0]["task_id"] == ids["task_id"]
        and task_drafts[0]["content_text"] == answers[0]["answer_text"]
        and task_drafts[0]["state"] == "pending",
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    result = {
        "run_id": run.name,
        "status": status,
        "stage_status": "BLOCKED",
        "mode": "replay_not_live",
        "candidate_sha256": args.candidate_sha256.lower(),
        "fixture_sha256": fixture_sha256,
        "source_queue": str(args.source_queue),
        "checks": checks,
        "operation_ids": [item["operation_id"] for item in operations],
    }
    (run / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run / "report.md").write_text(
        "# M3 最新候选安装版离线恢复预演\n\n"
        f"候选安装包 SHA-256：`{args.candidate_sha256.lower()}`。使用早期 Chromium 回放"
        "留下的隔离 SQLite 缓存，在独立 Git 工作区启动同一安装版 Electron 两次；"
        "每次经界面读取离线笔记、本机草稿、待提交回答和同步阻断状态。\n\n"
        f"安装版与 SQLite 七项复核：**{status}**。界面观察见 "
        "[Electron 记录](electron-offline.json)，原操作 ID 与三份正文由"
        "[机读结果](result.json)对应[原始 SQLite 素材](fixture/offline-growth.sqlite)"
        "及源队列逐项核对。失败前驱 "
        "[v1](../m3-installed-offline-69320303-20260926-v1/report.md)、"
        "[v2](../m3-installed-offline-69320303-20260926-v2/report.md)、"
        "[v3](../m3-installed-offline-69320303-20260926-v3/report.md)、"
        "[v4](../m3-installed-offline-69320303-20260926-v4/report.md) 和 "
        "[v5](../m3-installed-offline-69320303-20260926-v5/report.md) "
        "保留。此预演未生成真实模型卡片、未连接外部云或 macOS；不计 S02 正式步骤。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(checks)
    print(run)


if __name__ == "__main__":
    main()
