"""Independently audit the live Windows race card and record S02-02."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def artifact(run: Path, name: str) -> dict[str, str]:
    path = run / "artifacts" / name
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--installed-exe", type=Path, required=True)
    parser.add_argument("--strict-scenario", action="store_true")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    result = read(run / "result.json")
    capture = read(run / "artifacts/s02-02-capture-ui.json")
    model = read(run / "artifacts/s02-02-deepseek-ui.json")
    first_card_path = run / "artifacts/s02-02-card-ui-attempt1.json"
    first_card = read(first_card_path) if first_card_path.exists() else None
    card = read(run / "artifacts/s02-02-card-ui.json")
    if manifest["case_id"] != "S02" or not result["checks"]["S02-01"]:
        raise AssertionError("S02-01 前置缺失")
    if "S02-02" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S02-02 已经记录")
    candidate = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    executable = hashlib.sha256(args.installed_exe.read_bytes()).hexdigest()
    if candidate != manifest["candidate_sha256"]:
        raise AssertionError("安装包与同包候选不一致")
    if any(item["executable_sha256"] != executable for item in
           (capture, model, card, *([first_card] if first_card else []))):
        raise AssertionError("安装版二进制不一致")
    if capture["status"] != "observed" or model["status"] != "observed_pending_semantic_review" or card["status"] != "observed_pending_audit":
        raise AssertionError("安装版捕获、模型或卡片 UI 观察不完整")
    if first_card and (first_card["status"] != "failed"
                       or first_card["task_id"] != card["task_id"]
                       or not card["continued_after_driver_assertion"]):
        raise AssertionError("驱动前缀误判后的原卡续接证据缺失")
    if capture["snapshot"]["head_sha"] != read(run / "chain.json")["head_sha"]:
        raise AssertionError("模型快照未绑定真实目标 Git 提交")
    if model["snapshot_id"] != capture["snapshot_id"] or card["analysis_id"] != model["analysis"]["analysis_id"]:
        raise AssertionError("模型、卡片未绑定同一快照和分析")

    db_path = run / "fixture/repo/.ahadiff/growth.sqlite"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        analysis_id = model["analysis"]["analysis_id"]
        request = db.execute("SELECT * FROM analysis_model_requests WHERE analysis_id=?", (analysis_id,)).fetchone()
        response = db.execute("SELECT * FROM analysis_model_responses WHERE analysis_id=?", (analysis_id,)).fetchone()
        batch = db.execute("SELECT * FROM opportunity_batches WHERE analysis_id=?", (analysis_id,)).fetchone()
        source_ids = model["preview"]["source_ref_ids"]
        sources = db.execute(
            f"SELECT * FROM source_refs WHERE source_ref_id IN ({','.join('?' for _ in source_ids)})",
            source_ids).fetchall()
        task = db.execute("SELECT * FROM growth_tasks WHERE task_id=?", (card["task_id"],)).fetchone()
        events = [dict(row) for row in db.execute(
            "SELECT * FROM growth_events WHERE entity_id IN (?,?) ORDER BY created_at,event_id",
            (analysis_id, card["task_id"]))]
        attempts = db.execute("SELECT count(*) FROM learning_attempts WHERE task_id=?",
                              (card["task_id"],)).fetchone()[0]
    if not all((request, response, batch, sources, task)):
        raise AssertionError("本机真实模型请求、响应、源码或卡片账本缺失")
    opportunity = model["opportunities"][0]
    patch = (run / "artifacts/feature.diff").read_text(encoding="utf-8")
    source_map = {row["relative_path"]: row for row in sources}
    source = source_map.get("src/search.ts")
    if source is None:
        raise AssertionError("缺少 requestId 修复源码")
    source_text = source["content"].decode("utf-8")
    model_text = " ".join((opportunity["title"], opportunity["reason"],
                           opportunity["learning_goal"]))
    probe = source_map.get("test/PromiseProbe.mjs")
    approved_probe_path = run / "artifacts/s02-02-approved-probe.mjs"
    scenario_checks = {
        "approved_controlled_probe": probe is not None and approved_probe_path.is_file()
        and probe["content"] == approved_probe_path.read_bytes()
        and all(token in probe["content"].decode("utf-8") for token in
                ("search('a')", "search('ab')", "pending.get('ab')", "pending.get('a')")),
        "card_asks_old_request_cancellation": "取消" in opportunity["learning_goal"]
        and "a-result" in opportunity["learning_goal"]
        and "fetchResults" in opportunity["learning_goal"]
        and opportunity["learning_goal"] in card["task_chain"]["task"]["question"],
        "model_uses_controlled_order": "ab" in opportunity["reason"]
        and "a" in opportunity["reason"] and "completed" in opportunity["reason"],
    } if args.strict_scenario else {}
    checks = {
        "controlled_promise_both_complete": read(run / "artifacts/windows-probe-result.json")["checks"]["both_requests_complete"],
        "source_exact_guard": "const requestId = ++latestRequest;" in source_text
        and "if (requestId === latestRequest)" in source_text
        and "render(rows);" in source_text,
        "real_diff_guard": "requestId === latestRequest" in patch,
        "model_explains_render_guard": all(token in model_text for token in
                                            ("latestRequest", "requestId", "render")),
        "model_does_not_claim_cancellation": not any(token in model_text for token in
                                                  ("已取消请求", "取消了请求", "AbortController", "abort(")),
        "approved_sources": all(row["snapshot_id"] == capture["snapshot_id"] for row in sources)
        and {row["source_ref_id"] for row in sources} == set(source_ids)
        and set(json.loads(request["source_ref_ids_json"])) == set(source_ids)
        and set(json.loads(opportunity["source_refs_json"])) == set(source_ids),
        "real_deepseek_receipt": request["provider_name"] == "deepseek"
        and request["approved_payload_hash"] == model["preview"]["approval_hash"]
        and model["preview"]["provider_host"] == "https://api.deepseek.com"
        and batch["origin"] == "live" and batch["model_name"] == "deepseek-flash"
        and min(response["input_tokens"], response["output_tokens"], response["elapsed_ms"]) > 0,
        "card_links_guarded_source": task["opportunity_id"] == opportunity["opportunity_id"]
        and task["topic_id"] == card["topic_id"]
        and set(json.loads(task["source_refs_json"])) == set(source_ids)
        and opportunity["learning_goal"] in task["question"]
        and opportunity["learning_goal"] in card["card_text"],
        "card_has_no_answer_yet": task["progress"] == "ready" and attempts == 0,
        **scenario_checks,
    }
    review = {
        "status": "PASS" if all(checks.values()) else "FAIL", "mode": "live_observation",
        "analysis_id": analysis_id, "task_id": card["task_id"], "checks": checks,
        "source_paths": sorted(source_map), "provider_request_id": response["provider_request_id"],
        "input_tokens": response["input_tokens"], "output_tokens": response["output_tokens"],
        "task_created_trace_id": next((row["trace_id"] for row in events
                                        if row["event_type"] == "task_created"), None),
    }
    write(run / "artifacts/s02-02-semantic-review.json", review)
    if review["status"] != "PASS" or not review["task_created_trace_id"]:
        raise AssertionError(checks)
    names = ["s02-02-capture-ui.json", "s02-02-deepseek-ui.json",
             "s02-02-card-ui.json", "s02-02-semantic-review.json",
             "feature.diff", "windows-probe-result.json"]
    if first_card:
        names.append("s02-02-card-ui-attempt1.json")
    if args.strict_scenario:
        names.append("s02-02-approved-probe.mjs")
    step = {
        "schema_version": 1, "run_id": run.name, "case_id": "S02", "step_id": "S02-02",
        "status": "PASS", "started_at": capture["started_at"],
        "finished_at": card["finished_at"],
        "device_id": manifest["environment"]["windows_node_id"],
        "trace_id": review["task_created_trace_id"],
        "related_trace_ids": [row["trace_id"] for row in events if row["event_type"]
                              in ("analysis_requested", "analysis_succeeded")],
        "action": "Windows 安装版捕获真实 Git 快照，批准 DeepSeek 请求、确认竞态主题并开卡",
        "expected": "卡片关联 requestId 守卫源码，且不把忽略旧响应说成取消请求",
        "actual": {"analysis_id": analysis_id, "opportunity_id": opportunity["opportunity_id"],
                   "topic_id": card["topic_id"], "task_id": card["task_id"],
                   "snapshot_id": capture["snapshot_id"], "provider_name": "deepseek"},
        "artifacts": [artifact(run, name) for name in names],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")
    chain = read(run / "chain.json")
    chain.update({"snapshot_id": capture["snapshot_id"], "analysis_id": analysis_id,
                  "opportunity_id": opportunity["opportunity_id"],
                  "topic_id": card["topic_id"], "task_id": card["task_id"]})
    write(run / "chain.json", chain)
    result.setdefault("proof", {})["windows_app"] = artifact(run, "s02-02-card-ui.json")
    result["proof"]["model_provider"] = artifact(run, "s02-02-semantic-review.json")
    result["proof"]["semantic_review"] = artifact(run, "s02-02-semantic-review.json")
    write(run / "result.json", result)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\n## S02-02 Windows 真实模型竞态卡片\n\n"
                     "同一安装包捕获真实 requestId 修复快照；安装版核对单文件请求后调用 DeepSeek Flash，"
                     "确认竞态主题并创建源码关联卡片。独立语义复核确认模型解释的是仅渲染最新结果，"
                     "没有把旧请求说成已取消。"
                     + ("模型还读取用户批准的受控 Promise Probe，卡片明确追问旧请求是否取消。"
                        if args.strict_scenario else "")
                     + ("首次驱动因卡片标准前缀误判而中断，保留原记录，"
                        "续接使用原 task_id，没有重发模型或重建卡片。"
                        if first_card else "")
                     + "完整 S02 仍待验收。\n")
    print(f"S02-02_PASS {card['task_id']}")


if __name__ == "__main__":
    main()
