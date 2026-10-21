"""Verify that a code opportunity can use an existing confirmed cloud topic."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from m1_cloud_smoke import ISSUER, token

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import connect

ROOT = Path(__file__).resolve().parents[2]


async def exercise(seed_run: Path, run_dir: Path, dsn: str) -> None:
    chain = json.loads((seed_run / "chain.json").read_text(encoding="utf-8"))
    account = uuid.UUID(chain["account_id"])
    topic_id = chain["topic_id"]
    with connect(dsn) as conn:
        proposal = conn.execute(
            "SELECT proposal_id, opportunity_id FROM growth_topic_proposals "
            "WHERE account_id=%s AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (account,),
        ).fetchone()
        if proposal is None:
            raise AssertionError("种子数据缺少待确认的代码主题建议")
        initial_count = conn.execute(
            "SELECT count(*) AS n FROM growth_topics WHERE account_id=%s", (account,),
        ).fetchone()["n"]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER,
                                                     public_key_pem=public_pem)))
    device = uuid.uuid4()
    headers = {"Authorization": f"Bearer {token(key, account)}",
               "X-Device-Id": str(device)}
    checks: dict[str, bool] = {}
    steps: list[dict[str, object]] = []

    async def call(client: httpx.AsyncClient, method: str, path: str,
                   expected: int, label: str, body: dict[str, object] | None = None
                   ) -> dict[str, object]:
        response = await client.request(method, path, headers=headers, json=body)
        actual = response.json()
        steps.append({"label": label, "expected": expected,
                      "actual_status": response.status_code, "response": actual})
        if response.status_code != expected:
            raise AssertionError(f"{label}: {response.status_code} != {expected}: {actual}")
        return actual

    path = f"/v1/topic-proposals/{proposal['proposal_id']}/decision"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://growth.test") as client:
        await call(client, "POST", "/v1/devices", 201, "register_device",
                   {"operation_id": str(uuid.uuid4()), "device_id": str(device)})
        missing = await call(client, "POST", path, 409, "reject_missing_topic",
                             {"operation_id": str(uuid.uuid4()), "decision": "confirm",
                              "topic_id": str(uuid.uuid4()), "reuse_existing": True})
        checks["missing_topic_rejected"] = missing["code"] == "TOPIC_NOT_ACTIVE"
        body: dict[str, object] = {"operation_id": str(uuid.uuid4()),
                                   "decision": "confirm", "topic_id": topic_id,
                                   "reuse_existing": True}
        decided = await call(client, "POST", path, 200, "reuse_confirmed_topic", body)
        retry = await call(client, "POST", path, 200, "retry_same_operation", body)
        checks["same_operation_idempotent"] = decided == retry
        another = await call(client, "POST", path, 200, "retry_new_operation",
                             {**body, "operation_id": str(uuid.uuid4())})
        checks["new_operation_same_decision"] = another["topic_id"] == topic_id
        conflict = await call(client, "POST", path, 409, "reject_changed_reuse_mode",
                              {**body, "operation_id": str(uuid.uuid4()),
                               "reuse_existing": False})
        checks["changed_decision_rejected"] = conflict["code"] == "DECISION_CONFLICT"
        task_id = uuid.uuid4()
        task = await call(client, "POST", "/v1/tasks", 201, "card_uses_existing_topic",
                          {"operation_id": str(uuid.uuid4()), "task_id": str(task_id),
                           "opportunity_id": str(proposal["opportunity_id"]),
                           "topic_id": topic_id,
                           "question": "认证和授权分别解决什么问题？",
                           "followups": []})
        checks["task_same_topic"] = task["task_id"] == str(task_id)
    with connect(dsn) as conn:
        topic_count = conn.execute(
            "SELECT count(*) AS n FROM growth_topics WHERE account_id=%s", (account,),
        ).fetchone()["n"]
        proposal_row = conn.execute(
            "SELECT topic_id, status FROM growth_topic_proposals "
            "WHERE account_id=%s AND proposal_id=%s",
            (account, proposal["proposal_id"]),
        ).fetchone()
        task_row = conn.execute(
            "SELECT topic_id FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, task_id),
        ).fetchone()
        topic_events = conn.execute(
            "SELECT count(*) AS n FROM growth_change_feed WHERE account_id=%s "
            "AND entity_type='topic' AND entity_id=%s",
            (account, uuid.UUID(topic_id)),
        ).fetchone()["n"]
    checks["one_topic_with_shared_id"] = (
        initial_count == topic_count == 1
        and str(proposal_row["topic_id"]) == topic_id
        and proposal_row["status"] == "confirmed"
        and str(task_row["topic_id"]) == topic_id
        and topic_events == 1
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    (run_dir / "steps.json").write_text(
        json.dumps(steps, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(
        json.dumps({"status": status, "checks": checks}, ensure_ascii=False, indent=2)
        + "\n", encoding="utf-8")
    if status != "PASS":
        raise AssertionError(f"主题复用检查失败：{checks}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("seed_run", type=Path)
    args = parser.parse_args()
    seed_run = args.seed_run.resolve()
    manifest = json.loads((seed_run / "manifest.json").read_text(encoding="utf-8"))
    if manifest["check_status"] != "PASS":
        raise ValueError("仅能使用通过验证的 M2 种子运行")
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    dsn = admin_dsn.replace("dbname=postgres", f"dbname={manifest['database_name']}")
    if dsn == admin_dsn:
        raise ValueError("管理员 DSN 必须包含 dbname=postgres")
    run_id = f"m7-topic-reuse-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    run_dir.mkdir(parents=True)
    try:
        asyncio.run(exercise(seed_run, run_dir, dsn))
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            (run_dir / "result.json").write_text(
                json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False)
                + "\n", encoding="utf-8")
        raise
    finally:
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "stage": "M7", "mode": "replay",
                        "seed_run": seed_run.name, "database_name": manifest["database_name"],
                        "check_status": result["status"], "stage_status": "BLOCKED"},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (run_dir / "report.md").write_text(
            "# S04 已有主题复用回放\n\n"
            "在隔离 PostgreSQL 的 M2 种子链上确认第二次代码机会，关联原主题 ID，"
            "再创建短卡片；验证没有新增主题、两种重试幂等，缺失主题和改变决定被拒绝。"
            "该结果为 API 回放，不计入安装版 live 验收。\n\n"
            f"结果：**{result['status']}**。\n", encoding="utf-8")
        print(run_dir)


if __name__ == "__main__":
    main()
