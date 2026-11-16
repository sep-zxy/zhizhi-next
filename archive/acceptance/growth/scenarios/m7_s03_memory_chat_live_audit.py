"""Audit an installed real-model S03 memory chat as Windows-only rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from ahadiff.growth.cloud.db import connect
from m7_s03_note_outage import N1


QUESTION = "我对订单缓存到期有什么误解？现在怎么理解？"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    prepared = read(run / "preparation.json")
    ui = read(run / "artifacts/chat-ui.json")
    review = read(run / "artifacts/semantic-review.json")
    recovery = read(run.parent / prepared["recovery_run_id"] / "recovery.json")
    source = run.parent / prepared["source_run_id"]
    source_result = read(source / "result.json")
    note_ui = read(source / "artifacts/s03-09-note-ui.json")
    feedback_ui = read(source / "artifacts/s03-06-feedback-ui.json")
    account = uuid.UUID(prepared["account_id"])
    session = uuid.UUID(ui["session_id"])
    with connect(args.dsn) as conn:
        messages = conn.execute(
            "SELECT message_id,role,origin,content_text,provider_name,model_name,"
            "provider_request_id,request_payload_hash,response_payload_hash,"
            "input_tokens,output_tokens,elapsed_ms FROM growth_chat_messages "
            "WHERE account_id=%s AND session_id=%s ORDER BY created_at,message_id",
            (account, session)).fetchall()
        suggestion_count = conn.execute(
            "SELECT COUNT(*) AS n FROM growth_chat_suggestions WHERE account_id=%s AND session_id=%s",
            (account, session)).fetchone()["n"]
        note = conn.execute("SELECT content_text,revision FROM growth_notes WHERE account_id=%s AND note_id=%s",
                            (account, uuid.UUID(prepared["note_id"]))).fetchone()
        attempts = conn.execute(
            "SELECT attempt_id,parent_attempt_id,answer_text,feedback FROM growth_learning_attempts "
            "WHERE account_id=%s AND task_id=%s", (account, uuid.UUID(prepared["task_id"]))).fetchall()
    by_id = {str(item["attempt_id"]): item for item in attempts}
    first_id, second_id = prepared["attempt_ids"]
    first, second = by_id.get(first_id), by_id.get(second_id)
    user, assistant = messages if len(messages) == 2 else (None, None)
    receipt = ui["assistant"]
    answer = receipt["content_text"]
    checks = {
        "same_installed_candidate": source_result["checks"]["S03-09"]
        and hashlib.sha256(args.installer.read_bytes()).hexdigest() == prepared["candidate_sha256"]
        and ui["executable_sha256"] == note_ui["executable_sha256"]
        and ui["mode"] == "live_observation" and ui["status"] == "observed_pending_audit",
        "same_account_restarted_note": ui["reopened_after_projection"] and ui["reopened_note_exact"]
        and ui["sync"]["account_id"] == prepared["account_id"]
        and ui["sync"]["outbox"]["pending"] == 0
        and note is not None and note["revision"] == 1 and note["content_text"] == N1,
        "real_reme_recovery_source": recovery["status"] == "PASS"
        and recovery["account_id"] == prepared["account_id"]
        and all(recovery["checks"].values()),
        "approved_memory_has_n1_a1_a2": ui["preview"]["memory_status"] == "current"
        and prepared["note_id"] in ui["preview"]["note_ids"]
        and set(prepared["attempt_ids"]) <= set(ui["preview"]["attempt_ids"])
        and ui["preview"]["historical_attempts"]
        and ui["preview"]["provider_host"] == "https://api.deepseek.com"
        and ui["preview"]["model_name"] == "deepseek-flash",
        "original_answer_parent_feedback_retained": first is not None and second is not None
        and second["parent_attempt_id"] == first["attempt_id"]
        and "后台线程自动删除缓存" in first["answer_text"]
        and "没有下一次访问" in second["answer_text"]
        and first["feedback"] is not None and second["feedback"] is not None
        and [turn["attempt_id"] for turn in feedback_ui["turns"]] == prepared["attempt_ids"],
        "real_deepseek_reply_semantically_reviewed": review["status"] == "PASS"
        and review["question"] == QUESTION and all(review["checks"].values())
        and "误解" in answer and "后台" in answer and "历史错误" in answer
        and "修正" in answer and "下一次 get" in answer
        and "now < expiresAt" in answer and "HashMap" in answer,
        "cloud_chat_two_messages_and_receipt": user is not None and assistant is not None
        and user["role"] == "user" and user["origin"] == "user"
        and user["content_text"] == QUESTION
        and assistant["role"] == "assistant"
        and assistant["message_id"] == uuid.UUID(receipt["message_id"])
        and assistant["content_text"] == answer
        and assistant["provider_name"] == "deepseek"
        and assistant["model_name"] == "deepseek-flash"
        and assistant["request_payload_hash"] == ui["preview"]["payload_sha256"]
        == receipt["request_payload_hash"]
        and assistant["response_payload_hash"] == receipt["response_payload_hash"]
        and assistant["input_tokens"] > 0 and assistant["output_tokens"] > 0
        and assistant["elapsed_ms"] > 0 and ui["sync_state"] == "acked",
        "memory_question_no_new_topic": suggestion_count == 0 and receipt["suggestion"] is None,
        "formal_step_count_unchanged": not source_result["checks"]["S03-10"]
        and not source_result["checks"]["S03-11"] and not source_result["checks"]["S03-12"],
    }
    result = {"run_id": run.name, "mode": "windows_rehearsal_not_formal",
              "status": "PASS" if all(checks.values()) else "FAIL", "stage_status": "BLOCKED",
              "candidate_sha256": prepared["candidate_sha256"],
              "source_run_id": prepared["source_run_id"], "recovery_run_id": prepared["recovery_run_id"],
              "account_id": prepared["account_id"], "session_id": ui["session_id"],
              "checks": checks}
    (run / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
    (run / "report.md").write_text(
        "# S03 同包 Windows 新会话真实模型演练\n\n"
        f"结果：**{result['status']}（{sum(checks.values())}/{len(checks)}）**。"
        "安装版重启后读取 N1，成长云从真实 ReMe 检索并按账号、版本回源 A1/A2/N1，"
        "用户批准请求后 DeepSeek Flash 回答过去误解及修正。"
        "测试云曾缺 ReMe URL、测试令牌曾过期，失败尝试保留。"
        "缺 macOS 实机与外部云，正式 S03-10 至 S03-12 不计 PASS。\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "checks": checks}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
