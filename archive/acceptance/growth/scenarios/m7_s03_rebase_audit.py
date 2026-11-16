"""Audit the installed S03 rebase/reindex rehearsal without promoting it to live acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    prep = load(run / "preparation.json")
    ui = load(run / "artifacts" / "rebase-ui.json")
    source = run.parent / prep["source_run_id"]
    source_audit = load(source / "audit.json")
    repo = Path(prep["workspace"])
    installed_exe = run / "installed" / "工程成长伴侣.exe"
    installer = run.parents[2] / "dist" / "archive" / (
        "工程成长伴侣 Setup 0.1.0-" + prep["candidate_sha256"][:8] + ".exe"
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    with sqlite3.connect(repo / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        snapshots = [dict(row) for row in db.execute(
            "SELECT snapshot_id, head_sha, effective_tree_hash FROM snapshots "
            "WHERE snapshot_id IN (?, ?)",
            (ui["old_snapshot_id"], ui["new_snapshot"]["snapshot_id"]),
        )]
        task = db.execute(
            "SELECT module_id, module_index_revision, source_refs_json "
            "FROM growth_tasks WHERE task_id=?", (prep["task_id"],),
        ).fetchone()
        attempts = [dict(row) for row in db.execute(
            "SELECT attempt_id, answer_text, feedback_json FROM learning_attempts "
            "WHERE task_id=? ORDER BY created_at,rowid", (prep["task_id"],),
        )]
        old_ref = db.execute(
            "SELECT snapshot_id, blob_hash, content FROM source_refs "
            "WHERE source_ref_id=?", (prep["old_source_ref_id"],),
        ).fetchone()
        new_ref = db.execute(
            "SELECT source_ref_id, blob_hash FROM source_refs "
            "WHERE snapshot_id=? AND relative_path='src/demo/orders/PriceCache.java'",
            (ui["new_snapshot"]["snapshot_id"],),
        ).fetchone()
    account = uuid.UUID(prep["account_id"])
    module_id = uuid.UUID(prep["module_id"])
    with connect(args.dsn) as conn:
        module = conn.execute(
            "SELECT snapshot_id, index_revision, effective_tree_hash, name, "
            "member_paths, locked, pending_adjustment, revision "
            "FROM growth_modules WHERE account_id=%s AND module_id=%s",
            (account, module_id),
        ).fetchone()
        cloud_snapshot = conn.execute(
            "SELECT head_sha, effective_tree_hash FROM growth_snapshots "
            "WHERE account_id=%s AND snapshot_id=%s",
            (account, uuid.UUID(ui["new_snapshot"]["snapshot_id"])),
        ).fetchone()
        cloud_task = conn.execute(
            "SELECT module_id, module_index_revision, source_refs FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(prep["task_id"])),
        ).fetchone()
        cloud_attempts = conn.execute(
            "SELECT attempt_id, answer_text, feedback FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s ORDER BY created_at",
            (account, uuid.UUID(prep["task_id"])),
        ).fetchall()
        module_events = conn.execute(
            "SELECT event_type, revision FROM growth_change_feed "
            "WHERE account_id=%s AND entity_type='module' AND entity_id=%s "
            "ORDER BY change_seq", (account, module_id),
        ).fetchall()
    new_snapshot_id = ui["new_snapshot"]["snapshot_id"]
    old_snapshot_id = ui["old_snapshot_id"]
    refs = set(ui["old_chain"]["source_refs"])
    checks = {
        "installed_candidate_and_source_provenance": source_audit["status"] == "PASS"
        and prep["cross_candidate"] == (
            source_audit["candidate_sha256"] != prep["candidate_sha256"]
        ) and ui["status"] == "observed_pending_audit"
        and hashlib.sha256(installer.read_bytes()).hexdigest()
        == prep["candidate_sha256"]
        and hashlib.sha256(installed_exe.read_bytes()).hexdigest()
        == ui["executable_sha256"],
        "real_git_rebase_and_new_snapshot": head == prep["feature_after_rebase"]
        and head != prep["feature_before_rebase"]
        and new_snapshot_id != old_snapshot_id
        and ui["new_snapshot"]["head_sha"] == head
        and len(snapshots) == 2
        and {row["snapshot_id"] for row in snapshots}
        == {old_snapshot_id, new_snapshot_id},
        "cloud_new_snapshot_same_id_and_hash": cloud_snapshot is not None
        and cloud_snapshot["head_sha"].strip() == head
        and cloud_snapshot["effective_tree_hash"].strip()
        == ui["new_index"]["effective_tree_hash"]
        and ui["snapshot_sync"]["outbox"]["pending"] == 0
        and ui["snapshot_sync"]["outbox"]["conflict"] == 0,
        "locked_module_reindexed_same_id": module is not None
        and str(module["snapshot_id"]) == new_snapshot_id
        and module["index_revision"].strip()
        == ui["new_index"]["index_revision"]
        and module["effective_tree_hash"].strip()
        == ui["new_index"]["effective_tree_hash"]
        and module["locked"] and module["name"] == "订单计价"
        and module["pending_adjustment"] is None
        and module["revision"] == ui["reindex"]["revision"]
        and set(module["member_paths"])
        == {node["file_path"] for node in ui["new_index"]["flow"]["nodes"]},
        "module_event_reindexed": [row["event_type"] for row in module_events]
        == ["module_mapped", "module_user_edited", "module_reindexed"]
        and [row["revision"] for row in module_events] == [1, 2, 3],
        "old_source_ref_immutable": old_ref is not None and new_ref is not None
        and old_ref["snapshot_id"] == old_snapshot_id
        and old_ref["blob_hash"] == prep["old_source_hash"]
        and hashlib.sha256(old_ref["content"]).hexdigest()
        == prep["old_source_hash"]
        and new_ref["source_ref_id"] != prep["old_source_ref_id"],
        "old_card_and_two_attempts_preserved": task is not None
        and task["module_id"] == prep["module_id"]
        and task["module_index_revision"] == prep["module_index_revision"]
        and set(json.loads(task["source_refs_json"])) == refs
        and len(attempts) == len(cloud_attempts) == 2
        and [row["attempt_id"] for row in attempts]
        == ui["old_chain"]["attempt_ids"]
        and all(row["feedback_json"] for row in attempts)
        and all(row["feedback"] for row in cloud_attempts)
        and [str(row["attempt_id"]) for row in cloud_attempts]
        == ui["old_chain"]["attempt_ids"]
        and [row["answer_text"] for row in attempts]
        == [row["answer_text"] for row in cloud_attempts],
        "cloud_old_task_not_rewritten": cloud_task is not None
        and str(cloud_task["module_id"]) == prep["module_id"]
        and cloud_task["module_index_revision"].strip()
        == prep["module_index_revision"]
        and set(cloud_task["source_refs"]) == refs,
    }
    result = {
        "run_id": run.name, "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "replay_not_live", "stage_status": "BLOCKED",
        "scope": "S03 Windows 安装版真实 rebase、模块重建索引与旧卡引用不变",
        "candidate_sha256": prep["candidate_sha256"],
        "cross_candidate": prep["cross_candidate"], "source_run_id": prep["source_run_id"],
        "task_id": prep["task_id"], "module_id": prep["module_id"],
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
