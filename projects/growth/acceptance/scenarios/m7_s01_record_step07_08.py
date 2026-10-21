"""Audit the installed S01 correction and handwritten note as separate steps."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

A1 = (
    "CompletableFuture 每调用一次 supplyAsync 就新建一个线程，"
    "join 只是把两个结果放到一起，不会等待。"
)
A2 = (
    "两个查询任务交给传入的 executor，线程是不是新建由执行器决定。"
    "thenCombine 组合结果，最后 join 会等待组合结果，调用 plan 的线程仍可能被阻塞。"
)
N1 = (
    "我以前把 CompletableFuture 当成“每个任务自动新建线程”。"
    "这次代码把两个查询交给同一个 executor，实际线程由执行器安排。"
    "thenCombine 组合结果，join 会等待，所以这段代码并不意味着调用方从头到尾都不阻塞。"
    "以后排查并发问题，我要先找执行器在哪里配置。"
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact(run: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(run).as_posix(), "sha256": sha256(path.read_bytes())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read_json(run / "manifest.json")
    card_path = run / "artifacts" / "s01-card-live-ui.json"
    feedback_path = run / "artifacts" / "s01-feedback-live-ui.json"
    review_path = run / "artifacts" / "s01-06-feedback-semantic-review.json"
    card, feedback, review = map(read_json, (card_path, feedback_path, review_path))
    chain = read_json(run / "chain.json")
    steps_path = run / "steps.jsonl"
    steps = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines()]
    if (
        manifest["case_id"] != "S01"
        or manifest["candidate_sha256"] != sha256(args.installer.read_bytes())
        or [step["step_id"] for step in steps] != [f"S01-{n:02}" for n in range(1, 7)]
    ):
        raise AssertionError("候选包或 S01 前六步证据不匹配")
    if (
        card["mode"] != feedback["mode"] != "live_observation"
        or card["status"] != "observed"
        or review["status"] != "PASS"
    ):
        raise AssertionError("安装版 UI 或真实反馈语义复核未通过")
    attempt_ids = [card["first_attempt_id"], card["correction_attempt_id"]]
    if (
        attempt_ids[0] == attempt_ids[1]
        or chain["attempt_ids"] != attempt_ids
        or chain["note_id"] != card["note_id"]
    ):
        raise AssertionError("修正回答或笔记业务 ID 不连续")
    if [turn["original_answer"] for turn in feedback["turns"]] != [A1, A2] or [
        turn["attempt_id"] for turn in feedback["turns"]
    ] != attempt_ids:
        raise AssertionError("A1/A2 原文与驱动输入不一致")
    if (
        feedback["turns"][1]["status"] != "feedback_ready"
        or feedback["turns"][1]["feedback"]["corrections"]
        or not all(
            item["source_grounded"] and item["user_text_preserved"] for item in review["items"]
        )
    ):
        raise AssertionError("A2 反馈未准确确认修正回答")

    db_path = run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        attempts = db.execute(
            "SELECT * FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid",
            (card["task_id"],),
        ).fetchall()
        notes = db.execute(
            "SELECT * FROM engineering_notes WHERE task_id=?", (card["task_id"],)
        ).fetchall()
        timeline = db.execute(
            "SELECT * FROM growth_timeline WHERE task_id=?", (card["task_id"],)
        ).fetchall()
        task = db.execute(
            "SELECT * FROM growth_tasks WHERE task_id=?", (card["task_id"],)
        ).fetchone()
        events = db.execute(
            "SELECT * FROM growth_events WHERE entity_id IN (?,?,?,?) "
            "ORDER BY created_at,event_id",
            (attempt_ids[0], attempt_ids[1], card["note_id"], card["task_id"]),
        ).fetchall()
    if (
        len(attempts) != 2
        or [row["attempt_id"] for row in attempts] != attempt_ids
        or any(
            row["actor_origin"] != "user"
            or row["answer_text"] != answer
            or row["answer_hash"] != sha256(answer.encode("utf-8"))
            for row, answer in zip(attempts, (A1, A2), strict=True)
        )
        or attempts[0]["parent_attempt_id"] is not None
        or attempts[1]["parent_attempt_id"] != attempt_ids[0]
        or attempts[1]["hint_level"] != 1
    ):
        raise AssertionError("A2 未独立保存，或覆盖了 A1/父链/提示级别")
    if task is None or task["progress"] != "completed" or len(notes) != 1:
        raise AssertionError("卡片结束状态或单条笔记不符")
    note = notes[0]
    source_refs = json.loads(task["source_refs_json"])
    if (
        note["note_id"] != card["note_id"]
        or note["author"] != "user"
        or note["revision"] != 1
        or note["content_text"] != N1
        or note["content_hash"] != sha256(N1.encode("utf-8"))
        or note["task_id"] != task["task_id"]
        or note["topic_id"] != task["topic_id"]
        or json.loads(note["source_refs_json"]) != source_refs
        or not source_refs
    ):
        raise AssertionError("笔记原文、哈希、修订或来源关联不符")
    if not any(
        row["source_type"] == "attempt"
        and row["source_id"] == attempt_ids[1]
        and row["evidence_label"] == "practicing"
        for row in timeline
    ) or any(row["evidence_label"] in {"independent", "mastered"} for row in timeline):
        raise AssertionError("修正回答能力标签越级或未进入时间线")
    if not any(
        row["source_type"] == "note"
        and row["source_id"] == note["note_id"]
        and row["actor_origin"] == "user"
        for row in timeline
    ):
        raise AssertionError("用户笔记未进入时间线")

    def event(event_type: str, entity_id: str, target: str | None = None) -> sqlite3.Row:
        matches = [
            row
            for row in events
            if row["event_type"] == event_type
            and row["entity_id"] == entity_id
            and (target is None or json.loads(row["payload_json"])["to"] == target)
        ]
        if len(matches) != 1:
            raise AssertionError(f"缺少唯一业务事件：{event_type} {entity_id}")
        return matches[0]

    correction_event = event("answer_saved", attempt_ids[1])
    completed_event = event("task_progress_changed", task["task_id"], "completed")
    note_event = event("note_saved", note["note_id"])
    common = {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "case_id": "S01",
        "status": "PASS",
        "device_id": manifest["environment"]["windows_node_id"],
    }
    records = [
        {
            **common,
            "step_id": "S01-07",
            "started_at": card["started_at"],
            "finished_at": card["finished_at"],
            "trace_id": correction_event["trace_id"],
            "related_trace_ids": [completed_event["trace_id"]],
            "action": "安装版手动输入 A2 并结束短卡学习",
            "expected": "A2 与 A1 分别保存、保留父链和一级提示，不越级标为独立设计",
            "actual": {
                "task_id": task["task_id"],
                "first_attempt_id": attempt_ids[0],
                "correction_attempt_id": attempt_ids[1],
                "hint_level": 1,
                "progress": task["progress"],
                "evidence_label": "practicing",
                "feedback_corrections": [],
            },
            "artifacts": [
                artifact(run, card_path),
                artifact(run, feedback_path),
                artifact(run, review_path),
            ],
        },
        {
            **common,
            "step_id": "S01-08",
            "started_at": card["started_at"],
            "finished_at": card["finished_at"],
            "trace_id": note_event["trace_id"],
            "action": "安装版检查笔记编辑器为空，手动输入 N1 并重载核验",
            "expected": "只保存用户正文，关联 task、topic 和来源，修订为 1",
            "actual": {
                "note_id": note["note_id"],
                "task_id": note["task_id"],
                "topic_id": note["topic_id"],
                "source_ref_ids": source_refs,
                "revision": note["revision"],
                "author": note["author"],
                "content_hash": note["content_hash"],
                "editor_empty_checked_by_ui_driver": True,
            },
            "artifacts": [artifact(run, card_path)],
        },
    ]
    with steps_path.open("a", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "report.md").open("a", encoding="utf-8") as output:
        output.write(
            "\n\n## S01-07/08 修正回答与手写笔记\n\n"
            "安装版界面手动输入 A2、完成短卡、核对空白笔记编辑器后输入 N1。"
            "SQLite 复核两条独立答案、父链、一级提示、未越级的 practicing 标签，"
            "以及用户笔记原文/哈希/修订/来源。"
            "正式 S01 仍待外部云、macOS、ReMe 与复习步骤。\n"
        )
    print(f"S01-07_08_PASS {note['note_id']}")


if __name__ == "__main__":
    main()
