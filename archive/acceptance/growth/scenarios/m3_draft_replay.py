"""Replay offline task drafts, answer submission, and progress across two devices."""

from __future__ import annotations

import asyncio
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
from m2_cloud_replay import LOCAL_RUN
from m2_cloud_replay import exercise as seed_m2
from psycopg import sql
from starlette.testclient import TestClient

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.sync import GrowthSyncClient, GrowthSyncStore
from ahadiff.serve.app import create_app as create_desktop_app
from ahadiff.serve.state import ServeState

ROOT = Path(__file__).resolve().parents[2]


async def exercise(run_dir: Path, dsn: str, local: dict[str, Any]) -> dict[str, bool]:
    seed_dir = run_dir / "seed-m2"
    (seed_dir / "artifacts").mkdir(parents=True)
    await seed_m2(seed_dir, dsn, local)
    chain = json.loads((seed_dir / "chain.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(chain["account_id"])
    task_id = uuid.UUID(chain["task_id"])
    previous_attempt_id = uuid.UUID(chain["attempt_ids"][-1])
    with connect(dsn) as conn:
        device_row = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s "
            "ORDER BY last_seen LIMIT 1", (account_id,),
        ).fetchone()
        task_row = conn.execute(
            "SELECT revision FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account_id, task_id),
        ).fetchone()
        assert device_row is not None and task_row is not None
        device_id = uuid.UUID(str(device_row["device_id"]))
        base_task_revision = int(task_row["revision"])
    second_device = uuid.uuid4()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(
        dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)
    ))
    jwt = token(private_key, account_id)
    headers_a = {"Authorization": f"Bearer {jwt}", "X-Device-Id": str(device_id)}
    headers_b = {"Authorization": f"Bearer {jwt}", "X-Device-Id": str(second_device)}
    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []

    def record(label: str, passed: bool, detail: dict[str, Any]) -> None:
        step = {"step": label, "time": datetime.now(UTC).isoformat(),
                "verdict": "PASS" if passed else "FAIL", "detail": detail}
        steps.append(step)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(step, ensure_ascii=False, default=str) + "\n")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        register = await client.post(
            "/v1/devices", headers={"Authorization": f"Bearer {jwt}"},
            json={"operation_id": str(uuid.uuid4()), "device_id": str(second_device)},
        )
        checks["second_device_registered"] = register.status_code == 201
        record("second_device_registered", checks["second_device_registered"],
               {"status": register.status_code})
        reopened = await client.patch(
            f"/v1/tasks/{task_id}/progress", headers=headers_a,
            json={"operation_id": str(uuid.uuid4()),
                  "base_revision": base_task_revision, "progress": "in_progress"},
        )
        checks["task_reopened"] = reopened.status_code == 200
        record("task_reopened", checks["task_reopened"],
               {"status": reopened.status_code, "response": reopened.json()})
        if not all(checks.values()):
            raise AssertionError("设备注册或卡片重新打开失败")
        reopened_revision = int(reopened.json()["revision"])

        state_a = run_dir / "artifacts" / "device-a"
        path_a = state_a / "growth.sqlite"
        with GrowthLocalRepository(path_a) as ledger_a:
            store_a = GrowthSyncStore(ledger_a, account_id, device_id)
            await GrowthSyncClient(store_a, client, jwt).bootstrap()
            desktop = TestClient(
                create_desktop_app(ServeState(state_dir=state_a, token="test-token")),
                base_url="http://localhost:8765",
            )
            desktop_headers = {
                "origin": "http://localhost:8765", "X-AhaDiff-Token": "test-token",
            }
            workspace_url = "/api/growth/local/sync/workspace"
            workspace = desktop.get(workspace_url, headers=desktop_headers)
            cached_task = next(
                item for item in workspace.json()["tasks"]
                if item["task_id"] == str(task_id)
            )
            checks["desktop_cached_card_and_draft"] = (
                desktop.get(workspace_url).status_code == 401
                and cached_task["task"]["progress"] == "in_progress"
                and cached_task["remote_draft"]["submitted_attempt_id"]
                == str(previous_attempt_id)
            )
            record("desktop_cached_card_and_draft",
                   checks["desktop_cached_card_and_draft"],
                   {"task_id": str(task_id)})
            draft_url = f"/api/growth/local/sync/tasks/{task_id}/draft"
            first_draft = desktop.post(
                draft_url, headers=desktop_headers,
                json={"account_id": str(account_id),
                      "content_text": "离线草稿一：只检查 synchronized。",
                      "after_attempt_id": str(previous_attempt_id)},
            )
            assert first_draft.status_code == 201, first_draft.text
            draft_one = first_draft.json()["operation_id"]
            second_draft = desktop.post(
                draft_url, headers=desktop_headers,
                json={"account_id": str(account_id),
                      "content_text": "离线草稿二：还要检查执行线程和顺序。"},
            )
            assert second_draft.status_code == 201, second_draft.text
            draft_two = second_draft.json()["operation_id"]
            answer = desktop.post(
                f"/api/growth/local/sync/tasks/{task_id}/attempts",
                headers=desktop_headers,
                json={"account_id": str(account_id),
                      "answer_text": "修正回答：检查执行线程、锁与结果顺序。",
                      "parent_attempt_id": str(previous_attempt_id), "hint_level": 1},
            )
            assert answer.status_code == 201, answer.text
            attempt_id = answer.json()["attempt_id"]
            answer_operation = answer.json()["operation_id"]
            offline_workspace = desktop.get(
                workspace_url, headers=desktop_headers,
            ).json()
            offline_task = next(
                item for item in offline_workspace["tasks"]
                if item["task_id"] == str(task_id)
            )
            checks["desktop_offline_answer_retained"] = (
                offline_task["local_draft"]["state"] == "pending"
                and offline_task["pending_attempts"][0]["answer_text"]
                == "修正回答：检查执行线程、锁与结果顺序。"
                and len(offline_task["pending_operations"]) == 3
            )
            record("desktop_offline_answer_retained",
                   checks["desktop_offline_answer_retained"],
                   {"attempt_id": attempt_id})
            pause_operation = store_a.queue_task_progress(
                task_id, reopened_revision + 1, "paused"
            )
            resume_operation = store_a.queue_task_progress(
                task_id, reopened_revision + 2, "in_progress"
            )
            pending_before = [row["operation_id"] for row in store_a.pending()]
        expected_order = [draft_one, draft_two, answer_operation,
                          pause_operation, resume_operation]
        checks["offline_actions_are_ordered"] = pending_before == expected_order
        record("offline_actions_are_ordered", checks["offline_actions_are_ordered"],
               {"operation_ids": pending_before})

        state_b = run_dir / "artifacts" / "device-b"
        path_b = state_b / "growth.sqlite"
        with GrowthLocalRepository(path_b) as ledger_b:
            store_b = GrowthSyncStore(ledger_b, account_id, second_device)
            sync_b = GrowthSyncClient(store_b, client, jwt)
            await sync_b.bootstrap()
            stale_operation = store_b.queue_task_draft(
                task_id, "设备 B 尚未看到正式回答时保存的原文。",
                after_attempt_id=previous_attempt_id,
            )
            stale_attempt_id, stale_answer_operation = store_b.queue_answer(
                task_id, "设备 B 离线继续写的正式回答。",
                parent_attempt_id=previous_attempt_id,
            )

            with GrowthLocalRepository(path_a) as ledger_a:
                store_a = GrowthSyncStore(ledger_a, account_id, device_id)
                sync_a = GrowthSyncClient(store_a, client, jwt)
                checks["restart_keeps_same_ids"] = (
                    [row["operation_id"] for row in store_a.pending()] == expected_order
                )
                record("restart_keeps_same_ids", checks["restart_keeps_same_ids"],
                       {"operation_ids": expected_order})
                pushed = await sync_a.push_batch()
                checks["five_offline_actions_synced"] = pushed == 5 and not store_a.pending()
                record("five_offline_actions_synced", checks["five_offline_actions_synced"],
                       {"pushed": pushed})
                await sync_a.pull(limit=4)
                answer_in_cache = ledger_a.connection.execute(
                    "SELECT payload_json FROM sync_entities WHERE account_id=? "
                    "AND entity_type='attempt' AND entity_id=?",
                    (str(account_id), attempt_id),
                ).fetchone()
                checks["answer_text_survives_incremental_sync"] = (
                    answer_in_cache is not None
                    and json.loads(answer_in_cache["payload_json"])["answer_text"]
                    == "修正回答：检查执行线程、锁与结果顺序。"
                )
                record("answer_text_survives_incremental_sync",
                       checks["answer_text_survives_incremental_sync"],
                       {"attempt_id": attempt_id})

            stale_pushed = await sync_b.push_batch()
            stale_row = ledger_b.connection.execute(
                "SELECT state, content_text FROM sync_task_drafts "
                "WHERE account_id=? AND task_id=?",
                (str(account_id), str(task_id)),
            ).fetchone()
            stale_payload = json.loads(ledger_b.connection.execute(
                "SELECT payload_json FROM sync_outbox WHERE account_id=? AND operation_id=?",
                (str(account_id), stale_operation),
            ).fetchone()["payload_json"])
            blocked_answer = ledger_b.connection.execute(
                "SELECT state, answer_text FROM sync_pending_attempts "
                "WHERE account_id=? AND operation_id=?",
                (str(account_id), stale_answer_operation),
            ).fetchone()
            checks["late_draft_retained_as_conflict"] = (
                stale_pushed == 1 and stale_row["state"] == "conflict"
                and stale_payload["content_text"] == "设备 B 尚未看到正式回答时保存的原文。"
                and blocked_answer["state"] == "conflict"
                and blocked_answer["answer_text"] == "设备 B 离线继续写的正式回答。"
                and not store_b.pending()
            )
            record("late_draft_retained_as_conflict",
                   checks["late_draft_retained_as_conflict"],
                   {"operation_id": stale_operation, "state": stale_row["state"]})
            await sync_b.pull(limit=3)
            cached_draft = ledger_b.connection.execute(
                "SELECT revision, payload_json FROM sync_entities WHERE account_id=? "
                "AND entity_type='task_draft' AND entity_id=?",
                (str(account_id), str(task_id)),
            ).fetchone()
            checks["second_device_sees_submission_marker"] = (
                cached_draft is not None
                and json.loads(cached_draft["payload_json"])["submitted_attempt_id"]
                == attempt_id
            )
            record("second_device_sees_submission_marker",
                   checks["second_device_sees_submission_marker"],
                   {"attempt_id": attempt_id})
            retry_stale = await client.put(
                f"/v1/tasks/{task_id}/draft", headers=headers_b, json=stale_payload,
            )
            checks["late_conflict_result_fixed"] = (
                retry_stale.status_code == 409
                and retry_stale.json()["code"] == "REVISION_CONFLICT"
            )
            record("late_conflict_result_fixed", checks["late_conflict_result_fixed"],
                   {"status": retry_stale.status_code})
        remote_draft = await client.get(f"/v1/tasks/{task_id}/draft", headers=headers_b)
        checks["remote_draft_unchanged_by_late_write"] = (
            remote_draft.status_code == 200
            and remote_draft.json()["draft"]["submitted_attempt_id"] == attempt_id
            and remote_draft.json()["draft"]["content_text"]
            == "修正回答：检查执行线程、锁与结果顺序。"
        )
        record("remote_draft_unchanged_by_late_write",
               checks["remote_draft_unchanged_by_late_write"],
               {"status": remote_draft.status_code,
                "revision": remote_draft.json().get("draft", {}).get("revision")})
        retry_answer = await client.post(
            f"/v1/tasks/{task_id}/attempts", headers=headers_a,
            json={"operation_id": answer_operation, "attempt_id": attempt_id,
                  "parent_attempt_id": str(previous_attempt_id),
                  "answer_text": "修正回答：检查执行线程、锁与结果顺序。",
                  "hint_level": 1, "actor_origin": "user"},
        )
        checks["answer_retry_returns_original"] = (
            retry_answer.status_code == 202
            and retry_answer.json()["attempt_id"] == attempt_id
        )
        record("answer_retry_returns_original", checks["answer_retry_returns_original"],
               {"status": retry_answer.status_code})

        newer_draft = await client.put(
            f"/v1/tasks/{task_id}/draft", headers=headers_b,
            json={"operation_id": str(uuid.uuid4()), "base_revision": 5,
                  "content_text": "设备 B 在看到回答后写的新草稿。",
                  "after_attempt_id": attempt_id},
        )
        checks["second_device_starts_new_draft"] = (
            newer_draft.status_code == 200
            and newer_draft.json()["revision"] == 6
        )
        record("second_device_starts_new_draft", checks["second_device_starts_new_draft"],
               {"status": newer_draft.status_code})
        with GrowthLocalRepository(path_a) as ledger_a:
            store_a = GrowthSyncStore(ledger_a, account_id, device_id)
            sync_a = GrowthSyncClient(store_a, client, jwt)
            await sync_a.pull(limit=3)
            next_draft_operation = store_a.queue_task_draft(
                task_id, "设备 A 看到新版本后继续编辑。"
            )
            continued = await sync_a.push_batch()
            checks["pulled_draft_revision_used_for_next_write"] = (
                continued == 1 and not store_a.pending()
                and ledger_a.connection.execute(
                    "SELECT state, local_revision FROM sync_task_drafts "
                    "WHERE account_id=? AND task_id=?",
                    (str(account_id), str(task_id)),
                ).fetchone()["local_revision"] == 7
            )
            record("pulled_draft_revision_used_for_next_write",
                   checks["pulled_draft_revision_used_for_next_write"],
                   {"operation_id": next_draft_operation, "pushed": continued})

        batch_operations = [
            {"method": "PUT", "path": f"/v1/tasks/{task_id}/draft",
             "payload": {"operation_id": str(uuid.uuid4()), "base_revision": 7,
                         "content_text": "批量上传中先成功的草稿。"}},
            {"method": "PUT", "path": f"/v1/tasks/{task_id}/draft",
             "payload": {"operation_id": str(uuid.uuid4()), "base_revision": 7,
                         "content_text": "批量上传中过期的草稿。"}},
            {"method": "PUT", "path": f"/v1/tasks/{task_id}/draft",
             "payload": {"operation_id": str(uuid.uuid4()), "base_revision": 8,
                         "content_text": "冲突之后不得执行的草稿。"}},
        ]
        first_batch = await client.post(
            "/v1/sync/operations", headers=headers_a,
            json={"operations": batch_operations},
        )
        second_batch = await client.post(
            "/v1/sync/operations", headers=headers_a,
            json={"operations": batch_operations},
        )
        batch_result = first_batch.json()
        checks["batch_stops_after_fixed_conflict"] = (
            first_batch.status_code == second_batch.status_code == 200
            and batch_result == second_batch.json()
            and batch_result["stopped"] is True
            and len(batch_result["results"]) == 2
            and [item["status"] for item in batch_result["results"]] == [200, 409]
            and batch_result["results"][1]["response"]["code"]
            == "REVISION_CONFLICT"
        )
        record("batch_stops_after_fixed_conflict",
               checks["batch_stops_after_fixed_conflict"],
               {"result": batch_result})
        forbidden_batch = await client.post(
            "/v1/sync/operations", headers=headers_a,
            json={"operations": [{"method": "POST", "path": "/v1/projects",
                                  "payload": {"operation_id": str(uuid.uuid4())}}]},
        )
        checks["batch_rejects_unlisted_path"] = (
            forbidden_batch.status_code == 200
            and forbidden_batch.json()["results"][0]["status"] == 422
            and forbidden_batch.json()["results"][0]["response"]["code"]
            == "BATCH_OPERATION_FORBIDDEN"
        )
        record("batch_rejects_unlisted_path", checks["batch_rejects_unlisted_path"],
               {"status": forbidden_batch.status_code})

    with connect(dsn) as conn:
        task = conn.execute(
            "SELECT progress, revision FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id),
        ).fetchone()
        answer = conn.execute(
            "SELECT answer_text, parent_attempt_id, hint_level FROM growth_learning_attempts "
            "WHERE account_id=%s AND attempt_id=%s", (account_id, uuid.UUID(attempt_id)),
        ).fetchone()
        draft = conn.execute(
            "SELECT revision, content_text, submitted_attempt_id FROM growth_task_drafts "
            "WHERE account_id=%s AND task_id=%s", (account_id, task_id),
        ).fetchone()
    assert task is not None and answer is not None and draft is not None
    checks["database_has_one_formal_answer_and_progress"] = (
        str(task["progress"]) == "in_progress"
        and int(task["revision"]) == reopened_revision + 3
        and uuid.UUID(str(answer["parent_attempt_id"])) == previous_attempt_id
        and int(answer["hint_level"]) == 1
        and draft["submitted_attempt_id"] is None
        and draft["content_text"] == "批量上传中先成功的草稿。"
        and int(draft["revision"]) == 8
    )
    record("database_has_one_formal_answer_and_progress",
           checks["database_has_one_formal_answer_and_progress"],
           {"task": task, "answer": answer, "draft": draft})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        with GrowthLocalRepository(path_b) as ledger_b:
            store_b = GrowthSyncStore(ledger_b, account_id, second_device)
            sync_b = GrowthSyncClient(store_b, client, jwt)
            await sync_b.pull()
            desktop_b = TestClient(
                create_desktop_app(ServeState(state_dir=state_b, token="test-token")),
                base_url="http://localhost:8765",
            )
            desktop_headers = {
                "origin": "http://localhost:8765", "X-AhaDiff-Token": "test-token",
            }
            workspace_url = "/api/growth/local/sync/workspace"
            before_rebase = next(
                item for item in desktop_b.get(
                    workspace_url, headers=desktop_headers,
                ).json()["tasks"] if item["task_id"] == str(task_id)
            )
            assert before_rebase["local_draft"]["state"] == "conflict"
            assert before_rebase["remote_draft"]["revision"] == 8
            rebase = desktop_b.post(
                f"/api/growth/local/sync/tasks/{task_id}/rebase",
                headers=desktop_headers,
                json={"account_id": str(account_id),
                      "content_text": "设备 B 核对两版后写的新草稿。"},
            )
            assert rebase.status_code == 201, rebase.text
            assert await sync_b.push_batch() == 1
            assert await sync_b.pull() >= 1
            after_rebase = next(
                item for item in desktop_b.get(
                    workspace_url, headers=desktop_headers,
                ).json()["tasks"] if item["task_id"] == str(task_id)
            )
            checks["desktop_conflict_rebase_preserves_originals"] = (
                after_rebase["local_draft"]["state"] == "synced"
                and after_rebase["remote_draft"]["revision"] == 9
                and after_rebase["remote_draft"]["content_text"]
                == "设备 B 核对两版后写的新草稿。"
                and any(item["payload"].get("content_text")
                        == "设备 B 尚未看到正式回答时保存的原文。"
                        for item in after_rebase["blocked_operations"])
                and any(item["payload"].get("answer_text")
                        == "设备 B 离线继续写的正式回答。"
                        for item in after_rebase["blocked_operations"])
            )
            record("desktop_conflict_rebase_preserves_originals",
                   checks["desktop_conflict_rebase_preserves_originals"],
                   {"old_attempt_id": stale_attempt_id,
                    "new_operation_id": rebase.json()["operation_id"]})
            progress_url = f"/api/growth/local/sync/tasks/{task_id}/progress"
            pause = desktop_b.post(
                progress_url, headers=desktop_headers,
                json={"account_id": str(account_id),
                      "base_revision": after_rebase["revision"],
                      "progress": "paused"},
            )
            assert pause.status_code == 201, pause.text
            assert await sync_b.push_batch() == 1
            assert await sync_b.pull() >= 1
            paused_task = next(
                item for item in desktop_b.get(
                    workspace_url, headers=desktop_headers,
                ).json()["tasks"] if item["task_id"] == str(task_id)
            )
            resume = desktop_b.post(
                progress_url, headers=desktop_headers,
                json={"account_id": str(account_id),
                      "base_revision": paused_task["revision"],
                      "progress": "in_progress"},
            )
            assert resume.status_code == 201, resume.text
            assert await sync_b.push_batch() == 1
            assert await sync_b.pull() >= 1
            resumed_task = next(
                item for item in desktop_b.get(
                    workspace_url, headers=desktop_headers,
                ).json()["tasks"] if item["task_id"] == str(task_id)
            )
            checks["desktop_pause_and_resume"] = (
                paused_task["task"]["progress"] == "paused"
                and resumed_task["task"]["progress"] == "in_progress"
                and resumed_task["revision"] == paused_task["revision"] + 1
            )
            record("desktop_pause_and_resume", checks["desktop_pause_and_resume"],
                   {"paused_revision": paused_task["revision"],
                    "resumed_revision": resumed_task["revision"]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        with GrowthLocalRepository(path_a) as ledger_a:
            sync_a = GrowthSyncClient(
                GrowthSyncStore(ledger_a, account_id, device_id), client, jwt
            )
            await sync_a.pull(limit=5)
            acknowledged = await sync_a.acknowledge()
            repeated_ack = await sync_a.acknowledge()
            checks["device_ack_is_durable_and_retryable"] = (
                acknowledged == repeated_ack == sync_a.store.cursor()
            )
            record("device_ack_is_durable_and_retryable",
                   checks["device_ack_is_durable_and_retryable"],
                   {"cursor": acknowledged})
            future_ack = await client.post(
                "/v1/sync/ack", headers=headers_a,
                json={"operation_id": str(uuid.uuid4()),
                      "last_change_seq": acknowledged + 100000},
            )
            self_revoke = await client.post(
                f"/v1/devices/{device_id}/revoke", headers=headers_a,
                json={"operation_id": str(uuid.uuid4())},
            )
            checks["future_ack_and_self_revoke_rejected"] = (
                future_ack.status_code == 422
                and future_ack.json()["code"] == "INVALID_CURSOR"
                and self_revoke.status_code == 409
                and self_revoke.json()["code"] == "SELF_REVOKE_FORBIDDEN"
            )
            record("future_ack_and_self_revoke_rejected",
                   checks["future_ack_and_self_revoke_rejected"],
                   {"future_ack": future_ack.status_code,
                    "self_revoke": self_revoke.status_code})
        with GrowthLocalRepository(path_b) as ledger_b:
            sync_b = GrowthSyncClient(
                GrowthSyncStore(ledger_b, account_id, second_device), client, jwt
            )
            await sync_b.pull(limit=5)
            b_ack = await sync_b.acknowledge()
            checks["second_device_acknowledged"] = b_ack == sync_b.store.cursor()
            record("second_device_acknowledged", checks["second_device_acknowledged"],
                   {"cursor": b_ack})
        revoked = await client.post(
            f"/v1/devices/{second_device}/revoke", headers=headers_a,
            json={"operation_id": str(uuid.uuid4())},
        )
        denied = await client.get("/v1/sync/changes", headers=headers_b,
                                  params={"after": b_ack})
        reregistered = await client.post(
            "/v1/devices", headers={"Authorization": f"Bearer {jwt}"},
            json={"operation_id": str(uuid.uuid4()),
                  "device_id": str(second_device)},
        )
        checks["revoked_device_cannot_sync_or_reregister"] = (
            revoked.status_code == 200
            and denied.status_code == reregistered.status_code == 403
            and denied.json()["code"] == reregistered.json()["code"]
            == "DEVICE_REVOKED"
        )
        record("revoked_device_cannot_sync_or_reregister",
               checks["revoked_device_cannot_sync_or_reregister"],
               {"revoke": revoked.status_code, "sync": denied.status_code})
        replacement_id = uuid.uuid4()
        replacement = await client.post(
            "/v1/devices", headers={"Authorization": f"Bearer {jwt}"},
            json={"operation_id": str(uuid.uuid4()),
                  "device_id": str(replacement_id)},
        )
        replacement_bootstrap = await client.get(
            "/v1/sync/bootstrap",
            headers={"Authorization": f"Bearer {jwt}",
                     "X-Device-Id": str(replacement_id)},
        )
        checks["replacement_device_requires_fresh_bootstrap"] = (
            replacement.status_code == 201
            and replacement_bootstrap.status_code == 200
            and any(row["task_id"] == str(task_id) for row in
                    replacement_bootstrap.json()["entities"]["tasks"])
        )
        record("replacement_device_requires_fresh_bootstrap",
               checks["replacement_device_requires_fresh_bootstrap"],
               {"register": replacement.status_code,
                "bootstrap": replacement_bootstrap.status_code})
    with connect(dsn) as conn:
        device_rows = conn.execute(
            "SELECT device_id, last_change_seq, revoked_at FROM growth_devices "
            "WHERE account_id=%s AND device_id IN (%s, %s)",
            (account_id, device_id, second_device),
        ).fetchall()
    by_device = {str(row["device_id"]): row for row in device_rows}
    checks["device_cursor_and_revocation_persisted"] = (
        int(by_device[str(device_id)]["last_change_seq"]) == acknowledged
        and int(by_device[str(second_device)]["last_change_seq"]) == b_ack
        and by_device[str(second_device)]["revoked_at"] is not None
    )
    record("device_cursor_and_revocation_persisted",
           checks["device_cursor_and_revocation_persisted"],
           {"devices": device_rows})
    (run_dir / "artifacts" / "database.json").write_text(
        json.dumps({"task": task, "answer": answer, "draft": draft},
                   ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return checks


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m3-draft-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    database_name = "growth_m3_" + uuid.uuid4().hex[:12]
    result: dict[str, Any] = {"status": "FAIL"}
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        expected = ["001_initial", "002_learning", "003_sync", "004_memory",
                    "005_review", "006_modules", "007_module_tasks", "008_chat_topics",
                    "009_topic_maintenance", "010_development_events", "011_exports",
                    "012_task_drafts", "013_chat_model_receipts", "014_review_answer_text"]
        if apply_migrations(dsn) != expected:
            raise AssertionError("M3 草稿迁移不完整")
        checks = asyncio.run(exercise(run_dir, dsn, local))
        result = {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
        if result["status"] != "PASS":
            raise AssertionError(f"M3 草稿回放失败：{checks}")
    except Exception as exc:
        result.setdefault("error", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "stage": "M3", "mode": "replay",
                        "check_status": result["status"], "stage_status": "BLOCKED",
                        "database_name": database_name}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (run_dir / "report.md").write_text(
            "# M3 离线回答与草稿回放\n\n"
            "真实 PostgreSQL 和两份 SQLite 文件执行。设备 A 离线保存两版草稿、正式回答、"
            "暂停和继续；重启后按原操作 ID 发送。设备 B 基于旧版本的草稿晚到时保留本机正文，"
            "云端正式回答不被撤销。本机 HTTP 入口验证离线草稿与回答、受保护的缓存读取、"
            "冲突后保留被阻止的原操作、手工重写草稿，以及进度暂停和恢复。\n\n"
            f"结果：**{result['status']}**；S02 双平台 live 仍未验收。\n",
            encoding="utf-8",
        )
        print(run_dir)


if __name__ == "__main__":
    main()
