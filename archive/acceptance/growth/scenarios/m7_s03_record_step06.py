"""Audit and record S03's exact A1/A2 and real DeepSeek feedback."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect
from m7_s03_feedback_replay_provider import A1, A2


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
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    result = read(run / "result.json")
    prepared = read(run / "artifacts/s03-06-preparation.json")
    answers = read(run / "artifacts/s03-06-answers-ui.json")
    feedback = read(run / "artifacts/s03-06-feedback-ui.json")
    synced = read(run / "artifacts/s03-06-sync-ui.json")
    review = read(run / "artifacts/s03-06-semantic-review.json")
    model = read(run / "artifacts/s03-05-deepseek-ui.json")
    if manifest["case_id"] != "S03" or hashlib.sha256(args.installer.read_bytes()).hexdigest() != manifest["candidate_sha256"]:
        raise AssertionError("S03 安装包候选不一致")
    if not result["checks"]["S03-05"] or "S03-06" in (run / "steps.jsonl").read_text(encoding="utf-8"):
        raise AssertionError("S03-05 未通过或 S03-06 已记录")
    if prepared["task_id"] != model["task_id"] or answers["mode"] != feedback["mode"] != synced["mode"] != "live_observation":
        raise AssertionError("回答链没有来自本次真实模型卡片")
    if answers["status"] != "observed" or feedback["status"] != "observed_pending_semantic_review" or synced["status"] != "observed_pending_audit":
        raise AssertionError("安装版回答、模型反馈或同步未完成")
    if len(feedback["turns"]) != 2 or [row["original_answer"] for row in feedback["turns"]] != [A1, A2]:
        raise AssertionError("S03 A1/A2 不是情境原文")
    if review["status"] != "PASS" or review["task_id"] != prepared["task_id"] or not all(review["checks"].values()):
        raise AssertionError("反馈语义复核未通过")
    first, second = feedback["turns"]
    if not first["feedback"]["corrections"] or second["feedback"]["corrections"] != []:
        raise AssertionError("A1 纠错或 A2 不误纠错语义错误")
    if not ("后台" in " ".join(first["feedback"]["corrections"])
            and "load" in " ".join(first["feedback"]["code_facts"])
            and "now + ttlMillis" in " ".join(second["feedback"]["code_facts"])):
        raise AssertionError("反馈未区分后台刷新与读时过期")
    expected_ids = [answers["first_attempt_id"], answers["second_attempt_id"]]
    if [row["attempt_id"] for row in feedback["turns"]] != expected_ids or synced["attempt_ids"] != expected_ids:
        raise AssertionError("回答、反馈与上传 ID 不一致")
    if synced["sync"]["account_id"] != prepared["account_id"] or synced["sync"]["uploaded"] < 4 or synced["sync"]["outbox"]["pending"] != 0 or synced["sync"]["outbox"]["conflict"] != 0:
        raise AssertionError("隔离云 A1/A2 与反馈上传回执不完整")

    with sqlite3.connect(run / "fixture/repo/.ahadiff/growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        local = [dict(row) for row in db.execute(
            "SELECT * FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid", (prepared["task_id"],))]
        requests = [dict(row) for row in db.execute(
            "SELECT * FROM feedback_model_requests WHERE attempt_id IN (?,?) ORDER BY created_at", expected_ids)]
        responses = [dict(row) for row in db.execute(
            "SELECT * FROM feedback_model_responses WHERE attempt_id IN (?,?) ORDER BY created_at", expected_ids)]
        events = [dict(row) for row in db.execute(
            "SELECT * FROM growth_events WHERE entity_id IN (?,?) ORDER BY created_at,event_id", expected_ids)]
    if len(local) != len(requests) or len(local) != len(responses):
        raise AssertionError("本地反馈链数量错误")
    if len(local) != 2 or [row["attempt_id"] for row in local] != expected_ids or [row["answer_text"] for row in local] != [A1, A2]:
        raise AssertionError("回答原文或顺序改变")
    if [row["hint_level"] for row in local] != [0, 1] or local[0]["parent_attempt_id"] is not None or local[1]["parent_attempt_id"] != expected_ids[0]:
        raise AssertionError("提示级别或修正父链错误")
    if any(row["status"] != "feedback_ready" or not row["feedback_json"] for row in local):
        raise AssertionError("反馈未落库或覆盖回答")
    by_request = {row["attempt_id"]: row for row in requests}
    by_response = {row["attempt_id"]: row for row in responses}
    for turn in feedback["turns"]:
        attempt_id = turn["attempt_id"]
        saved = by_request[attempt_id]
        receipt = by_response[attempt_id]
        if (turn["preview"]["provider_host"] != "https://api.deepseek.com"
                or turn["preview"]["model_name"] != "deepseek-flash"
                or not turn["preview"]["ui_payload_exact_match"]
                or saved["approved_payload_hash"] != turn["preview"]["approval_hash"]
                or hashlib.sha256(saved["approved_payload_text"].encode()).hexdigest() != turn["preview"]["payload_sha256"]
                or saved["provider_name"] != "deepseek"
                or min(receipt["input_tokens"], receipt["output_tokens"], receipt["elapsed_ms"]) <= 0):
            raise AssertionError("真实反馈 provider 请求/响应与 UI 批准正文不一致")
        if set(turn["feedback"]["source_refs"]) != set(prepared["source_ref_ids"]):
            raise AssertionError("反馈没有双源码引用")

    account_id = uuid.UUID(prepared["account_id"])
    task_id = uuid.UUID(prepared["task_id"])
    with connect(args.dsn) as conn:
        cloud = conn.execute(
            "SELECT attempt_id,answer_text,answer_hash,parent_attempt_id,hint_level,status,feedback "
            "FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s ORDER BY created_at",
            (account_id, task_id)).fetchall()
        feed = conn.execute(
            "SELECT event_type FROM growth_change_feed WHERE account_id=%s AND entity_type='attempt' "
            "AND entity_id=ANY(%s) ORDER BY change_seq",
            (account_id, [uuid.UUID(item) for item in expected_ids])).fetchall()
    if len(cloud) != 2 or [str(row["attempt_id"]) for row in cloud] != expected_ids:
        raise AssertionError("云端回答 ID 或数量不一致")
    for index, row in enumerate(cloud):
        expected = [A1, A2][index]
        if (row["answer_text"] != expected or row["answer_hash"].strip() != hashlib.sha256(expected.encode()).hexdigest()
                or row["status"] != "feedback_ready" or not row["feedback"]
                or row["hint_level"] != index
                or (str(row["parent_attempt_id"]) if row["parent_attempt_id"] else None) != ([None, expected_ids[0]][index])):
            raise AssertionError("云端回答原文、父链、提示或反馈不一致")
    if [row["event_type"] for row in feed] != ["answer_saved", "answer_saved", "feedback_ready", "feedback_ready"]:
        raise AssertionError("云端回答与反馈事件不完整")

    audit = {"status": "PASS", "mode": "live_observation", "run_id": run.name,
             "candidate_sha256": manifest["candidate_sha256"], "task_id": prepared["task_id"],
             "attempt_ids": expected_ids, "provider_name": "deepseek",
             "input_tokens": [row["input_tokens"] for row in responses],
             "output_tokens": [row["output_tokens"] for row in responses],
             "cloud_kind": "isolated_local_postgresql", "cloud_events": [row["event_type"] for row in feed]}
    write(run / "artifacts/s03-06-live-audit.json", audit)
    names = ["s03-06-preparation.json", "s03-06-answers-ui.json", "s03-06-feedback-ui.json",
             "s03-06-sync-ui.json", "s03-06-semantic-review.json", "s03-06-live-audit.json"]
    step = {"schema_version": 1, "run_id": run.name, "case_id": "S03", "step_id": "S03-06",
            "status": "PASS", "started_at": answers["started_at"], "finished_at": synced["finished_at"],
            "device_id": manifest["environment"]["windows_node_id"],
            "trace_id": next(row["trace_id"] for row in events if row["event_type"] == "feedback_ready"),
            "related_trace_ids": [row["trace_id"] for row in events if row["event_type"] == "answer_saved"],
            "action": "安装版输入错误 A1、查看一级提示、输入修正 A2，两次批准真实 DeepSeek 反馈并上传",
            "expected": "保留原文和修正父链；反馈区分访问时检查与后台刷新，A2 不产生错误纠错",
            "actual": {"task_id": prepared["task_id"], "attempt_ids": expected_ids,
                       "hint_levels": [0, 1], "provider_name": "deepseek",
                       "cloud_kind": "isolated_local_postgresql"},
            "artifacts": [artifact(run, name) for name in names]}
    with (run / "steps.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")
    chain = read(run / "chain.json")
    chain["attempt_ids"] = expected_ids
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write("\n\n## S03-06 Windows 双回答与真实反馈\n\n"
                     "安装版保存情境 A1/A2 原文及一级提示父链。两次 DeepSeek Flash 反馈经批准正文、"
                     "provider 用量、本机账本、独立源码语义复核和本机隔离 PostgreSQL 同 ID 回执核对。"
                     "A1 的后台刷新误解被指出，A2 被认可且无错误纠错。Mac 与完整情境仍待验收。\n")
    print(f"S03-06_PASS {prepared['task_id']}")


if __name__ == "__main__":
    main()
