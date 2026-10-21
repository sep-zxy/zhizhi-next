"""Verify S04's installed Windows topic setup against Git, SQLite and PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--probe-run", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    source = args.source_run.resolve()
    ui = json.loads((run / "electron-s04.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    probe = json.loads((args.probe_run / "result.json").read_text(encoding="utf-8"))
    workspace = Path(ui["workspace"]).resolve()
    actual_head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    git_status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    with sqlite3.connect(workspace / ".ahadiff" / "growth.sqlite") as local:
        project = local.execute(
            "SELECT name FROM projects WHERE project_id=?", (ui["project_id"],),
        ).fetchone()
        binding = local.execute(
            "SELECT canonical_local_path FROM bindings WHERE binding_id=? "
            "AND project_id=?", (ui["binding_id"], ui["project_id"]),
        ).fetchone()
        feature = local.execute(
            "SELECT start_base_sha FROM features WHERE feature_id=? "
            "AND project_id=?", (ui["feature_id"], ui["project_id"]),
        ).fetchone()
    account = uuid.UUID(ui["account_id"])
    topic_id = uuid.UUID(ui["topic_id"])
    note_id = uuid.UUID(ui["note_id"])
    with connect(args.dsn) as conn:
        cloud_project = conn.execute(
            "SELECT name FROM growth_projects WHERE account_id=%s AND project_id=%s",
            (account, uuid.UUID(ui["project_id"])),
        ).fetchone()
        cloud_feature = conn.execute(
            "SELECT start_base_sha FROM growth_features "
            "WHERE account_id=%s AND feature_id=%s",
            (account, uuid.UUID(ui["feature_id"])),
        ).fetchone()
        topic = conn.execute(
            "SELECT title, status, revision FROM growth_topics "
            "WHERE account_id=%s AND topic_id=%s",
            (account, topic_id),
        ).fetchone()
        note = conn.execute(
            "SELECT topic_id, task_id, content_text, revision FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, note_id),
        ).fetchone()
        topic_count = conn.execute(
            "SELECT count(*) AS total FROM growth_topics WHERE account_id=%s",
            (account,),
        ).fetchone()["total"]
        events = conn.execute(
            "SELECT entity_type, entity_id, event_type FROM growth_change_feed "
            "WHERE account_id=%s AND entity_id IN (%s, %s) ORDER BY change_seq",
            (account, topic_id, note_id),
        ).fetchall()
    response_statuses = {item["path"]: item["status"] for item in ui["responses"]}
    checks = {
        "same_candidate_and_base": manifest["candidate_sha256"]
        == args.candidate_sha256.lower() == probe["candidate_sha256"]
        and manifest["fixture"]["base_sha"] == ui["base_sha"]
        == probe["base_sha"] == actual_head and not git_status,
        "baseline_probe": probe["status"] == "PASS"
        and probe["checks"]["baseline_bob_can_edit"],
        "installed_ui_created_local_project": project is not None
        and project[0] == "document-demo" and binding is not None
        and Path(binding[0]).resolve() == workspace and feature is not None
        and feature[0] == actual_head,
        "approved_project_reached_postgres": cloud_project is not None
        and cloud_project["name"] == "document-demo" and cloud_feature is not None
        and cloud_feature["start_base_sha"].strip() == actual_head,
        "old_topic_created_once": ui["topic_count_before"] == 0
        and ui["topic_count_after"] == topic_count == 1
        and topic is not None and topic["title"] == "登录机制"
        and topic["status"] == "active" and topic["revision"] == 1,
        "old_note_user_text_preserved": note is not None
        and note["topic_id"] == topic_id and note["task_id"] is None
        and note["content_text"] == ui["note_text"]
        == "登录成功说明应用识别了用户身份。" and note["revision"] == 1,
        "installed_ui_actions_and_events": response_statuses.get(
            "/api/growth/local/cloud/topics/create"
        ) == 201 and response_statuses.get(
            f"/api/growth/local/cloud/topics/{topic_id}/notes"
        ) == 201 and {item["entity_id"] for item in events} == {topic_id, note_id},
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    result = {
        "run_id": run.name,
        "status": status,
        "stage_status": "BLOCKED",
        "scope": "S04-01 安装版 Windows 与本机隔离 PostgreSQL 前置历史演练",
        "candidate_sha256": args.candidate_sha256.lower(),
        "source_run_id": source.name,
        "project_id": ui["project_id"],
        "feature_id": ui["feature_id"],
        "topic_id": ui["topic_id"],
        "note_id": ui["note_id"],
        "note_sha256": hashlib.sha256(ui["note_text"].encode()).hexdigest(),
        "checks": checks,
    }
    (run / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (run / "postgres-evidence.json").write_text(
        json.dumps({
            "topic": dict(topic) if topic else None,
            "note": dict(note) if note else None,
            "topic_count": topic_count,
            "events": [dict(item) for item in events],
        }, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8",
    )
    (run / "report.md").write_text(
        "# S04 Windows 安装版旧主题与笔记前置演练\n\n"
        f"候选 SHA-256：`{args.candidate_sha256.lower()}`。使用真实 Git 基线、"
        "安装版界面和本机隔离 PostgreSQL 创建 `document-demo`、旧主题 T-old"
        "“登录机制”及用户手写旧笔记 N-old。\n\n"
        f"七项检查：**{status}**。界面响应见 [UI 证据](electron-s04.json)，"
        "数据库主题、笔记与事件见 [PostgreSQL 证据](postgres-evidence.json)。"
        "本机隔离服务尚不能替代外部云端、模型或 macOS；完整 S04 仍为 BLOCKED。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(checks)
    print(run)


if __name__ == "__main__":
    main()
