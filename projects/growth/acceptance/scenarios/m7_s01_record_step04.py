"""Record the installed S01 DeepSeek step only after source and response review."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def artifact(run: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(run).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read_json(run / "manifest.json")
    retry_path = run / "artifacts" / "s01-04-deepseek-ui-retry.json"
    failed_path = run / "artifacts" / "s01-04-deepseek-ui.json"
    ui = read_json(retry_path if retry_path.exists() else failed_path)
    failed = read_json(failed_path) if retry_path.exists() else None
    review = read_json(run / "artifacts" / "s01-04-semantic-review.json")
    installer_sha = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if manifest["case_id"] != "S01" or manifest["candidate_sha256"] != installer_sha:
        raise AssertionError("S01 候选安装包不匹配")
    if "S01-04" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S01-04 已有正式记录")
    if ui["mode"] != "live_observation" or ui["status"] != "observed_pending_semantic_review":
        raise AssertionError("模型 UI 未完成 live 观察")
    if ui["preview"]["provider_host"] != "https://api.deepseek.com" or ui[
        "preview"
    ]["model_name"] != "deepseek-flash" or ui["provider_name"] != "deepseek":
        raise AssertionError("未使用真实 DeepSeek provider")
    if ui["workspace"] != str(run / "fixture" / "repo") or not ui[
        "preview"
    ]["ui_payload_exact_match"]:
        raise AssertionError("界面授权请求或工作区不匹配")
    if failed is not None:
        if failed["status"] != "failed" or not ui["reanalysis"] or ui["analysis"][
            "supersedes_analysis_id"
        ] != failed["submission"]["analysis_id"]:
            raise AssertionError("首次网络失败与主动重试的版本关系不完整")
    elif ui["reanalysis"] or ui["analysis"]["supersedes_analysis_id"] is not None:
        raise AssertionError("首次分析不能伪装为主动重试")
    if ui["analysis"]["mode"] != "live" or ui["analysis"]["status"] != "succeeded":
        raise AssertionError("真实模型分析未成功")
    opportunities = ui["opportunities"]
    source_ids = ui["preview"]["source_ref_ids"]
    if not 1 <= len(opportunities) <= 3 or len(source_ids) != 1:
        raise AssertionError("机会数量或批准来源不符")
    if any(
        item["analysis_id"] != ui["analysis"]["analysis_id"]
        or not 3 <= item["estimated_minutes"] <= 5
        or json.loads(item["source_refs_json"]) != source_ids
        for item in opportunities
    ):
        raise AssertionError("机会版本、预计时长或源码引用不符")
    if review["status"] != "PASS" or review["analysis_id"] != ui["analysis"][
        "analysis_id"
    ] or {item["opportunity_id"] for item in review["items"]} != {
        item["opportunity_id"] for item in opportunities
    } or not all(item["source_grounded"] and item["uncertainty_honest"] for item in review["items"]):
        raise AssertionError("人工语义审阅未覆盖全部机会")

    db_path = run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        analysis_id = ui["analysis"]["analysis_id"]
        request = db.execute(
            "SELECT provider_name, approved_payload_hash, source_ref_ids_json "
            "FROM analysis_model_requests WHERE analysis_id=?", (analysis_id,),
        ).fetchone()
        response = db.execute(
            "SELECT input_tokens, output_tokens, elapsed_ms FROM analysis_model_responses "
            "WHERE analysis_id=?", (analysis_id,),
        ).fetchone()
        batch = db.execute(
            "SELECT model_name FROM opportunity_batches WHERE analysis_id=?", (analysis_id,),
        ).fetchone()
        topic_times = [datetime.fromisoformat(row[0]) for row in db.execute(
            "SELECT created_at FROM growth_topics"
        )]
        formal_topics_at_model = sum(
            created <= datetime.fromisoformat(ui["finished_at"]) for created in topic_times
        )
        ids = [analysis_id, *[item["opportunity_id"] for item in opportunities]]
        events = db.execute(
            f"SELECT * FROM growth_events WHERE entity_id IN ({','.join('?' for _ in ids)}) "
            "ORDER BY created_at, event_id", ids,
        ).fetchall()
    if request is None or request["provider_name"] != "deepseek" or request[
        "approved_payload_hash"
    ] != ui["preview"]["approval_hash"] or json.loads(request["source_ref_ids_json"]) != source_ids:
        raise AssertionError("批准内容与数据库请求不一致")
    if response is None or response["input_tokens"] <= 0 or response[
        "output_tokens"
    ] <= 0 or response["elapsed_ms"] <= 0 or batch is None or batch[
        "model_name"
    ] != "deepseek-flash" or formal_topics_at_model != 0:
        raise AssertionError("真实 provider 用量或未确认主题状态不符")
    completed = next((item for item in events if item["event_type"] == "analysis_succeeded"), None)
    requested = next((item for item in events if item["event_type"] == "analysis_requested"), None)
    if completed is None or requested is None or sum(
        item["event_type"] == "opportunity_saved" for item in events
    ) != len(opportunities):
        raise AssertionError("分析或机会业务事件缺失")

    metadata_path = run / "artifacts" / "s01-04-provider-metadata.json"
    metadata_path.write_text(json.dumps({
        "provider_host": ui["preview"]["provider_host"],
        "model_name": batch["model_name"],
        "analysis_id": analysis_id,
        "input_tokens": response["input_tokens"],
        "output_tokens": response["output_tokens"],
        "elapsed_ms": response["elapsed_ms"],
        "formal_topic_count_at_model": formal_topics_at_model,
        "first_attempt_status": failed["status"] if failed is not None else "succeeded",
        "retry_supersedes": failed["submission"]["analysis_id"] if failed is not None else None,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifact_names = ["s01-04-deepseek-ui.json", "s01-04-semantic-review.json",
                      "s01-04-provider-metadata.json"]
    if retry_path.exists():
        artifact_names.append("s01-04-deepseek-ui-retry.json")
    artifacts = [artifact(run, run / "artifacts" / name) for name in artifact_names]
    step = {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "case_id": "S01",
        "step_id": "S01-04",
        "status": "PASS",
        "started_at": ui["started_at"],
        "finished_at": ui["finished_at"],
        "device_id": manifest["environment"]["windows_node_id"],
        "trace_id": completed["trace_id"],
        "related_trace_ids": [requested["trace_id"]],
        "action": "Windows 安装版逐项批准源码后调用 DeepSeek，生成并人工复核学习机会",
        "expected": "真实模型返回 0–3 条有源码依据的机会；不确定性如实标注；建议未经确认不创建正式主题",
        "actual": {
            "provider_host": "https://api.deepseek.com",
            "model_name": "deepseek-flash",
            "analysis_id": analysis_id,
            "source_ref_ids": source_ids,
            "opportunity_ids": [item["opportunity_id"] for item in opportunities],
            "formal_topic_count_at_model": formal_topics_at_model,
            "first_attempt_failed_then_reanalysis_succeeded": failed is not None,
        },
        "artifacts": artifacts,
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
    chain = read_json(run / "chain.json")
    chain["analysis_id"] = analysis_id
    chain["opportunity_ids"] = step["actual"]["opportunity_ids"]
    (run / "chain.json").write_text(
        json.dumps(chain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result_path = run / "result.json"
    result = read_json(result_path)
    result.setdefault("proof", {})["model_provider"] = artifact(run, metadata_path)
    result["proof"]["semantic_review"] = artifact(
        run, run / "artifacts" / "s01-04-semantic-review.json"
    )
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write(
            "\n\n## S01-04 Windows 安装版真实模型\n\n"
            "用户在安装版界面核对单文件请求并批准发送。DeepSeek Flash 真实返回"
            f" {len(opportunities)} 条机会，均经源码语义复核；未确认建议没有创建正式主题。"
            "本机代理合成 DNS 经临时公有 DNS 映射后完成请求，系统 hosts 已恢复。"
            "Mac 和完整情境仍待验收。\n"
        )
    print(f"S01-04_PASS {analysis_id}")


if __name__ == "__main__":
    main()
