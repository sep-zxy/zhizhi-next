"""Audit an installed S03 module-linked card rehearsal across SQLite and PostgreSQL."""

from __future__ import annotations

import argparse
import json
import sqlite3
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
    prepared = load(run / "preparation.json")
    ui = load(run / "artifacts" / "card-ui.json")
    provider = load(run / "provider-request.json")
    source = run.parent / prepared["source_run_id"]
    module_run = run.parent / prepared["module_run_id"]
    source_result = load(source / "result.json")
    module_result = load(module_run / "result.json")
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM growth_tasks WHERE task_id=?", (ui["task_id"],)
        ).fetchone()
        local = dict(row) if row else None
    with connect(args.dsn) as conn:
        task = conn.execute(
            "SELECT opportunity_id, topic_id, question, source_refs, module_id, "
            "module_index_revision FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (uuid.UUID(prepared["account_id"]), uuid.UUID(ui["task_id"])),
        ).fetchone()
        module = conn.execute(
            "SELECT snapshot_id, index_revision, name, locked, member_paths "
            "FROM growth_modules WHERE account_id=%s AND module_id=%s",
            (uuid.UUID(prepared["account_id"]), uuid.UUID(prepared["module_id"])),
        ).fetchone()
        feed = conn.execute(
            "SELECT event_type, payload FROM growth_change_feed "
            "WHERE account_id=%s AND entity_type='task' AND entity_id=%s",
            (uuid.UUID(prepared["account_id"]), uuid.UUID(ui["task_id"])),
        ).fetchall()
    refs = set(json.loads(local["source_refs_json"])) if local else set()
    checks = {
        "same_candidate_sources": source_result["checks"]["S03-04"]
        and module_result["status"] == "PASS"
        and source_result["proof"]["windows_app"]
        and prepared["candidate_sha256"] == module_result["candidate_sha256"],
        "installed_ui_approved_source": ui["status"] == "observed_pending_audit"
        and ui["preview"]["payload_sha256"] == provider["prompt_sha256"]
        and set(ui["preview"]["source_ref_ids"]) == set(provider["source_refs"].values())
        and provider["has_native_schema"] and not provider["stream"],
        "installed_card_displays_module_and_ttl": "订单计价" in ui["card_text"]
        and "访问时检查 TTL" in ui["card_text"],
        "local_card_module_and_evidence": local is not None
        and local["opportunity_id"] == ui["opportunity_id"]
        and local["topic_id"] == ui["topic_id"]
        and local["module_id"] == prepared["module_id"]
        and local["module_index_revision"] == prepared["module_index_revision"]
        and refs == set(ui["preview"]["source_ref_ids"]),
        "cloud_card_same_link": task is not None and local is not None
        and str(task["opportunity_id"]) == local["opportunity_id"]
        and str(task["topic_id"]) == local["topic_id"]
        and task["question"] == local["question"]
        and set(task["source_refs"]) == refs
        and str(task["module_id"]) == prepared["module_id"]
        and task["module_index_revision"].strip() == prepared["module_index_revision"],
        "locked_module_same_snapshot": module is not None
        and module["locked"] and module["name"] == "订单计价"
        and str(module["snapshot_id"]) == prepared["snapshot_id"]
        and module["index_revision"].strip() == prepared["module_index_revision"]
        and "src/demo/orders/PriceCache.java" in module["member_paths"],
        "sync_receipt_and_feed": ui["final_sync"]["outbox"]["pending"] == 0
        and ui["final_sync"]["outbox"]["conflict"] == 0
        and len(feed) == 1 and feed[0]["event_type"] == "task_created"
        and feed[0]["payload"]["module_id"] == prepared["module_id"],
    }
    result = {
        "run_id": run.name, "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "replay_not_live", "stage_status": "BLOCKED",
        "scope": "Windows 安装版 S03 模块关联 TTL 短卡片、本机及隔离 PostgreSQL 回执",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "module_run_id": prepared["module_run_id"],
        "task_id": ui["task_id"], "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
