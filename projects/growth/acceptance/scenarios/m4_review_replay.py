"""Replay delayed FSRS review without changing authoritative sync clocks."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from m1_cloud_smoke import ISSUER, now, save_json, token
from m2_cloud_replay import LOCAL_RUN
from m2_cloud_replay import exercise as seed_m2
from psycopg import sql

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect

ROOT = Path(__file__).resolve().parents[2]


async def exercise(run_dir: Path, dsn: str, local: dict[str, Any]) -> None:
    seed_dir = run_dir / "seed-m2"
    (seed_dir / "artifacts").mkdir(parents=True)
    await seed_m2(seed_dir, dsn, local)
    chain = json.loads((seed_dir / "chain.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(chain["account_id"])
    task_id = uuid.UUID(chain["task_id"])
    with connect(dsn) as conn:
        device = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s ORDER BY last_seen LIMIT 1",
            (account_id,),
        ).fetchone()
        schedule = conn.execute(
            "SELECT revision, fsrs_state, due_at, scheduler_version, updated_at "
            "FROM growth_review_schedules WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchone()
    assert device is not None and schedule is not None
    device_id = device["device_id"]
    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []
    initial_due = schedule["due_at"]
    learning_time = initial_due + timedelta(seconds=1)
    checks["completion_schedules_delayed_review"] = (
        schedule["revision"] == 1
        and initial_due > datetime.now(UTC) + timedelta(hours=47)
        and schedule["scheduler_version"].startswith("fsrs-")
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    verifier = AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)
    production = create_app(CloudSettings(dsn, verifier))
    acceptance = create_app(CloudSettings(dsn, verifier, acceptance_mode=True))
    headers = {"Authorization": f"Bearer {token(private_key, account_id)}",
               "X-Device-Id": str(device_id)}
    clock_headers = {**headers, "X-Learning-Clock": learning_time.isoformat()}

    async def call(
        client: httpx.AsyncClient, method: str, path: str, expected: int, label: str,
        *, body: dict[str, Any] | None = None, injected_clock: bool = False,
    ) -> dict[str, Any]:
        started = now()
        response = await client.request(
            method, path, headers=clock_headers if injected_clock else headers,
            json=body,
        )
        actual = response.json()
        step = {"step": label, "started_at": started, "finished_at": now(),
                "device_id": str(device_id), "expected_status": expected,
                "actual_status": response.status_code, "actual": actual,
                "operation_id": body.get("operation_id") if body else None,
                "verdict": "PASS" if response.status_code == expected else "FAIL"}
        steps.append(step)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(step, ensure_ascii=False) + "\n")
        if response.status_code != expected:
            raise AssertionError(f"{label}: {response.status_code} != {expected}: {actual}")
        return actual

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=production), base_url="http://growth.test"
    ) as prod_client:
        not_due = await call(
            prod_client, "GET", "/v1/reviews/due", 200, "before_due"
        )
        checks["not_due_on_real_clock"] = not not_due["due"]
        forbidden = await call(
            prod_client, "GET", "/v1/reviews/due", 403,
            "production_rejects_clock_override", injected_clock=True,
        )
        checks["clock_override_acceptance_only"] = (
            forbidden["code"] == "CLOCK_OVERRIDE_FORBIDDEN"
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=acceptance), base_url="http://growth.test"
    ) as client:
        due = await call(
            client, "GET", "/v1/reviews/due", 200, "due_after_time_injection",
            injected_clock=True,
        )
        checks["due_at_injected_time"] = (
            len(due["due"]) == 1 and due["due"][0]["task_id"] == str(task_id)
        )
        invalid = await call(
            client, "POST", f"/v1/tasks/{task_id}/reviews", 422,
            "hint_cannot_rate_good", injected_clock=True,
            body={"operation_id": str(uuid.uuid4()), "review_id": str(uuid.uuid4()),
                  "base_revision": 1, "answer_text": "否，join 可能等待组合结果。",
                  "answer": "good", "hint_level": 2,
                  "actor_origin": "simulated_user"},
        )
        checks["hint_does_not_fake_mastery"] = (
            invalid["code"] == "INVALID_REVIEW_RATING"
        )
        review_id = uuid.uuid4()
        body = {"operation_id": str(uuid.uuid4()), "review_id": str(review_id),
                "base_revision": 1, "answer_text": "否，join 可能等待组合结果。",
                "answer": "hard", "hint_level": 2,
                "actor_origin": "simulated_user"}
        reviewed = await call(
            client, "POST", f"/v1/tasks/{task_id}/reviews", 200,
            "record_prompted_review", body=body, injected_clock=True,
        )
        repeated = await call(
            client, "POST", f"/v1/tasks/{task_id}/reviews", 200,
            "retry_review_same_operation", body=body, injected_clock=True,
        )
        checks["review_fixed_idempotent"] = (
            repeated == reviewed and reviewed["revision"] == 2
            and reviewed["hint_level"] == 2
        )
        stale = await call(
            client, "POST", f"/v1/tasks/{task_id}/reviews", 409,
            "reject_stale_review", injected_clock=True,
            body={**body, "operation_id": str(uuid.uuid4()),
                  "review_id": str(uuid.uuid4())},
        )
        checks["stale_review_fixed_conflict"] = (
            stale["code"] == "REVISION_CONFLICT" and stale["server_revision"] == 2
        )
        bootstrap = await call(
            client, "GET", "/v1/sync/bootstrap", 200, "read_review_across_sync"
        )
        checks["review_in_bootstrap"] = (
            len(bootstrap["entities"]["review_schedules"]) == 1
            and bootstrap["entities"]["review_schedules"][0]["revision"] == 2
            and len(bootstrap["entities"]["review_events"]) == 1
        )

    with connect(dsn) as conn:
        current = conn.execute(
            "SELECT revision, fsrs_state, due_at, last_hint_level, last_review_id, "
            "updated_at FROM growth_review_schedules WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchone()
        events = conn.execute(
            "SELECT review_id, answer_text, answer, hint_level, actor_origin, rating, due_before, "
            "due_after, reviewed_at FROM growth_review_events "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id),
        ).fetchall()
        timeline = conn.execute(
            "SELECT evidence_label FROM growth_timeline "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id),
        ).fetchall()
    checks["fsrs_state_and_prompt_persist"] = (
        current is not None and current["revision"] == 2
        and current["last_hint_level"] == 2
        and current["last_review_id"] == review_id
        and len(events) == 1 and events[0]["answer"] == "hard"
        and events[0]["answer_text"] == "否，join 可能等待组合结果。"
        and events[0]["hint_level"] == 2
        and events[0]["actor_origin"] == "simulated_user"
        and events[0]["due_before"] == initial_due
        and events[0]["reviewed_at"] == learning_time
    )
    checks["sync_timestamp_uses_server_clock"] = (
        current is not None
        and abs((datetime.now(UTC) - current["updated_at"]).total_seconds()) < 300
        and current["updated_at"] < learning_time
    )
    checks["no_false_mastery_evidence"] = (
        {item["evidence_label"] for item in timeline} <= {"encountered", "practicing"}
    )
    save_json(run_dir / "artifacts" / "review.json", {
        "initial_schedule": schedule, "current_schedule": current,
        "review_events": events, "timeline": timeline,
    })
    save_json(run_dir / "chain.json", {
        "seed": "seed-m2", "account_id": str(account_id),
        "task_id": str(task_id), "review_id": str(review_id),
        "initial_due_at": initial_due.isoformat(),
        "injected_learning_time": learning_time.isoformat(),
    })
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in events),
        encoding="utf-8",
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    save_json(run_dir / "result.json", {"status": status, "checks": checks})
    save_json(run_dir / "manifest.json", {
        "run_id": run_dir.name, "stage": "M4", "case_id": "S01-review-replay",
        "mode": "replay", "check_status": status, "stage_status": "BLOCKED",
        "stage_blockers": ["live 模型未接入", "真实 ReMe 未运行", "桌面 UI 未驱动"],
        "started_at": steps[0]["started_at"], "finished_at": now(),
        "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
    })
    (run_dir / "report.md").write_text(
        "# M4 延迟复习回放\n\n"
        "完成的卡片在服务端安排两日后复习，使用现有 FSRS 状态。真实时钟下尚未到期；"
        "仅验收应用接受学习时钟注入。提示程度 2 时，`good` 评价被拒，`hard` 记录"
        "为独立复习事件并产生下一次 due_at。重复操作返回固定结果；云端 bootstrap 可重读。"
        "同步 updated_at 保持真实服务器时间，没有产生虚假的掌握证据。\n\n"
        f"检查：**{status}**。这是合成用户输入和时钟回放，不计入 live 验收。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(f"M4 review checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m4-review-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    database_name = "growth_m4_review_" + uuid.uuid4().hex[:10]
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        if apply_migrations(dsn) != [
            "001_initial", "002_learning", "003_sync", "004_memory", "005_review",
            "006_modules", "007_module_tasks", "008_chat_topics",
            "009_topic_maintenance", "010_development_events", "011_exports", "012_task_drafts",
            "013_chat_model_receipts", "014_review_answer_text",
        ]:
            raise AssertionError("M4 migrations did not apply")
        asyncio.run(exercise(run_dir, dsn, local))
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
            save_json(run_dir / "manifest.json", {
                "run_id": run_id, "stage": "M4", "mode": "replay",
                "check_status": "FAIL", "stage_status": "BLOCKED",
                "database_name": database_name, "error": str(exc),
            })
            (run_dir / "report.md").write_text(
                f"# M4 复习回放失败\n\n`{type(exc).__name__}: {exc}`。此运行保持 FAIL。\n",
                encoding="utf-8",
            )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
