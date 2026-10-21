"""Audit local card, correction, and note evidence from an installed UI rehearsal."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    prepared = read_json(run / "preparation.json")
    ui = read_json(run / "artifacts" / "card-ui.json")
    model = read_json(run.parent / prepared["source_run_id"] / "audit.json")
    model_ui = read_json(run.parent / prepared["source_run_id"] / "artifacts" / "model-ui.json")
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        proposal = db.execute(
            "SELECT * FROM topic_proposals WHERE proposal_id=?", (prepared["proposal_id"],),
        ).fetchone()
        task = db.execute("SELECT * FROM growth_tasks WHERE task_id=?", (ui["task_id"],)).fetchone()
        attempts = [dict(row) for row in db.execute(
            "SELECT * FROM learning_attempts WHERE task_id=? ORDER BY created_at,rowid", (ui["task_id"],)
        )]
        hints = [dict(row) for row in db.execute(
            "SELECT * FROM growth_task_hints WHERE task_id=? ORDER BY level", (ui["task_id"],)
        )]
        notes = [dict(row) for row in db.execute(
            "SELECT * FROM engineering_notes WHERE task_id=?", (ui["task_id"],)
        )]
        events = [row[0] for row in db.execute(
            "SELECT event_type FROM growth_events WHERE entity_id IN (?,?,?,?)",
            (prepared["proposal_id"], ui["task_id"], ui["first_attempt_id"], ui["correction_attempt_id"]),
        )]
    checks = {
        "source_replay_passed": model["status"] == "PASS"
        and model["mode"] == prepared["mode"] == ui["mode"] == "replay_not_live"
        and model["candidate_sha256"] == prepared["candidate_sha256"]
        and model_ui["executable_sha256"] == ui["executable_sha256"],
        "confirmed_topic_without_duplicate": proposal is not None
        and proposal["status"] == "confirmed" and proposal["topic_id"] == ui["topic_id"],
        "card_from_selected_opportunity": task is not None
        and task["opportunity_id"] == prepared["opportunity_id"]
        and task["topic_id"] == ui["topic_id"]
        and task["question"] == ui["question"],
        "original_error_preserved": len(attempts) == 2
        and attempts[0]["attempt_id"] == ui["first_attempt_id"]
        and attempts[0]["actor_origin"] == "user"
        and attempts[0]["answer_text"] ==
            "CompletableFuture 每调用一次 supplyAsync 就新建一个线程，join 只是把两个结果放到一起，不会等待。",
        "hint_and_correction_linked": len(hints) == 1 and len(attempts) == 2
        and hints[0]["hint_id"] == ui["hint"]["hint_id"]
        and hints[0]["level"] == attempts[1]["hint_level"] == 1
        and attempts[1]["parent_attempt_id"] == ui["first_attempt_id"]
        and attempts[1]["attempt_id"] == ui["correction_attempt_id"],
        "scenario_correction_exact": len(attempts) == 2
        and attempts[1]["answer_text"] ==
            "两个查询任务交给传入的 executor，线程是不是新建由执行器决定。thenCombine 组合结果，最后 join 会等待组合结果，调用 plan 的线程仍可能被阻塞。",
        "pause_resume_complete_persisted": task is not None and task["progress"] == "completed"
        and events.count("task_progress_changed") == 4,
        "user_note_persisted": len(notes) == 1 and notes[0]["note_id"] == ui["note_id"]
        and notes[0]["author"] == "user"
        and notes[0]["content_text"] == ui["chain"]["notes"][0]["content_text"]
        and notes[0]["content_text"] ==
            "我以前把 CompletableFuture 当成“每个任务自动新建线程”。这次代码把两个查询交给同一个 executor，实际线程由执行器安排。thenCombine 组合结果，join 会等待，所以这段代码并不意味着调用方从头到尾都不阻塞。以后排查并发问题，我要先找执行器在哪里配置。",
        "installed_ui_reload_observed": ui["status"] == "observed"
        and ui["chain"]["task"]["progress"] == "completed"
        and len(ui["chain"]["attempts"]) == 2,
    }
    audit = {
        "run_id": prepared["run_id"],
        "mode": "replay_not_live",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "scope": "Windows 安装版本地短卡片、错误回答、提示、修正、进度与笔记预演",
        "candidate_sha256": prepared["candidate_sha256"],
        "source_run_id": prepared["source_run_id"],
        "task_id": ui["task_id"],
        "checks": checks,
    }
    (run / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(audit, ensure_ascii=False))
    if audit["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
