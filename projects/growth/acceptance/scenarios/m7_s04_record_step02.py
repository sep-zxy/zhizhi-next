"""Audit and record the installed, real-provider S04 two-turn conversation."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from ahadiff.growth.cloud.db import connect


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def artifact(run: Path, path: Path) -> dict[str, str]:
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
    chain = read(run / "chain.json")
    local_setup = read(run / "artifacts" / "s04-01-rehearsal-result.json")
    local_run = run.parent / local_setup["run_id"]
    local_result = read(local_run / "result.json")
    old_ui = read(run / "artifacts" / "s04-01-windows-ui.json")
    ui_path = run / "artifacts" / "s04-02-chat-live-ui.json"
    review_path = run / "artifacts" / "s04-02-semantic-review.json"
    ui, review = read(ui_path), read(review_path)
    steps = [json.loads(line) for line in (run / "steps.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    digest = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    if manifest["case_id"] != "S04" or manifest["candidate_sha256"] != digest:
        raise AssertionError("安装包与正式 S04 候选不一致")
    if [step["step_id"] for step in steps] != ["S04-01"] or steps[0]["status"] != "PASS":
        raise AssertionError("S04-01 必须先通过，且不能重复记录 S04-02")
    if ui["mode"] != "live_observation" or ui["status"] != "observed_pending_semantic_review":
        raise AssertionError("双轮必须来自安装版真实模型观察")
    if (ui["workspace"] != str(run / "fixture" / "repo")
            or ui["account_id"] != old_ui["account_id"]):
        raise AssertionError("S04-02 工作区或账号与 S04-01 不一致")
    if ui["topics_before"] != ui["topics_after"] or len(ui["topics_after"]) != 1:
        raise AssertionError("用户未确认前，正式主题数量必须保持为 1")
    if ui["topics_after"][0]["id"] != chain["topic_id"]:
        raise AssertionError("旧主题不是 S04-01 的主题")
    if review["verdict"] != "PASS" or review["session_id"] != ui["session_id"] \
            or review["candidate_sha256"] != digest or not all(review["checks"].values()):
        raise AssertionError("真实回复缺少通过的逐项语义复核")
    if len(ui["turns"]) != 2 or not ui["pending_suggestion_visible"]:
        raise AssertionError("没有完整双轮对话或待确认主题")

    expected_messages = []
    expected_suggestions = []
    previous_reply = None
    for index, turn in enumerate(ui["turns"]):
        if turn["index"] != index + 1 or turn["sync_state"] != "acked":
            raise AssertionError("双轮云回执不完整")
        preview = turn["preview"]
        payload = preview["payload_text"]
        context = json.loads(payload.split("\n", 1)[1])["messages"]
        if context[-1] != {"role": "user", "content": turn["user_message"]}:
            raise AssertionError("批准载荷缺少当前用户输入")
        if previous_reply is not None and context[-2] != {
            "role": "assistant", "content": previous_reply,
        }:
            raise AssertionError("第二轮没有回带第一轮真实回复")
        receipt = turn["assistant_receipt"]
        if (receipt["provider_name"] != "deepseek"
                or receipt["model_name"] != "deepseek-flash"
                or receipt["request_payload_hash"]
                != hashlib.sha256(payload.encode("utf-8")).hexdigest()
                or not receipt["response_payload_hash"]):
            raise AssertionError("真实模型身份或请求哈希不匹配")
        expected_messages.extend((
            ("user", turn["user_message"]), ("assistant", receipt["content_text"]),
        ))
        if receipt["suggestion"]:
            expected_suggestions.append(receipt["suggestion"])
        previous_reply = receipt["content_text"]
    if not ui["turns"][1]["assistant_receipt"]["suggestion"]:
        raise AssertionError("第二轮没有待用户确认的主题建议")

    account = uuid.UUID(ui["account_id"])
    session = uuid.UUID(ui["session_id"])
    with connect(args.dsn) as conn:
        messages = conn.execute(
            "SELECT message_id, role, origin, content_text, provider_name, model_name, "
            "request_payload_hash, response_payload_hash FROM growth_chat_messages "
            "WHERE account_id=%s AND session_id=%s ORDER BY created_at, message_id",
            (account, session),
        ).fetchall()
        suggestions = conn.execute(
            "SELECT suggestion_id, title, reason, status, topic_id "
            "FROM growth_chat_suggestions WHERE account_id=%s AND session_id=%s",
            (account, session),
        ).fetchall()
        topics = conn.execute(
            "SELECT topic_id, title FROM growth_topics WHERE account_id=%s "
            "AND deleted_at IS NULL", (account,),
        ).fetchall()
        note = conn.execute(
            "SELECT content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(chain["note_id"])),
        ).fetchone()
        all_events = conn.execute(
            "SELECT event_id, trace_id, entity_type, entity_id, event_type, payload, "
            "occurred_at FROM growth_domain_events WHERE account_id=%s ORDER BY occurred_at",
            (account,),
        ).fetchall()
    events = [dict(row) for row in all_events if row["payload"].get("session_id")
              == ui["session_id"]]
    actual_suggestions = {str(item["suggestion_id"]): item for item in suggestions}
    checks = {
        "same_installed_candidate": local_result["status"] == "PASS"
        and local_result["source_run_id"] == run.name
        and local_result["candidate_sha256"] == digest
        and Path(ui["executable"]).resolve()
        == (local_run / "installed" / "工程成长伴侣.exe").resolve()
        and ui["executable_sha256"] == hashlib.sha256(
            Path(ui["executable"]).read_bytes()).hexdigest(),
        "two_live_cloud_messages": len(messages) == 4 and all(
            (row["role"], row["content_text"]) == expected_messages[index]
            for index, row in enumerate(messages)),
        "provider_receipts_preserved": len(messages) == 4 and all(
            messages[2 * index + 1]["origin"] == "live"
            and messages[2 * index + 1]["message_id"]
            == uuid.UUID(ui["turns"][index]["assistant_receipt"]["message_id"])
            and messages[2 * index + 1]["provider_name"] == "deepseek"
            and messages[2 * index + 1]["model_name"] == "deepseek-flash"
            and messages[2 * index + 1]["request_payload_hash"]
            == ui["turns"][index]["assistant_receipt"]["request_payload_hash"]
            and messages[2 * index + 1]["response_payload_hash"]
            == ui["turns"][index]["assistant_receipt"]["response_payload_hash"]
            for index in range(2)),
        "suggestions_pending": len(suggestions) == len(expected_suggestions)
        and all((item := actual_suggestions.get(expected["suggestion_id"])) is not None
                and item["title"] == expected["title"]
                and item["reason"] == expected["reason"]
                and item["status"] == "pending" and item["topic_id"] is None
                for expected in expected_suggestions),
        "old_topic_and_note_preserved": len(topics) == 1
        and str(topics[0]["topic_id"]) == chain["topic_id"]
        and topics[0]["title"] == "登录机制" and note is not None
        and hashlib.sha256(note["content_text"].encode("utf-8")).hexdigest()
        == steps[0]["actual"]["note_sha256"],
        "domain_event_chain": len(events) == 1 + 2 * 2 + len(expected_suggestions)
        and sum(item["event_type"] == "chat_assistant_live" for item in events) == 2,
        "semantic_review": review["verdict"] == "PASS",
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    cloud_path = run / "artifacts" / "s04-02-cloud-audit.json"
    write(cloud_path, {"account_id": account, "session_id": session,
                       "message_count": len(messages),
                       "suggestions": [dict(item) for item in suggestions],
                       "formal_topics": [dict(item) for item in topics],
                       "checks": checks})
    record = {
        "schema_version": 1, "run_id": manifest["run_id"], "case_id": "S04",
        "step_id": "S04-02", "status": "PASS",
        "started_at": ui["started_at"], "finished_at": ui["finished_at"],
        "device_id": steps[0]["device_id"],
        "trace_id": str(events[0]["trace_id"]),
        "related_trace_ids": [str(item["trace_id"]) for item in events[1:]],
        "action": "安装版发送 U1/U2，逐轮预览批准真实 DeepSeek 请求，不确认主题",
        "expected": "两轮回复正确区分认证与授权；建议待确认；正式主题仍只有 T-old",
        "actual": {"cloud_kind": "isolated_local_postgresql",
                   "session_id": ui["session_id"], "message_count": len(messages),
                   "pending_suggestion_id": expected_suggestions[-1]["suggestion_id"],
                   "formal_topic_count": len(topics), "checks": checks},
        "artifacts": [artifact(run, item) for item in
                      (ui_path, review_path, cloud_path)],
    }
    with (run / "steps.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    chain["chat_session_id"] = ui["session_id"]
    chain["pending_suggestion_id"] = expected_suggestions[-1]["suggestion_id"]
    write(run / "chain.json", chain)
    current = read(run / "result.json")
    current.setdefault("proof", {}).update({
        "model_provider": artifact(run, ui_path),
        "semantic_review": artifact(run, review_path),
    })
    write(run / "result.json", current)
    with (run / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\n\n## S04-02 Windows 安装版真实对话\n\n"
                     "U1/U2 均经界面预览、批准和真实 DeepSeek Flash 回复，"
                     "四条消息与建议持久保存在隔离 PostgreSQL。"
                     "逐句语义复核通过，确认前正式主题仍只有 T-old。"
                     "证据见 `artifacts/s04-02-*`；外部云与 Mac 仍未验证。\n")
    print(f"S04-02_PASS {ui['session_id']} {expected_suggestions[-1]['suggestion_id']}")


if __name__ == "__main__":
    main()
