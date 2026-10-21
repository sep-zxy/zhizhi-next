"""Replay versioned development event de-duplication and device inbox."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
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
from ahadiff.growth.development_events import capture_development_event, event_inbox
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.sync import GrowthSyncStore

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True,
                            capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def fixture(run_dir: Path) -> tuple[str, str]:
    repo = run_dir / "fixture" / "document-demo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Growth Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    source = repo / "document.ts"
    source.write_text("export const canEdit = false;\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "document.ts")
    _git(repo, "commit", "-m", "建立文档权限模拟基线")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-c", "fix/document-owner-check")
    source.write_text("export const canEdit = true;\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "document.ts")
    _git(repo, "commit", "-m", "增加文档所有者编辑权限检查")
    head = _git(repo, "rev-parse", "HEAD")
    artifact = run_dir / "artifacts"
    artifact.mkdir()
    (artifact / "git.json").write_text(
        json.dumps({"base_sha": base, "head_sha": head,
                    "status": _git(repo, "status", "--porcelain")},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    _git(repo, "bundle", "create", str(artifact / "fixture.bundle"), "--all")
    return base, head


async def exercise(run_dir: Path, dsn: str, base: str, head: str) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER,
                                                     public_key_pem=public_pem)))
    account, foreign, windows, second, foreign_device = (
        uuid.uuid4() for _ in range(5)
    )
    event_id, pending_id = (uuid.uuid4() for _ in range(2))
    local_db = run_dir / "artifacts" / "event-sync.sqlite"
    with GrowthLocalRepository(local_db) as ledger:
        project_id = uuid.UUID(ledger.create_project("文档权限", trace_id=str(uuid.uuid4())))
        binding_id = ledger.bind_repo(str(project_id), run_dir / "fixture" / "document-demo",
                                      trace_id=str(uuid.uuid4()))
        feature_id = uuid.UUID(ledger.start_feature(
            str(project_id), binding_id, "所有者检查", "main",
            trace_id=str(uuid.uuid4()),
        ))
        ledger.grant_cloud_sync(str(project_id), str(account), "http://127.0.0.1:9001")
        ledger.connection.commit()
    headers = {"Authorization": f"Bearer {token(key, account)}",
               "X-Device-Id": str(windows)}
    second_headers = {**headers, "X-Device-Id": str(second)}
    foreign_headers = {"Authorization": f"Bearer {token(key, foreign)}",
                       "X-Device-Id": str(foreign_device)}
    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []

    async def call(client: httpx.AsyncClient, method: str, path: str, expected: int,
                   label: str, *, body: dict[str, Any] | None = None,
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
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        for owner, device in ((account, windows), (account, second),
                              (foreign, foreign_device)):
            response = await client.post(
                "/v1/devices", json={"operation_id": str(uuid.uuid4()),
                                     "device_id": str(device)},
                headers={"Authorization": f"Bearer {token(key, owner)}"},
            )
            assert response.status_code == 201, response.text
        await call(client, "POST", "/v1/projects", 201, "create_project",
                   body={"operation_id": str(uuid.uuid4()),
                         "project_id": str(project_id), "name": "文档权限",
                         "sync_policy": {"cloud_allowed": True,
                                         "model_allowed": False}})
        await call(client, "POST", "/v1/features", 201, "create_feature",
                   body={"operation_id": str(uuid.uuid4()),
                         "feature_id": str(feature_id), "project_id": str(project_id),
                         "label": "所有者检查", "base_ref": "main",
                         "start_base_sha": base})
        event = {"operation_id": str(uuid.uuid4()), "schema_version": 1,
                 "event_id": str(event_id), "project_id": str(project_id),
                 "feature_id": str(feature_id), "type": "coding_task_finished",
                 "occurred_at": datetime.now(UTC).isoformat(), "source": "codex",
                 "target_device_id": str(windows), "expected_head_sha": head}
        created = await call(client, "POST", "/v1/development-events", 202,
                             "receive_targeted_event", body=event)
        retried = await call(client, "POST", "/v1/development-events", 202,
                             "retry_same_event_new_operation",
                             body={**event, "operation_id": str(uuid.uuid4())})
        checks["event_id_deduplicated"] = (
            created["event_id"] == retried["event_id"]
            and created["change_seq"] == retried["change_seq"]
        )
        altered = await call(client, "POST", "/v1/development-events", 409,
                             "reject_event_id_changed_payload",
                             body={**event, "operation_id": str(uuid.uuid4()),
                                   "expected_head_sha": base})
        checks["changed_event_rejected"] = altered["code"] == "EVENT_ID_REUSED"
        injection = await call(client, "POST", "/v1/development-events", 422,
                               "reject_local_path_or_command",
                               body={**event, "operation_id": str(uuid.uuid4()),
                                     "event_id": str(uuid.uuid4()),
                                     "local_path": "C:\\private\\work",
                                     "command": "git reset --hard"})
        checks["event_cannot_command_local_machine"] = (
            injection["code"] == "INVALID_REQUEST"
        )
        targeted = await call(client, "GET", "/v1/development-events/inbox", 200,
                              "windows_target_inbox")
        other_inbox = await call(client, "GET", "/v1/development-events/inbox", 200,
                                 "second_device_has_no_targeted_event",
                                 use_headers=second_headers)
        checks["only_target_device_receives_event"] = (
            [item["event_id"] for item in targeted["events"]] == [str(event_id)]
            and other_inbox["events"] == []
        )
        pending_event = {**event, "operation_id": str(uuid.uuid4()),
                         "event_id": str(pending_id), "target_device_id": None}
        pending = await call(client, "POST", "/v1/development-events", 202,
                             "event_without_target_waits", body=pending_event)
        checks["untargeted_requires_confirmation"] = (
            pending["status"] == "pending_confirmation"
        )
        mismatch = await call(client, "POST",
                              f"/v1/development-events/{pending_id}/claim", 409,
                              "reject_wrong_head_claim",
                              body={"operation_id": str(uuid.uuid4()),
                                    "expected_head_sha": base},
                              use_headers=second_headers)
        checks["head_mismatch_rejected"] = mismatch["code"] == "HEAD_MISMATCH"
        claimed = await call(client, "POST",
                             f"/v1/development-events/{pending_id}/claim", 200,
                             "confirm_target_and_head",
                             body={"operation_id": str(uuid.uuid4()),
                                   "expected_head_sha": head},
                             use_headers=second_headers)
        checks["claim_targets_one_device"] = (
            claimed["target_device_id"] == str(second)
        )
        foreign_inbox = await call(client, "GET", "/v1/development-events/inbox",
                                   200, "foreign_account_empty_inbox",
                                   use_headers=foreign_headers)
        checks["account_isolation"] = foreign_inbox["events"] == []
        bootstrap = await call(client, "GET", "/v1/sync/bootstrap", 200,
                               "events_in_sync_snapshot")
        checks["sync_snapshot_contains_two_events"] = (
            len(bootstrap["entities"]["development_events"]) == 2
        )
        with GrowthLocalRepository(local_db) as ledger:
            local_store = GrowthSyncStore(ledger, account, windows)
            local_store.apply_bootstrap(bootstrap)
            cached_count = ledger.connection.execute(
                "SELECT COUNT(*) FROM sync_entities WHERE account_id=? "
                "AND entity_type='development_event'",
                (str(account),),
            ).fetchone()[0]
            inbox = event_inbox(ledger)
            captured = capture_development_event(
                ledger, str(account), str(event_id), binding_id,
                selected_untracked=set(), trace_id=str(uuid.uuid4()),
            )
            captured_again = capture_development_event(
                ledger, str(account), str(event_id), binding_id,
                selected_untracked=set(), trace_id=str(uuid.uuid4()),
            )
            receipt_count = ledger.connection.execute(
                "SELECT COUNT(*) FROM local_development_event_captures "
                "WHERE account_id=? AND event_id=?",
                (str(account), str(event_id)),
            ).fetchone()[0]
        checks["events_reach_local_cache"] = cached_count == 2
        checks["target_device_local_inbox"] = (
            [item["event_id"] for item in inbox] == [str(event_id)]
        )
        checks["target_capture_idempotent"] = (
            not captured["reused"] and captured_again["reused"]
            and captured["snapshot_id"] == captured_again["snapshot_id"]
            and receipt_count == 1
        )
    with connect(dsn) as conn:
        events = conn.execute(
            "SELECT event_id, target_device_id, expected_head_sha, status, "
            "payload_hash FROM growth_development_events WHERE account_id=%s "
            "ORDER BY event_id", (account,),
        ).fetchall()
        changes = conn.execute(
            "SELECT event_type, entity_id FROM growth_change_feed "
            "WHERE account_id=%s AND entity_type='development_event' "
            "ORDER BY change_seq", (account,),
        ).fetchall()
    artifact = run_dir / "artifacts"
    (artifact / "event-rows.json").write_text(
        json.dumps({"events": events, "changes": changes},
                   ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    checks["one_receipt_per_event_id"] = (
        len(events) == 2 and len(changes) == 3
        and events[0]["expected_head_sha"].strip() == head
        and all(row["status"] == "targeted" for row in events)
    )
    result = "PASS" if all(checks.values()) else "FAIL"
    (run_dir / "result.json").write_text(
        json.dumps({"status": result, "checks": checks},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result != "PASS":
        raise AssertionError(f"M6 event checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    run_id = f"m6-event-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    run_dir.mkdir(parents=True)
    database_name = "growth_m6_" + uuid.uuid4().hex[:12]
    try:
        base, head = fixture(run_dir)
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        if dsn == admin_dsn:
            raise ValueError("管理员 DSN 必须包含 dbname=postgres")
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        applied = apply_migrations(dsn)
        expected = ["001_initial", "002_learning", "003_sync", "004_memory",
                    "005_review", "006_modules", "007_module_tasks",
                    "008_chat_topics", "009_topic_maintenance",
                    "010_development_events", "011_exports", "012_task_drafts", "013_chat_model_receipts",
                    "014_review_answer_text"]
        if applied != expected:
            raise AssertionError(f"M6 迁移不完整：{applied}")
        asyncio.run(exercise(run_dir, dsn, base, head))
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
                        "stage_blockers": ["目标机 live 模型分析、双端 UI 尚未验收"]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (run_dir / "report.md").write_text(
            "# M6 开发事件回放\n\n"
            "真实 Git fixture 的 HEAD 作为 expected_head_sha。事件 API 接收同 ID 重发"
            "后只有一条业务记录；不同内容、任意本机路径和命令字段被拒绝。"
            "指定目标只在该设备 Inbox 可见，未指定目标须确认；版本不符不能认领。"
            "本机已核对事件目标、绑定和真实 Git HEAD，幂等捕获一份快照；"
            "live 模型分析仍需经 UI 预览和用户批准。\n\n"
            f"结果：**{result['status']}**；M6/S04 live 仍未验收。\n",
            encoding="utf-8",
        )
        print(run_dir)


if __name__ == "__main__":
    main()
