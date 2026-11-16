"""Audit and record the live code opportunity, topic reuse, and short card."""

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
    prior_path = run / "artifacts" / "s04-06-reuse-live-ui-attempt1.json"
    resume_path = run / "artifacts" / "s04-06-card-resume-ui.json"
    review_path = run / "artifacts" / "s04-06-semantic-review.json"
    prior = read(prior_path)
    if prior.get("status") == "observed_pending_semantic_review" and prior.get("task_id"):
        resume_path = prior_path
    resume, review = read(resume_path), read(review_path)
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    local_setup = read(run / "artifacts" / "s04-01-rehearsal-result.json")
    local_run = run.parent / local_setup["run_id"]
    local_result = read(local_run / "result.json")
    installed_exe = local_run / "installed" / "工程成长伴侣.exe"
    steps = [json.loads(line) for line in (run / "steps.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != digest:
        raise AssertionError("安装包与 S04 候选不一致")
    if [step["step_id"] for step in steps] != [f"S04-{number:02d}" for number in range(1, 6)] \
            or any(step["status"] != "PASS" for step in steps):
        raise AssertionError("S04-01 至 S04-05 必须先连续通过")
    if review["verdict"] != "PASS" or not all(review["checks"].values()) \
            or review["candidate_sha256"] != digest \
            or review["analysis_id"] != prior["analysis_id"]:
        raise AssertionError("真实模型机会的语义复核未通过")

    workspace = run / "fixture" / "repo"
    db_path = workspace / ".ahadiff" / "growth.sqlite"
    with sqlite3.connect(db_path) as local:
        local.row_factory = sqlite3.Row
        analysis = local.execute(
            "SELECT snapshot_id, mode, status FROM analysis_runs WHERE analysis_id=?",
            (prior["analysis_id"],),
        ).fetchone()
        request = local.execute(
            "SELECT * FROM analysis_model_requests WHERE analysis_id=?",
            (prior["analysis_id"],),
        ).fetchone()
        response = local.execute(
            "SELECT * FROM analysis_model_responses WHERE analysis_id=?",
            (prior["analysis_id"],),
        ).fetchone()
        batch = local.execute(
            "SELECT * FROM opportunity_batches WHERE analysis_id=?",
            (prior["analysis_id"],),
        ).fetchone()
        opportunity = local.execute(
            "SELECT * FROM opportunities WHERE opportunity_id=?",
            (prior["opportunity_id"],),
        ).fetchone()
        proposal = local.execute(
            "SELECT * FROM topic_proposals WHERE proposal_id=?",
            (prior["proposal_id"],),
        ).fetchone()
        task = local.execute(
            "SELECT * FROM growth_tasks WHERE task_id=?", (resume["task_id"],)
        ).fetchone()
        reuse_event = local.execute(
            "SELECT * FROM growth_events WHERE entity_type='topic_proposal' "
            "AND entity_id=? AND event_type='topic_confirmed'",
            (prior["proposal_id"],),
        ).fetchone()
        task_event = local.execute(
            "SELECT * FROM growth_events WHERE entity_type='task' "
            "AND entity_id=? AND event_type='task_created'",
            (resume["task_id"],),
        ).fetchone()
        pending = local.execute(
            "SELECT count(*) FROM sync_outbox WHERE account_id=? AND state='pending'",
            (prior["account_id"],),
        ).fetchone()[0]
        local_topic_count = local.execute(
            "SELECT count(*) FROM growth_topics WHERE topic_id=? AND status='active'",
            (chain["new_topic_id"],),
        ).fetchone()[0]
    account_id = uuid.UUID(prior["account_id"])
    with connect(args.dsn) as cloud:
        topics = cloud.execute(
            "SELECT topic_id, title FROM growth_topics WHERE account_id=%s "
            "AND deleted_at IS NULL", (account_id,),
        ).fetchall()
        cloud_proposal = cloud.execute(
            "SELECT topic_id, status FROM growth_topic_proposals "
            "WHERE account_id=%s AND proposal_id=%s",
            (account_id, uuid.UUID(prior["proposal_id"])),
        ).fetchone()
        cloud_task = cloud.execute(
            "SELECT topic_id, opportunity_id, question FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s",
            (account_id, uuid.UUID(resume["task_id"])),
        ).fetchone()
        cloud_reuse = cloud.execute(
            "SELECT payload FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='topic_proposal' AND entity_id=%s "
            "AND event_type='topic_confirmed'",
            (account_id, uuid.UUID(prior["proposal_id"])),
        ).fetchone()
        cloud_card = cloud.execute(
            "SELECT payload FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='task' AND entity_id=%s AND event_type='task_created'",
            (account_id, uuid.UUID(resume["task_id"])),
        ).fetchone()

    checks = {
        "same_installed_candidate": prior["mode"] == resume["mode"] == "live_observation"
        and prior["executable_sha256"] == resume["executable_sha256"]
        and resume["executable_sha256"] == hashlib.sha256(installed_exe.read_bytes()).hexdigest()
        and local_result["candidate_sha256"] == digest
        and local_result["source_run_id"] == run.name
        and prior["workspace"] == resume["workspace"] == str(workspace)
        and prior["account_id"] == resume["account_id"]
        and resume["status"] == "observed_pending_semantic_review",
        "approved_two_source_request": analysis is not None and request is not None
        and analysis["snapshot_id"] == chain["snapshot_id"]
        and analysis["mode"] == "live" and analysis["status"] == "succeeded"
        and request["provider_name"] == "deepseek"
        and request["approved_payload_hash"] == prior["preview"]["approval_hash"]
        and hashlib.sha256(request["approved_payload_text"].encode()).hexdigest()
        == prior["preview"]["payload_sha256"]
        and json.loads(request["source_ref_ids_json"]) == prior["preview"]["source_ref_ids"]
        and len(prior["preview"]["source_ref_ids"]) == 2
        and "src/access.ts" in request["selected_patch"]
        and "src/document.ts" in request["selected_patch"]
        and "Web 认证与授权" in request["approved_payload_text"],
        "real_model_and_grounded_question": response is not None and batch is not None
        and opportunity is not None and batch["origin"] == "live"
        and batch["provider_name"] == "deepseek"
        and batch["model_name"] == "deepseek-flash"
        and opportunity["analysis_id"] == prior["analysis_id"]
        and "Bob" in opportunity["learning_goal"]
        and "Alice" in opportunity["learning_goal"]
        and "canEdit" in opportunity["reason"]
        and "ownerId" in opportunity["reason"]
        and "FORBIDDEN" in opportunity["reason"],
        "existing_topic_reused_once": proposal is not None and reuse_event is not None
        and cloud_proposal is not None and cloud_reuse is not None
        and proposal["status"] == "confirmed"
        and proposal["topic_id"] == chain["new_topic_id"]
        and proposal["title"] == "Web 认证与授权"
        and json.loads(reuse_event["payload_json"])["reuse_existing"] is True
        and str(cloud_proposal["topic_id"]) == chain["new_topic_id"]
        and cloud_proposal["status"] == "confirmed"
        and cloud_reuse["payload"]["reuse_existing"] is True
        and local_topic_count == 1
        and {str(item["topic_id"]) for item in topics}
        == {chain["topic_id"], chain["new_topic_id"]},
        "short_card_on_same_topic": task is not None and task_event is not None
        and cloud_task is not None and cloud_card is not None
        and task["opportunity_id"] == prior["opportunity_id"]
        and task["topic_id"] == chain["new_topic_id"]
        and 3 <= task["estimated_minutes"] <= 5
        and "Bob" in task["question"] and "Alice" in task["question"]
        and str(cloud_task["topic_id"]) == chain["new_topic_id"]
        and str(cloud_task["opportunity_id"]) == prior["opportunity_id"]
        and cloud_task["question"] == task["question"]
        and cloud_card["payload"]["opportunity_id"] == prior["opportunity_id"]
        and "Bob" in resume["card_text"] and "Alice" in resume["card_text"],
        "durable_sync_complete": pending == 0 and resume["final_sync"]["queued"] == 0,
    }
    audit_path = run / "artifacts" / "s04-06-audit.json"
    write(audit_path, {
        "step_id": "S04-06", "status": "PASS" if all(checks.values()) else "FAIL",
        "candidate_sha256": digest, "cloud_kind": "isolated_local_postgresql",
        "account_id": str(account_id), "analysis_id": prior["analysis_id"],
        "opportunity_id": prior["opportunity_id"], "proposal_id": prior["proposal_id"],
        "topic_id": chain["new_topic_id"], "task_id": resume["task_id"],
        "checks": checks,
    })
    if not all(checks.values()):
        raise AssertionError(checks)
    record = {
        "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S04",
        "step_id": "S04-06", "status": "PASS",
        "started_at": prior["started_at"], "finished_at": resume["finished_at"],
        "device_id": steps[0]["device_id"], "trace_id": task_event["trace_id"],
        "action": "安装版批准双文件模型请求，真实 DeepSeek 生成授权机会，复用已有主题并创建短卡",
        "expected": "Bob/Alice 资源授权题回到 T-new；不重复创建主题；模型请求与代码快照一致",
        "actual": {"cloud_kind": "isolated_local_postgresql",
                   "analysis_id": prior["analysis_id"], "task_id": resume["task_id"],
                   "topic_id": chain["new_topic_id"], "checks": checks},
        "artifacts": [artifact(run, item) for item in
                      dict.fromkeys((prior_path, resume_path, review_path, audit_path))],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(task_event), ensure_ascii=False, default=str) + "\n")
    chain["analysis_id"] = prior["analysis_id"]
    chain["opportunity_id"] = prior["opportunity_id"]
    chain["proposal_id"] = prior["proposal_id"]
    chain["task_id"] = resume["task_id"]
    write(run / "chain.json", chain)
    with (run / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n## S04-06 真实代码机会与主题复用\n\n"
                     "安装版批准 `access.ts` 和 `document.ts` 的模型请求；真实 DeepSeek "
                     "提出 Bob/Alice 文档授权问题。安装版复用已确认的 T-new，创建短卡并同步。"
                     "SQLite 与 PostgreSQL 审计确认短卡指向既有 T-new，正式主题仍只有"
                     " T-old/T-new。证据见 `artifacts/s04-06-*`。\n")
    print(f"S04-06_PASS {resume['task_id']} {chain['new_topic_id']}")


if __name__ == "__main__":
    main()
