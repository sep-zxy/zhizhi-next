"""Replay S04 chat interest and explicit topic decisions on real PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from m1_cloud_smoke import ISSUER, token
from psycopg import sql

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.sync import GrowthSyncStore

ROOT = Path(__file__).resolve().parents[2]
U1 = "我最近总看到 JWT，它是不是说明登录以后就什么接口都能调用？我有点分不清。"
U2 = "那我更想搞清楚“你是谁”和“你能改什么”怎么分开。"


async def exercise(run_dir: Path, dsn: str) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    verifier = AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)
    account, foreign = uuid.uuid4(), uuid.uuid4()
    device, second_device, foreign_device = (uuid.uuid4() for _ in range(3))
    old_topic, new_topic, session_id, suggestion_id = (uuid.uuid4() for _ in range(4))
    headers = {"Authorization": f"Bearer {token(private_key, account)}",
               "X-Device-Id": str(device)}
    second_headers = {**headers, "X-Device-Id": str(second_device)}
    foreign_headers = {"Authorization": f"Bearer {token(private_key, foreign)}",
                       "X-Device-Id": str(foreign_device)}
    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []
    acceptance = create_app(CloudSettings(dsn, verifier, acceptance_mode=True))
    production = create_app(CloudSettings(dsn, verifier))

    async def call(client: httpx.AsyncClient, method: str, path: str,
                   expected: int, label: str, *, body: dict[str, Any] | None = None,
                   use_headers: dict[str, str] | None = None) -> dict[str, Any]:
        response = await client.request(method, path, json=body,
                                        headers=use_headers or headers)
        actual = response.json()
        entry = {"step": label, "time": datetime.now(UTC).isoformat(),
                 "expected_status": expected, "actual_status": response.status_code,
                 "operation_id": body.get("operation_id") if body else None,
                 "actual": actual,
                 "verdict": "PASS" if response.status_code == expected else "FAIL"}
        steps.append(entry)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        if response.status_code != expected:
            raise AssertionError(f"{label}: {response.status_code} != {expected}: {actual}")
        return actual

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=acceptance), base_url="http://growth.test"
    ) as client:
        for owner, identifier in ((account, device), (account, second_device),
                                  (foreign, foreign_device)):
            response = await client.post(
                "/v1/devices", json={"operation_id": str(uuid.uuid4()),
                                     "device_id": str(identifier)},
                headers={"Authorization": f"Bearer {token(private_key, owner)}"},
            )
            assert response.status_code == 201, response.text
        await call(client, "POST", "/v1/topics", 201, "create_prior_topic",
                   body={"operation_id": str(uuid.uuid4()),
                         "topic_id": str(old_topic), "title": "登录机制"})
        await call(client, "POST", "/v1/chats", 201, "start_free_chat",
                   body={"operation_id": str(uuid.uuid4()),
                         "session_id": str(session_id)})
        for label, text in (("jwt_question", U1), ("identity_vs_permission", U2)):
            await call(client, "POST", f"/v1/chats/{session_id}/messages", 201,
                       label, body={"operation_id": str(uuid.uuid4()),
                                    "message_id": str(uuid.uuid4()),
                                    "content_text": text})
        listed = await call(client, "GET", "/v1/chats", 200,
                            "list_chat_sessions")
        checks["chat_list_contains_session"] = any(
            item["session_id"] == str(session_id) for item in listed["sessions"]
        )
        replay_body = {"operation_id": str(uuid.uuid4()),
                       "message_id": str(uuid.uuid4()),
                       "content_text": "登录识别身份，访问资源还要做授权检查。",
                       "suggestion": {"suggestion_id": str(suggestion_id),
                                      "title": "Web 认证与授权",
                                      "reason": "用户连续追问身份与资源权限的区别"}}
        replay = await call(client, "POST",
                            f"/v1/chats/{session_id}/assistant-replay", 201,
                            "assistant_replay_suggests_topic", body=replay_body)
        repeated = await call(client, "POST",
                              f"/v1/chats/{session_id}/assistant-replay", 201,
                              "retry_assistant_replay", body=replay_body)
        checks["assistant_replay_idempotent"] = replay == repeated
        before = await call(client, "GET", "/v1/sync/bootstrap", 200,
                            "topic_before_confirmation")
        checks["suggestion_not_formal_topic"] = (
            len(before["entities"]["topics"]) == 1
            and before["entities"]["chat_suggestions"][0]["status"] == "pending"
        )
        (run_dir / "artifacts").mkdir(exist_ok=True)
        with GrowthLocalRepository(run_dir / "artifacts" / "incremental-topic.sqlite") as ledger:
            incremental_store = GrowthSyncStore(ledger, account, second_device)
            incremental_store.apply_bootstrap(before)
        decision_body = {"operation_id": str(uuid.uuid4()),
                         "decision": "confirm", "topic_id": str(new_topic)}
        decided = await call(client, "POST",
                             f"/v1/chats/{session_id}/suggestions/{suggestion_id}/decision",
                             200, "confirm_topic", body=decision_body)
        repeat_decision = await call(
            client, "POST",
            f"/v1/chats/{session_id}/suggestions/{suggestion_id}/decision",
            200, "retry_topic_confirmation", body=decision_body,
        )
        checks["confirmation_idempotent"] = decided == repeat_decision
        read = await call(client, "GET", f"/v1/chats/{session_id}", 200,
                          "read_chat_on_second_device", use_headers=second_headers)
        checks["second_device_sees_same_chat"] = (
            [message["content_text"] for message in read["messages"]
             if message["role"] == "user"] == [U1, U2]
            and read["suggestions"][0]["topic_id"] == str(new_topic)
        )
        after = await call(client, "GET", "/v1/sync/bootstrap", 200,
                           "topic_after_confirmation", use_headers=second_headers)
        checks["topic_only_after_user_action"] = (
            {row["topic_id"] for row in after["entities"]["topics"]}
            == {str(old_topic), str(new_topic)}
        )
        changes = await call(client, "GET",
                             f"/v1/sync/changes?after={before['high_watermark']}", 200,
                             "incremental_topic_after_confirmation",
                             use_headers=second_headers,
                             body=None)
        with GrowthLocalRepository(run_dir / "artifacts" / "incremental-topic.sqlite") as ledger:
            incremental_store = GrowthSyncStore(ledger, account, second_device)
            incremental_store.apply_changes(changes)
            cached_topic = ledger.connection.execute(
                "SELECT payload_json FROM sync_entities WHERE account_id=? "
                "AND entity_type='topic' AND entity_id=?",
                (str(account), str(new_topic)),
            ).fetchone()
        checks["confirmed_topic_reaches_incremental_cache"] = (
            cached_topic is not None
            and json.loads(cached_topic["payload_json"])["title"] == "Web 认证与授权"
        )
        (run_dir / "artifacts").mkdir(exist_ok=True)
        with GrowthLocalRepository(run_dir / "artifacts" / "chat-sync.sqlite") as ledger:
            local_store = GrowthSyncStore(ledger, account, second_device)
            local_store.apply_bootstrap(after)
            cached_types = {
                row[0]: row[1] for row in ledger.connection.execute(
                    "SELECT entity_type, COUNT(*) FROM sync_entities WHERE account_id=? "
                    "GROUP BY entity_type", (str(account),),
                )
            }
        checks["chat_bootstrap_reaches_local_cache"] = (
            cached_types.get("chat_session") == 1
            and cached_types.get("chat_message") == 3
            and cached_types.get("chat_suggestion") == 1
        )
        foreign_read = await call(client, "GET", f"/v1/chats/{session_id}",
                                  404, "foreign_account_cannot_read",
                                  use_headers=foreign_headers)
        checks["account_isolation"] = foreign_read["code"] == "CHAT_NOT_FOUND"
        rejected = await call(
            client, "POST",
            f"/v1/chats/{session_id}/suggestions/{suggestion_id}/decision",
            409, "reject_opposite_decision",
            body={"operation_id": str(uuid.uuid4()), "decision": "reject"},
        )
        checks["opposite_decision_rejected"] = rejected["code"] == "DECISION_CONFLICT"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=production), base_url="http://growth.test"
    ) as client:
        forbidden = await call(client, "POST",
                               f"/v1/chats/{session_id}/assistant-replay", 403,
                               "production_rejects_replay",
                               body={**replay_body,
                                     "operation_id": str(uuid.uuid4()),
                                     "message_id": str(uuid.uuid4())})
        checks["replay_acceptance_only"] = forbidden["code"] == "REPLAY_FORBIDDEN"
        synthetic_text = "合成助手响应，仅用于生产入口契约回放。"
        live_message_id, live_suggestion_id = uuid.uuid4(), uuid.uuid4()
        live_body = {
            "operation_id": str(uuid.uuid4()), "message_id": str(live_message_id),
            "content_text": synthetic_text,
            "suggestion": {"suggestion_id": str(live_suggestion_id),
                           "title": "合成主题建议", "reason": "仅验证待确认状态"},
            "provider_name": "fixture-only", "model_name": "synthetic-contract-test",
            "provider_request_id": "synthetic-request",
            "request_payload_hash": hashlib.sha256(b"synthetic prompt").hexdigest(),
            "response_payload_hash": hashlib.sha256(synthetic_text.encode()).hexdigest(),
            "input_tokens": 7, "output_tokens": 11, "elapsed_ms": 1,
        }
        created_live = await call(
            client, "POST", f"/v1/chats/{session_id}/assistant", 201,
            "production_live_receipt_contract_synthetic", body=live_body,
        )
        repeated_live = await call(
            client, "POST", f"/v1/chats/{session_id}/assistant", 201,
            "retry_synthetic_live_receipt", body=live_body,
        )
        checks["production_live_receipt_contract_idempotent"] = (
            created_live == repeated_live
            and created_live["message_id"] == str(live_message_id)
            and created_live["suggestion_id"] == str(live_suggestion_id)
        )
    with connect(dsn) as conn:
        rows = {
            "topics": conn.execute("SELECT topic_id, title FROM growth_topics "
                                   "WHERE account_id=%s ORDER BY title", (account,)).fetchall(),
            "messages": conn.execute(
                "SELECT message_id, role, origin, content_text FROM growth_chat_messages "
                "WHERE account_id=%s AND session_id=%s ORDER BY created_at, message_id",
                (account, session_id),
            ).fetchall(),
            "suggestions": conn.execute(
                "SELECT suggestion_id, status, topic_id FROM growth_chat_suggestions "
                "WHERE account_id=%s", (account,),
            ).fetchall(),
            "live_receipts": conn.execute(
                "SELECT payload FROM growth_change_feed WHERE account_id=%s "
                "AND entity_type='chat_message' AND event_type='chat_assistant_live'",
                (account,),
            ).fetchall(),
            "live_metrics": conn.execute(
                "SELECT provider_name, model_name, provider_request_id, "
                "input_tokens, output_tokens, elapsed_ms FROM growth_chat_messages "
                "WHERE account_id=%s AND message_id=%s",
                (account, live_message_id),
            ).fetchone(),
        }
    artifact = run_dir / "artifacts"
    artifact.mkdir(exist_ok=True)
    (artifact / "chat-rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    checks["assistant_not_user_note"] = (
        len(rows["messages"]) == 4
        and sum(message["origin"] == "replay" for message in rows["messages"]) == 1
        and sum(message["origin"] == "live" for message in rows["messages"]) == 1
        and len(rows["topics"]) == 2
    )
    checks["synthetic_live_provenance_and_pending_topic"] = (
        len(rows["live_receipts"]) == 1
        and rows["live_receipts"][0]["payload"]["provider_name"] == "fixture-only"
        and any(item["suggestion_id"] == live_suggestion_id
                and item["status"] == "pending" for item in rows["suggestions"])
    )
    checks["synthetic_live_metrics_persisted"] = (
        rows["live_metrics"]["provider_name"] == "fixture-only"
        and rows["live_metrics"]["model_name"] == "synthetic-contract-test"
        and rows["live_metrics"]["provider_request_id"] == "synthetic-request"
        and rows["live_metrics"]["input_tokens"] == 7
        and rows["live_metrics"]["output_tokens"] == 11
        and rows["live_metrics"]["elapsed_ms"] == 1
    )
    result = "PASS" if all(checks.values()) else "FAIL"
    (run_dir / "result.json").write_text(
        json.dumps({"status": result, "checks": checks},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result != "PASS":
        raise AssertionError(f"M6 chat checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    run_id = f"m6-chat-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    run_dir.mkdir(parents=True)
    database_name = "growth_m6_" + uuid.uuid4().hex[:12]
    try:
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        if dsn == admin_dsn:
            raise ValueError("管理员 DSN 必须包含 dbname=postgres")
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        migrations = apply_migrations(dsn)
        expected = ["001_initial", "002_learning", "003_sync", "004_memory",
                    "005_review", "006_modules", "007_module_tasks",
                    "008_chat_topics", "009_topic_maintenance",
                    "010_development_events", "011_exports", "012_task_drafts", "013_chat_model_receipts",
                    "014_review_answer_text"]
        if migrations != expected:
            raise AssertionError(f"M6 迁移不完整：{migrations}")
        asyncio.run(exercise(run_dir, dsn))
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            (run_dir / "result.json").write_text(
                json.dumps({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
        raise
    finally:
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "stage": "M6", "mode": "replay",
                        "check_status": result["status"], "stage_status": "BLOCKED",
                        "database_name": database_name,
                        "stage_blockers": ["真实模型聊天、主题维护、事件、导出及双端 UI 未验收"]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (run_dir / "report.md").write_text(
            "# M6 自由聊天与主题确认回放\n\n"
            "S04 的 U1/U2 原文由合成用户经云 API 输入。助手回复与建议明确为 replay；"
            "确认前只存在先前用户创建的主题，确认后第二设备身份看到同一正式主题。"
            "生产配置拒绝固定助手回复；另以明确标注的合成模型回执验证生产入口"
            "的持久化、来源字段与幂等契约，该回执不计作真实模型调用。\n\n"
            f"结果：**{result['status']}**；M6/S04 live 仍未验收。\n",
            encoding="utf-8",
        )
        print(run_dir)


if __name__ == "__main__":
    main()
