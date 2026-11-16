"""Audit and record S01's installed card and two real provider feedback turns."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def artifact(run: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(run).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read_json(run / "manifest.json")
    model = read_json(run / "artifacts" / "s01-04-deepseek-ui.json")
    card = read_json(run / "artifacts" / "s01-card-live-ui.json")
    feedback = read_json(run / "artifacts" / "s01-feedback-live-ui.json")
    review = read_json(run / "artifacts" / "s01-06-feedback-semantic-review.json")
    if manifest["case_id"] != "S01" or hashlib.sha256(
        args.installer.read_bytes()
    ).hexdigest() != manifest["candidate_sha256"]:
        raise AssertionError("S01 候选不匹配")
    steps_path = run / "steps.jsonl"
    steps = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines()]
    if [step["step_id"] for step in steps] != [
        "S01-01", "S01-02", "S01-03", "S01-04",
    ]:
        raise AssertionError("S01 前四步未按序通过")
    if model["analysis"]["status"] != "succeeded" or card[
        "mode"
    ] != feedback["mode"] != "live_observation" or card[
        "status"
    ] != "observed" or feedback["status"] != "observed_pending_semantic_review":
        raise AssertionError("安装版卡片或反馈 UI 未完成")
    if len(feedback["turns"]) != 2 or any(
        turn["status"] != "feedback_ready" or turn["preview"]["provider_host"]
        != "https://api.deepseek.com" or turn["preview"]["model_name"]
        != "deepseek-flash" or not turn["preview"]["ui_payload_exact_match"]
        for turn in feedback["turns"]
    ):
        raise AssertionError("真实 DeepSeek 双轮反馈不完整")
    attempt_ids = [card["first_attempt_id"], card["correction_attempt_id"]]
    if review["status"] != "PASS" or review["task_id"] != card["task_id"] or {
        item["attempt_id"] for item in review["items"]
    } != set(attempt_ids) or not all(
        item["source_grounded"] and item["user_text_preserved"]
        for item in review["items"]
    ):
        raise AssertionError("反馈语义复核未覆盖两次回答")
    if card["opportunity_id"] not in {
        item["opportunity_id"] for item in model["opportunities"]
    } or card["progress"] != "completed" or len(card["chain"]["attempts"]) != 2:
        raise AssertionError("卡片与模型机会的来源关系不符")

    db_path = run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        proposal = db.execute("SELECT * FROM topic_proposals WHERE proposal_id=?",
                              (card["proposal_id"],)).fetchone()
        task = db.execute("SELECT * FROM growth_tasks WHERE task_id=?",
                          (card["task_id"],)).fetchone()
        attempts = db.execute(
            "SELECT * FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid",
            (card["task_id"],),
        ).fetchall()
        notes = db.execute("SELECT * FROM engineering_notes WHERE task_id=?",
                           (card["task_id"],)).fetchall()
        hints = db.execute("SELECT * FROM growth_task_hints WHERE task_id=?",
                           (card["task_id"],)).fetchall()
        feedback_requests = db.execute(
            "SELECT attempt_id,provider_name,status FROM feedback_model_requests "
            "WHERE attempt_id IN (?,?)", attempt_ids,
        ).fetchall()
        usages = db.execute(
            "SELECT attempt_id,input_tokens,output_tokens,elapsed_ms "
            "FROM feedback_model_responses WHERE attempt_id IN (?,?)", attempt_ids,
        ).fetchall()
        ids = [card["proposal_id"], card["topic_id"], card["task_id"],
               *attempt_ids, card["note_id"]]
        events = db.execute(
            f"SELECT * FROM growth_events WHERE entity_id IN ({','.join('?' for _ in ids)}) "
            "ORDER BY created_at,event_id", ids,
        ).fetchall()
    if proposal is None or proposal["status"] != "confirmed" or proposal[
        "topic_id"
    ] != card["topic_id"] or task is None or task[
        "opportunity_id"
    ] != card["opportunity_id"] or task["topic_id"] != card["topic_id"]:
        raise AssertionError("确认主题或短卡片来源不符")
    if len(attempts) != 2 or [row["attempt_id"] for row in attempts] != attempt_ids or any(
        row["actor_origin"] != "user" for row in attempts
    ) or attempts[1]["parent_attempt_id"] != attempt_ids[0] or attempts[1][
        "hint_level"
    ] != 1 or any(attempts[i]["answer_text"] != feedback["turns"][i][
        "original_answer"
    ] for i in range(2)):
        raise AssertionError("模拟用户原文或修正父链不符")
    if len(hints) != 1 or hints[0]["hint_id"] != card["hint"]["hint_id"] or len(
        notes
    ) != 1 or notes[0]["note_id"] != card["note_id"] or notes[0][
        "author"
    ] != "user" or notes[0]["content_text"] != card["chain"]["notes"][0][
        "content_text"
    ] or task["progress"] != "completed":
        raise AssertionError("提示、笔记原文或学习进度不符")
    if len(feedback_requests) != 2 or any(row["provider_name"] != "deepseek" or row[
        "status"
    ] != "succeeded" for row in feedback_requests) or len(usages) != 2 or any(
        row["input_tokens"] <= 0 or row["output_tokens"] <= 0 or row["elapsed_ms"] <= 0
        for row in usages
    ):
        raise AssertionError("模型双反馈请求或实际用量不符")
    card_events = [row for row in events if row["event_type"] not in {
        "feedback_requested", "feedback_ready",
    }]
    feedback_events = [row for row in events if row["event_type"] in {
        "feedback_requested", "feedback_ready",
    }]
    if len([row for row in card_events if row["event_type"] == "answer_saved"]) != 2 or len(
        [row for row in feedback_events if row["event_type"] == "feedback_ready"]
    ) != 2:
        raise AssertionError("回答或反馈业务事件缺失")
    usage_path = run / "artifacts" / "s01-06-feedback-usage.json"
    usage_path.write_text(json.dumps([dict(row) for row in usages], ensure_ascii=False,
                                     indent=2) + "\n", encoding="utf-8")
    device_id = manifest["environment"]["windows_node_id"]
    records = [
        {
            "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S01",
            "step_id": "S01-05", "status": "PASS",
            "started_at": card["started_at"], "finished_at": card["finished_at"],
            "device_id": device_id,
            "trace_id": next(row["trace_id"] for row in card_events
                             if row["event_type"] == "task_created"),
            "action": "安装版确认模型建议并建立短卡片，保存错误回答、提示、修正原文与用户笔记",
            "expected": "仅选中建议成为正式主题；错误原文与父子修正保留；笔记由模拟用户输入",
            "actual": {"topic_id": card["topic_id"], "task_id": card["task_id"],
                       "opportunity_id": card["opportunity_id"],
                       "attempt_ids": attempt_ids, "note_id": card["note_id"],
                       "hint_level": 1, "progress": task["progress"]},
            "artifacts": [artifact(run, run / "artifacts" / "s01-card-live-ui.json")],
        },
        {
            "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S01",
            "step_id": "S01-06", "status": "PASS",
            "started_at": feedback["started_at"], "finished_at": feedback["finished_at"],
            "device_id": device_id,
            "trace_id": feedback_events[-1]["trace_id"],
            "action": "安装版逐次批准 DeepSeek 反馈，复核两轮反馈与用户回答原文",
            "expected": "错误说法被纠正；修正回答获针对性反馈；模型不覆盖原文、不宣称已掌握",
            "actual": {"task_id": card["task_id"], "attempt_ids": attempt_ids,
                       "provider_name": "deepseek", "feedback_count": 2,
                       "original_answers_preserved": True},
            "artifacts": [artifact(run, run / "artifacts" / name) for name in (
                "s01-feedback-live-ui.json", "s01-06-feedback-semantic-review.json",
                "s01-06-feedback-usage.json",
            )],
        },
    ]
    with steps_path.open("a", encoding="utf-8") as output:
        for step in records:
            output.write(json.dumps(step, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in [*card_events, *feedback_events]:
            output.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
    chain = read_json(run / "chain.json")
    chain.update({"topic_id": card["topic_id"], "task_id": card["task_id"],
                  "attempt_ids": attempt_ids, "note_id": card["note_id"]})
    (run / "chain.json").write_text(
        json.dumps(chain, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (run / "report.md").open("a", encoding="utf-8") as report:
        report.write(
            "\n\n## S01-05/06 安装版卡片与真实反馈\n\n"
            "同一 Windows 安装版确认一条 DeepSeek 机会，保存模拟用户的错误回答、"
            "一级提示、修正原文和手写笔记；随后两次逐项预览批准 DeepSeek 反馈。"
            "SQLite 事件与真实 token 用量、人工语义复核一致。完整 S01 仍待同步、"
            "记忆、复习和 macOS 验收。\n"
        )
    print(f"S01-05_06_PASS {card['task_id']}")


if __name__ == "__main__":
    main()
