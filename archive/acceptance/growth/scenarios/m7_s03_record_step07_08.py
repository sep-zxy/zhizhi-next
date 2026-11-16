"""Record S03's real rebase and locked-module reindex after source audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def artifact(run: Path, name: str) -> dict[str, str]:
    path = run / "artifacts" / name
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--installed-exe", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    repo = run / "fixture/repo"
    manifest = read(run / "manifest.json")
    result = read(run / "result.json")
    prepared = read(run / "artifacts/s03-07-git-preparation.json")
    ui = read(run / "artifacts/s03-07-08-rebase-ui.json")
    chain = read(run / "chain.json")
    if not result["checks"]["S03-06"] or "S03-07" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S03-06 未通过或 rebase 已记录")
    if hashlib.sha256(args.installer.read_bytes()).hexdigest() != manifest["candidate_sha256"] or prepared["candidate_sha256"] != manifest["candidate_sha256"]:
        raise AssertionError("S03 安装包候选不一致")
    if hashlib.sha256(args.installed_exe.read_bytes()).hexdigest() != ui["executable_sha256"] or ui["mode"] != "live_observation" or ui["status"] != "observed_pending_audit":
        raise AssertionError("安装版 UI rebase 观察缺失")
    if git(repo, "rev-parse", "HEAD") != prepared["feature_after_rebase"] or git(repo, "branch", "--show-current") != "feature/price-cache" or git(repo, "status", "--porcelain"):
        raise AssertionError("当前 Git 状态不等于已准备 rebase 结果")
    if git(repo, "rev-parse", prepared["feature_after_rebase"] + "^") != prepared["main_head"]:
        raise AssertionError("feature rebase 后没有以新 main 为父")
    if prepared["feature_before_rebase"] in (prepared["main_head"], prepared["feature_after_rebase"]):
        raise AssertionError("feature 原提交与 rebase 后提交未区分")
    if "订单接口保留原语义" not in git(repo, "show", prepared["main_head"] + ":src/demo/orders/OrderController.java"):
        raise AssertionError("main 上的 Controller 注释提交缺失")
    if ui["new_snapshot"]["head_sha"] != prepared["feature_after_rebase"] or ui["old_snapshot_id"] == ui["new_snapshot"]["snapshot_id"] or not ui["old_card_opened"]:
        raise AssertionError("安装版没有建立 rebase 后独立快照或打开旧卡")
    if ui["new_index"]["snapshot_id"] != ui["new_snapshot"]["snapshot_id"] or ui["new_index"]["index_revision"] == prepared["module_index_revision"]:
        raise AssertionError("CodeGraph 新索引未与新快照绑定")
    if ui["reindex_http_status"] != 200 or ui["reindex"]["module_id"] != prepared["module_id"] or ui["reindex"]["pending_adjustment"]:
        raise AssertionError("锁定模块重新索引失败")
    if ui["old_chain"]["module_index_revision"] != prepared["module_index_revision"] or ui["old_card_after_reindex"]["module_index_revision"] != prepared["module_index_revision"]:
        raise AssertionError("旧卡引用被新模块索引覆盖")
    if ui["old_chain"]["attempt_ids"] != chain["attempt_ids"] or ui["old_card_after_reindex"]["attempt_ids"] != chain["attempt_ids"]:
        raise AssertionError("旧回答父链在 rebase 后丢失")
    if ui["snapshot_sync"]["outbox"]["pending"] != 0 or ui["snapshot_sync"]["outbox"]["conflict"] != 0:
        raise AssertionError("新快照发布有待处理或冲突")

    new_snapshot_id = ui["new_snapshot"]["snapshot_id"]
    with sqlite3.connect(repo / ".ahadiff/growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        snapshots = [dict(row) for row in db.execute(
            "SELECT snapshot_id,head_sha,effective_tree_hash FROM snapshots WHERE snapshot_id IN (?,?)",
            (ui["old_snapshot_id"], new_snapshot_id))]
        old_ref = db.execute("SELECT snapshot_id,blob_hash,content FROM source_refs WHERE source_ref_id=?",
                             (prepared["old_source_ref_id"],)).fetchone()
        new_ref = db.execute("SELECT source_ref_id,blob_hash FROM source_refs WHERE snapshot_id=? "
                             "AND relative_path='src/demo/orders/PriceCache.java'", (new_snapshot_id,)).fetchone()
        task = db.execute("SELECT module_id,module_index_revision,source_refs_json FROM growth_tasks WHERE task_id=?",
                          (prepared["task_id"],)).fetchone()
        attempts = [dict(row) for row in db.execute(
            "SELECT attempt_id,answer_text,feedback_json FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid",
            (prepared["task_id"],))]
    if len(snapshots) != 2 or {row["snapshot_id"] for row in snapshots} != {ui["old_snapshot_id"], new_snapshot_id}:
        raise AssertionError("新旧源码快照没有同时保存")
    if old_ref is None or new_ref is None or old_ref["snapshot_id"] != ui["old_snapshot_id"] or old_ref["blob_hash"] != prepared["old_source_hash"] or hashlib.sha256(old_ref["content"]).hexdigest() != prepared["old_source_hash"] or new_ref["source_ref_id"] == prepared["old_source_ref_id"]:
        raise AssertionError("rebase 后旧 SourceRef 原文或新来源版本不正确")
    if task is None or task["module_id"] != prepared["module_id"] or task["module_index_revision"] != prepared["module_index_revision"] or set(json.loads(task["source_refs_json"])) != set(ui["old_chain"]["source_refs"]):
        raise AssertionError("旧卡模块版本或 SourceRef 被改写")
    if len(attempts) != 2 or [row["attempt_id"] for row in attempts] != chain["attempt_ids"] or not all(row["feedback_json"] for row in attempts):
        raise AssertionError("A1/A2 原文反馈在 rebase 后丢失")

    account = uuid.UUID(prepared["account_id"])
    module_id = uuid.UUID(prepared["module_id"])
    with connect(args.dsn) as conn:
        module = conn.execute("SELECT snapshot_id,index_revision,effective_tree_hash,name,member_paths,locked,pending_adjustment,revision FROM growth_modules WHERE account_id=%s AND module_id=%s", (account, module_id)).fetchone()
        cloud_snapshot = conn.execute("SELECT head_sha,effective_tree_hash FROM growth_snapshots WHERE account_id=%s AND snapshot_id=%s", (account, uuid.UUID(new_snapshot_id))).fetchone()
        cloud_task = conn.execute("SELECT module_id,module_index_revision,source_refs FROM growth_tasks WHERE account_id=%s AND task_id=%s", (account, uuid.UUID(prepared["task_id"]))).fetchone()
        cloud_attempts = conn.execute("SELECT attempt_id,answer_text,feedback FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s ORDER BY created_at", (account, uuid.UUID(prepared["task_id"]))).fetchall()
        module_feed = conn.execute("SELECT event_type,revision FROM growth_change_feed WHERE account_id=%s AND entity_type='module' AND entity_id=%s ORDER BY change_seq", (account, module_id)).fetchall()
        domain = conn.execute("SELECT entity_id,event_type,occurred_at,trace_id FROM growth_domain_events WHERE account_id=%s AND ((entity_type='snapshot' AND entity_id=%s) OR (entity_type='module' AND entity_id=%s AND event_type='module_reindexed')) ORDER BY occurred_at", (account, uuid.UUID(new_snapshot_id), module_id)).fetchall()
    if cloud_snapshot is None or cloud_snapshot["head_sha"].strip() != prepared["feature_after_rebase"] or cloud_snapshot["effective_tree_hash"].strip() != ui["new_index"]["effective_tree_hash"]:
        raise AssertionError("云端新快照与 Git HEAD/索引指纹不一致")
    if module is None or str(module["snapshot_id"]) != new_snapshot_id or module["index_revision"].strip() != ui["new_index"]["index_revision"] or module["effective_tree_hash"].strip() != ui["new_index"]["effective_tree_hash"] or module["name"] != "订单计价" or not module["locked"] or module["pending_adjustment"] or module["revision"] != 3:
        raise AssertionError("锁定模块新版本没有留在同一 ID")
    if set(module["member_paths"]) != {node["file_path"] for node in ui["new_index"]["flow"]["nodes"]}:
        raise AssertionError("新模块边界与 CodeGraph 流不一致")
    if [row["event_type"] for row in module_feed] != ["module_mapped", "module_user_edited", "module_reindexed"] or [row["revision"] for row in module_feed] != [1, 2, 3]:
        raise AssertionError("模块更新业务事件缺失")
    if cloud_task is None or str(cloud_task["module_id"]) != prepared["module_id"] or cloud_task["module_index_revision"].strip() != prepared["module_index_revision"] or set(cloud_task["source_refs"]) != set(ui["old_chain"]["source_refs"]):
        raise AssertionError("云端旧卡被重新索引覆盖")
    if len(cloud_attempts) != 2 or [str(row["attempt_id"]) for row in cloud_attempts] != chain["attempt_ids"] or [row["answer_text"] for row in cloud_attempts] != [row["answer_text"] for row in attempts] or not all(row["feedback"] for row in cloud_attempts):
        raise AssertionError("云端旧 A1/A2/反馈丢失")
    if [row["event_type"] for row in domain] != ["snapshot_saved", "module_reindexed"]:
        raise AssertionError("新快照或模块重索引领域事件缺失")

    audit = {"status": "PASS", "mode": "live_observation", "run_id": run.name,
             "candidate_sha256": manifest["candidate_sha256"], "feature_before_rebase": prepared["feature_before_rebase"],
             "main_head": prepared["main_head"], "feature_after_rebase": prepared["feature_after_rebase"],
             "old_snapshot_id": ui["old_snapshot_id"], "new_snapshot_id": new_snapshot_id,
             "old_index_revision": prepared["module_index_revision"],
             "new_index_revision": ui["new_index"]["index_revision"],
             "module_id": prepared["module_id"], "old_task_id": prepared["task_id"],
             "cloud_kind": "isolated_local_postgresql", "domain_events": [row["event_type"] for row in domain]}
    write(run / "artifacts/s03-07-08-live-audit.json", audit)
    first_names = ["s03-07-git-preparation.json", "s03-07-08-rebase-ui.json", "s03-07-08-live-audit.json"]
    git_start = git(repo, "show", "-s", "--format=%cI", prepared["feature_before_rebase"])
    snapshot_event, module_event = domain
    steps = [
        {"step_id": "S03-07", "started_at": git_start,
         "finished_at": snapshot_event["occurred_at"].isoformat(),
         "trace_id": str(snapshot_event["trace_id"]),
         "action": "真实提交 feature 与 main 后 rebase；安装版捕获新快照并打开旧卡",
         "expected": "Git HEAD/部分行号变化，旧卡、旧源码引用、A1/A2 与反馈保持可追溯",
         "actual": {"feature_before_rebase": prepared["feature_before_rebase"],
                    "main_head": prepared["main_head"], "feature_after_rebase": prepared["feature_after_rebase"],
                    "old_snapshot_id": ui["old_snapshot_id"], "new_snapshot_id": new_snapshot_id,
                    "old_task_id": prepared["task_id"]}},
        {"step_id": "S03-08", "started_at": snapshot_event["occurred_at"].isoformat(),
         "finished_at": module_event["occurred_at"].isoformat(),
         "trace_id": str(module_event["trace_id"]),
         "action": "安装版对新快照建立 CodeGraph 索引并重新索引已锁定订单计价模块",
         "expected": "同一模块 ID 保留名称与边界、索引版本更新，旧卡保留旧索引引用",
         "actual": {"module_id": prepared["module_id"], "new_snapshot_id": new_snapshot_id,
                    "old_index_revision": prepared["module_index_revision"],
                    "new_index_revision": ui["new_index"]["index_revision"],
                    "module_revision": module["revision"]}},
    ]
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        for item in steps:
            record = {"schema_version": 1, "run_id": run.name, "case_id": "S03",
                      "status": "PASS", "device_id": manifest["environment"]["windows_node_id"],
                      "artifacts": [artifact(run, name) for name in first_names], **item}
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for row in domain:
            output.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
    chain.update({"new_snapshot_id": new_snapshot_id,
                  "new_index_revision": ui["new_index"]["index_revision"]})
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\n## S03-07/08 真实 Git rebase 与模块重索引\n\n"
                     "feature 与 main 分别提交后实际 rebase；安装版捕获新快照、发布本机隔离 PostgreSQL、"
                     "建立 CodeGraph 新索引并把已锁定订单计价模块更新至同一模块 ID。"
                     "旧卡、旧 SourceRef、A1/A2 与真实反馈未被覆盖。正式 S03 后续步骤和 macOS 仍待验收。\n")
    print(f"S03-07_08_PASS {new_snapshot_id}")


if __name__ == "__main__":
    main()
