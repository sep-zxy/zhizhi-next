"""Audit and record a live, installed S03 module-linked TTL card."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def artifact(run: Path, name: str) -> dict[str, str]:
    path = run / "artifacts" / name
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--module-run", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    result = read(run / "result.json")
    module_result = read(args.module_run / "result.json")
    prepared = read(run / "artifacts/s03-05-preparation.json")
    ui = read(run / "artifacts/s03-05-deepseek-ui.json")
    finish = read(run / "artifacts/s03-05-card-finish-ui.json")
    first_upload = read(run / "artifacts/s03-05-card-finish-ui-attempt1.json")
    review = read(run / "artifacts/s03-05-semantic-review.json")
    if manifest["case_id"] != "S03" or hashlib.sha256(args.installer.read_bytes()).hexdigest() != manifest["candidate_sha256"]:
        raise AssertionError("安装包与 S03 候选不一致")
    if not all(result["checks"][f"S03-{n:02d}"] for n in range(1, 5)) or "S03-05" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S03 前置步骤缺失或 S03-05 已记录")
    if module_result["status"] != "PASS" or module_result["source_run_id"] != run.name or prepared["module_run_id"] != args.module_run.name:
        raise AssertionError("模块前置不属于本次正式 S03")
    if prepared["candidate_sha256"] != manifest["candidate_sha256"] or prepared["module_id"] != module_result["module_id"]:
        raise AssertionError("模块、版本 ID 不一致")
    if ui["mode"] != "live_observation" or ui["status"] != "failed" or not ui["error"].startswith("本地模块卡片缺少模块索引版本"):
        raise AssertionError("没有保留建卡后的驱动误判记录")
    if ui["preview"]["provider_host"] != "https://api.deepseek.com" or ui["preview"]["model_name"] != "deepseek-flash" or not ui["preview"]["ui_payload_exact_match"]:
        raise AssertionError("DeepSeek 安装版批准请求不完整")
    if ui["analysis"]["status"] != "succeeded" or ui["analysis"]["mode"] != "live" or ui["analysis"]["supersedes_analysis_id"] is not None:
        raise AssertionError("真实机会分析版本不完整")
    if finish["status"] != "observed_pending_audit" or first_upload["final_sync"]["uploaded"] < 3 or finish["final_sync"]["outbox"]["pending"] != 0:
        raise AssertionError("安装版续接上传或零待办回执缺失")
    if finish["task_id"] != ui["task_id"] or finish["analysis_id"] != ui["analysis"]["analysis_id"] or finish["opportunity_id"] != ui["opportunity_id"]:
        raise AssertionError("续接的是不同任务")
    if review["status"] != "PASS" or review["analysis_id"] != ui["analysis"]["analysis_id"] or review["selected_opportunity_id"] != ui["opportunity_id"] or not all(review["checks"].values()):
        raise AssertionError("独立源码语义复核未通过")
    if "订单计价" not in finish["card_text"] or ui["opportunity"]["learning_goal"] not in finish["card_text"]:
        raise AssertionError("安装版卡片未显示模块或真实模型问题")

    db_path = run / "fixture/repo/.ahadiff/growth.sqlite"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        analysis_id = ui["analysis"]["analysis_id"]
        request = db.execute("SELECT * FROM analysis_model_requests WHERE analysis_id=?", (analysis_id,)).fetchone()
        response = db.execute("SELECT * FROM analysis_model_responses WHERE analysis_id=?", (analysis_id,)).fetchone()
        batch = db.execute("SELECT * FROM opportunity_batches WHERE analysis_id=?", (analysis_id,)).fetchone()
        task = db.execute("SELECT * FROM growth_tasks WHERE task_id=?", (ui["task_id"],)).fetchone()
        sources = db.execute("SELECT source_ref_id, relative_path FROM source_refs WHERE source_ref_id IN (?,?)",
                             tuple(ui["preview"]["source_ref_ids"])).fetchall()
        events = db.execute("SELECT * FROM growth_events WHERE entity_id IN (?,?) ORDER BY created_at,event_id",
                            (analysis_id, ui["task_id"])).fetchall()
        assert request and response and batch and task
        task = dict(task)
        events = [dict(row) for row in events]
    source_map = {row["relative_path"]: row["source_ref_id"] for row in sources}
    expected_paths = {"src/demo/orders/PriceCache.java", "src/demo/orders/OrderService.java"}
    if set(source_map) != expected_paths or set(source_map.values()) != set(ui["preview"]["source_ref_ids"]):
        raise AssertionError("模型机会没有同时引用新缓存和 Service")
    if request["provider_name"] != "deepseek" or request["approved_payload_hash"] != ui["preview"]["approval_hash"] or set(json.loads(request["source_ref_ids_json"])) != set(source_map.values()):
        raise AssertionError("模型批准正文与本机请求不一致")
    if batch["origin"] != "live" or batch["model_name"] != "deepseek-flash" or min(response["input_tokens"], response["output_tokens"], response["elapsed_ms"]) <= 0:
        raise AssertionError("真实 provider 响应回执缺失")
    if task["opportunity_id"] != ui["opportunity_id"] or task["module_id"] != prepared["module_id"] or task["module_index_revision"] != prepared["module_index_revision"]:
        raise AssertionError("本地卡片关联的机会或模块错误")
    if ui["opportunity"]["learning_goal"] not in task["question"] or set(json.loads(task["source_refs_json"])) != set(source_map.values()):
        raise AssertionError("卡片问题或原始源码引用不一致")
    if not {"analysis_requested", "analysis_succeeded", "task_created"} <= {row["event_type"] for row in events}:
        raise AssertionError("本机业务事件缺失")

    account_id = uuid.UUID(prepared["account_id"])
    task_id = uuid.UUID(ui["task_id"])
    with connect(args.dsn) as conn:
        cloud_task = conn.execute(
            "SELECT opportunity_id,topic_id,question,source_refs,module_id,module_index_revision "
            "FROM growth_tasks WHERE account_id=%s AND task_id=%s", (account_id, task_id)).fetchone()
        cloud_module = conn.execute(
            "SELECT snapshot_id,index_revision,name,locked FROM growth_modules WHERE account_id=%s AND module_id=%s",
            (account_id, uuid.UUID(prepared["module_id"]))).fetchone()
        feed = conn.execute(
            "SELECT event_type,payload FROM growth_change_feed WHERE account_id=%s AND entity_type='task' AND entity_id=%s",
            (account_id, task_id)).fetchall()
    if cloud_task is None or str(cloud_task["opportunity_id"]) != ui["opportunity_id"] or str(cloud_task["topic_id"]) != task["topic_id"] or cloud_task["question"] != task["question"]:
        raise AssertionError("隔离 PostgreSQL 任务未与本机 ID 和原文一致")
    if set(cloud_task["source_refs"]) != set(source_map.values()) or str(cloud_task["module_id"]) != prepared["module_id"] or cloud_task["module_index_revision"].strip() != prepared["module_index_revision"]:
        raise AssertionError("云端任务丢失源码或模块版本")
    if cloud_module is None or not cloud_module["locked"] or cloud_module["name"] != "订单计价" or str(cloud_module["snapshot_id"]) != prepared["snapshot_id"] or cloud_module["index_revision"].strip() != prepared["module_index_revision"]:
        raise AssertionError("云端锁定模块与快照版本不一致")
    if len(feed) != 1 or feed[0]["event_type"] != "task_created" or feed[0]["payload"]["module_id"] != prepared["module_id"]:
        raise AssertionError("云端建卡事件非唯一或模块关联不一致")

    audit = {
        "status": "PASS", "mode": "live_observation", "run_id": run.name,
        "candidate_sha256": manifest["candidate_sha256"],
        "analysis_id": analysis_id, "opportunity_id": ui["opportunity_id"],
        "task_id": ui["task_id"], "module_id": prepared["module_id"],
        "provider_host": ui["preview"]["provider_host"], "model_name": batch["model_name"],
        "provider_request_id": response["provider_request_id"],
        "input_tokens": response["input_tokens"], "output_tokens": response["output_tokens"],
        "source_map": source_map, "cloud_kind": "isolated_local_postgresql",
        "first_card_upload_count": first_upload["final_sync"]["uploaded"],
        "cloud_task_event_count": len(feed), "semantic_review": review["status"],
    }
    write(run / "artifacts/s03-05-live-audit.json", audit)
    names = ["s03-05-preparation.json", "s03-05-deepseek-ui-attempt1.json",
             "s03-05-deepseek-ui-attempt2.json", "s03-05-deepseek-ui-attempt3.json",
             "s03-05-deepseek-ui.json", "s03-05-card-finish-ui-attempt1.json",
             "s03-05-card-finish-ui.json", "s03-05-semantic-review.json",
             "s03-05-live-audit.json"]
    step = {
        "schema_version": 1, "run_id": run.name, "case_id": "S03", "step_id": "S03-05",
        "status": "PASS", "started_at": ui["started_at"], "finished_at": finish["finished_at"],
        "device_id": manifest["environment"]["windows_node_id"],
        "trace_id": next(row["trace_id"] for row in events if row["event_type"] == "task_created"),
        "related_trace_ids": [row["trace_id"] for row in events if row["event_type"] in ("analysis_requested", "analysis_succeeded")],
        "action": "Windows 安装版批准双源码发送真实 DeepSeek，确认主题并建立订单计价模块 TTL 短卡片",
        "expected": "卡片引用 PriceCache.get 与 Service 初始化；过期仅在访问时检查，无后台定时器或 Redis 幻觉",
        "actual": {"analysis_id": analysis_id, "opportunity_id": ui["opportunity_id"],
                   "task_id": ui["task_id"], "module_id": prepared["module_id"],
                   "module_index_revision": prepared["module_index_revision"],
                   "provider_name": "deepseek", "cloud_kind": "isolated_local_postgresql"},
        "artifacts": [artifact(run, name) for name in names],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")
    chain = read(run / "chain.json")
    chain.update({"analysis_id": analysis_id, "opportunity_id": ui["opportunity_id"],
                  "topic_id": task["topic_id"], "task_id": ui["task_id"]})
    write(run / "chain.json", chain)
    result.setdefault("proof", {})["model_provider"] = artifact(run, "s03-05-live-audit.json")
    result["proof"]["semantic_review"] = artifact(run, "s03-05-semantic-review.json")
    write(run / "result.json", result)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\n## S03-05 Windows 真实模型与模块卡片\n\n"
                     "安装版向 DeepSeek Flash 发送经界面批准的 PriceCache 与 OrderService 变更。"
                     "选中的 TTL 问题通过独立源码语义复核，卡片保留已锁定订单计价模块及索引版本，"
                     "本机 SQLite 与本机隔离 PostgreSQL 同 ID。驱动的页面折叠、导航及问题前缀误判"
                     "均保留原始失败记录，续接没有重发模型或重建卡片。外部云、macOS 与完整 S03 仍待验收。\n")
    print(f"S03-05_PASS {ui['task_id']}")


if __name__ == "__main__":
    main()
