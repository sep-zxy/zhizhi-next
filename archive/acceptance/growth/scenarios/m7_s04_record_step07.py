"""Audit and record S04's live feedback, completed card, and hand-written note."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from m7_s04_record_step02 import artifact, read, write

from ahadiff.growth.cloud.db import connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = read(run / "manifest.json")
    chain = read(run / "chain.json")
    prior_path = run / "artifacts" / "s04-07-answer-note-live-ui.json"
    finish_path = run / "artifacts" / "s04-07-answer-note-finish-ui.json"
    review_path = run / "artifacts" / "s04-07-semantic-review.json"
    prior = read(prior_path)
    if prior.get("status") == "observed_pending_semantic_review" and prior.get("note_id"):
        finish_path = prior_path
    finish, review = read(finish_path), read(review_path)
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    steps = [json.loads(line) for line in (run / "steps.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != digest:
        raise AssertionError("安装包与 S04 候选不一致")
    if [step["step_id"] for step in steps] != [f"S04-{number:02d}" for number in range(1, 7)] \
            or any(step["status"] != "PASS" for step in steps):
        raise AssertionError("S04-01 至 S04-06 必须先连续通过")
    if review["verdict"] != "PASS" or not all(review["checks"].values()) \
            or review["candidate_sha256"] != digest \
            or review["attempt_id"] != finish["attempt_id"]:
        raise AssertionError("真实反馈的独立语义复核未通过")
    local_setup = read(run / "artifacts" / "s04-01-rehearsal-result.json")
    local_run = run.parent / local_setup["run_id"]
    local_result = read(local_run / "result.json")
    installed_exe = local_run / "installed" / "工程成长伴侣.exe"
    account_id = uuid.UUID(finish["account_id"])
    attempt_id = uuid.UUID(finish["attempt_id"])
    note_id = uuid.UUID(finish["note_id"])
    task_id = uuid.UUID(finish["task_id"])
    topic_id = uuid.UUID(finish["topic_id"])
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        db.row_factory = sqlite3.Row
        attempt = db.execute(
            "SELECT * FROM learning_attempts WHERE attempt_id=?", (str(attempt_id),)
        ).fetchone()
        request = db.execute(
            "SELECT * FROM feedback_model_requests WHERE attempt_id=?", (str(attempt_id),)
        ).fetchone()
        response = db.execute(
            "SELECT * FROM feedback_model_responses WHERE attempt_id=?", (str(attempt_id),)
        ).fetchone()
        note = db.execute(
            "SELECT * FROM engineering_notes WHERE note_id=?", (str(note_id),)
        ).fetchone()
        task = db.execute(
            "SELECT * FROM growth_tasks WHERE task_id=?", (str(task_id),)
        ).fetchone()
        note_event = db.execute(
            "SELECT * FROM growth_events WHERE entity_type='note' AND entity_id=? "
            "AND event_type='note_saved'", (str(note_id),),
        ).fetchone()
        pending = db.execute(
            "SELECT count(*) FROM sync_outbox WHERE account_id=? AND state='pending'",
            (str(account_id),),
        ).fetchone()[0]
    with connect(args.dsn) as cloud:
        cloud_attempt = cloud.execute(
            "SELECT answer_text, status, feedback, feedback_origin, actor_origin "
            "FROM growth_learning_attempts WHERE account_id=%s AND attempt_id=%s",
            (account_id, attempt_id),
        ).fetchone()
        cloud_note = cloud.execute(
            "SELECT task_id, topic_id, content_text, author, source_refs, revision "
            "FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account_id, note_id),
        ).fetchone()
        cloud_task = cloud.execute(
            "SELECT progress FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchone()
        old_note = cloud.execute(
            "SELECT content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account_id, uuid.UUID(chain["note_id"])),
        ).fetchone()
        cloud_note_event = cloud.execute(
            "SELECT payload FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='note' AND entity_id=%s AND event_type='note_saved'",
            (account_id, note_id),
        ).fetchone()
    answer_hash = hashlib.sha256(finish["answer_text"].encode()).hexdigest()
    note_hash = hashlib.sha256(finish["note_text"].encode()).hexdigest()
    checks = {
        "same_installed_task_and_account": prior["mode"] == finish["mode"] == "live_observation"
        and prior["executable_sha256"] == finish["executable_sha256"]
        and finish["executable_sha256"] == hashlib.sha256(installed_exe.read_bytes()).hexdigest()
        and local_result["candidate_sha256"] == digest
        and local_result["source_run_id"] == run.name
        and prior["workspace"] == finish["workspace"] == str(run / "fixture" / "repo")
        and prior["account_id"] == finish["account_id"]
        and prior["task_id"] == finish["task_id"] == chain["task_id"]
        and finish["topic_id"] == chain["new_topic_id"]
        and finish["status"] == "observed_pending_semantic_review",
        "exact_user_answer_and_live_feedback": attempt is not None and cloud_attempt is not None
        and attempt["answer_text"] == cloud_attempt["answer_text"] == finish["answer_text"]
        and attempt["answer_hash"] == answer_hash
        and attempt["actor_origin"] == cloud_attempt["actor_origin"] == "user"
        and attempt["status"] == cloud_attempt["status"] == "feedback_ready"
        and attempt["feedback_origin"] == cloud_attempt["feedback_origin"] == "live"
        and json.loads(attempt["feedback_json"]) == cloud_attempt["feedback"] == finish["feedback"],
        "approved_source_and_real_model": request is not None and response is not None
        and request["provider_name"] == "deepseek" and request["status"] == "succeeded"
        and request["approved_payload_hash"] == prior["preview"]["approval_hash"]
        and hashlib.sha256(request["approved_payload_text"].encode()).hexdigest()
        == prior["preview"]["payload_sha256"]
        and finish["answer_text"] in request["approved_payload_text"]
        and "src/access.ts" in request["approved_payload_text"]
        and json.loads(response["response_text"]) == finish["feedback"]
        and response["input_tokens"] > 0 and response["output_tokens"] > 0,
        "card_completed": task is not None and cloud_task is not None
        and task["progress"] == cloud_task["progress"] == "completed",
        "hand_written_note_with_source": note is not None and cloud_note is not None
        and note_event is not None and cloud_note_event is not None
        and note["task_id"] == str(task_id) and note["topic_id"] == str(topic_id)
        and cloud_note["task_id"] == task_id and cloud_note["topic_id"] == topic_id
        and note["author"] == cloud_note["author"] == "user"
        and note["content_text"] == cloud_note["content_text"] == finish["note_text"]
        and note["content_hash"] == note_hash and cloud_note["revision"] == 1
        and json.loads(note["source_refs_json"]) == finish["source_refs"]
        and [str(item) for item in cloud_note["source_refs"]] == finish["source_refs"],
        "old_history_and_sync_preserved": old_note is not None
        and old_note["content_text"] == "登录成功说明应用识别了用户身份。"
        and pending == 0 and finish["final_sync"]["queued"] == 0,
    }
    audit_path = run / "artifacts" / "s04-07-audit.json"
    write(audit_path, {
        "step_id": "S04-07", "status": "PASS" if all(checks.values()) else "FAIL",
        "candidate_sha256": digest, "cloud_kind": "isolated_local_postgresql",
        "account_id": str(account_id), "task_id": str(task_id),
        "attempt_id": str(attempt_id), "note_id": str(note_id),
        "answer_sha256": answer_hash, "note_sha256": note_hash, "checks": checks,
    })
    if not all(checks.values()):
        raise AssertionError(checks)
    record = {
        "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S04",
        "step_id": "S04-07", "status": "PASS",
        "started_at": prior["started_at"], "finished_at": finish["finished_at"],
        "device_id": steps[0]["device_id"], "trace_id": note_event["trace_id"],
        "action": "安装版提交 A1、预览批准真实模型反馈、完成卡片并从空白框亲写 N1",
        "expected": "反馈区分认证和授权；原文笔记与代码来源保留；旧历史不丢失",
        "actual": {"cloud_kind": "isolated_local_postgresql",
                   "task_id": str(task_id), "attempt_id": str(attempt_id),
                   "note_id": str(note_id), "checks": checks},
        "artifacts": [artifact(run, item) for item in
                      dict.fromkeys((prior_path, finish_path, review_path, audit_path))],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(note_event), ensure_ascii=False, default=str) + "\n")
    chain["attempt_ids"] = [str(attempt_id)]
    chain["new_note_id"] = str(note_id)
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n## S04-07 真实反馈、完成卡片与亲写笔记\n\n"
                     "A1 经安装版预览批准后送达真实 DeepSeek；反馈指出 Bob 非所有者的 "
                     "FORBIDDEN 分支，并补充未登录时的 UNAUTHENTICATED 分支。安装版"
                     "完成卡片并从空白框输入 N1。SQLite、PostgreSQL 与来源引用审计见 "
                     "`artifacts/s04-07-*`。\n")
    print(f"S04-07_PASS {attempt_id} {note_id}")


if __name__ == "__main__":
    main()
