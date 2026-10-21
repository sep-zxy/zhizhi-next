"""Exercise two device identities against the real cloud API and PostgreSQL."""

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
from m1_cloud_smoke import ISSUER, now, save_json, token
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


async def exercise(run_dir: Path, dsn: str, local: dict[str, Any]) -> None:
    seed_dir = run_dir / "seed-m2"
    (seed_dir / "artifacts").mkdir(parents=True)
    await seed_m2(seed_dir, dsn, local)
    seed_chain = json.loads((seed_dir / "chain.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(seed_chain["account_id"])
    note_id = uuid.UUID(seed_chain["note_id"])
    with connect(dsn) as conn:
        row = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s ORDER BY last_seen LIMIT 1",
            (account_id,),
        ).fetchone()
        assert row is not None
        device_a = row["device_id"]
    device_b = uuid.uuid4()
    other_account, other_device = uuid.uuid4(), uuid.uuid4()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)))
    steps: list[dict[str, Any]] = []
    checks: dict[str, bool] = {}
    local_records: dict[str, Any] = {}
    trace_id = str(uuid.uuid4())

    def record_local(label: str, actual: dict[str, Any], passed: bool, device: uuid.UUID) -> None:
        step = {
            "step": label, "started_at": now(), "finished_at": now(),
            "device_id": str(device), "trace_id": trace_id,
            "actual": actual, "verdict": "PASS" if passed else "FAIL",
        }
        steps.append(step)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(step, ensure_ascii=False) + "\n")

    async def call(
        client: httpx.AsyncClient, method: str, path: str, expected: int, label: str,
        *, body: dict[str, Any] | None = None, device: uuid.UUID = device_a,
        account: uuid.UUID = account_id,
    ) -> dict[str, Any]:
        started = now()
        headers = {
            "Authorization": f"Bearer {token(private_key, account)}",
            "X-Device-Id": str(device), "X-Trace-Id": trace_id,
        }
        response = await client.request(method, path, headers=headers, json=body)
        actual = response.json()
        step = {
            "step": label, "started_at": started, "finished_at": now(),
            "device_id": str(device), "trace_id": trace_id,
            "operation_id": body.get("operation_id") if body else None,
            "expected_status": expected, "actual_status": response.status_code,
            "actual": actual,
            "verdict": "PASS" if response.status_code == expected else "FAIL",
        }
        steps.append(step)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(step, ensure_ascii=False) + "\n")
        if response.status_code != expected:
            raise AssertionError(f"{label}: {response.status_code} != {expected}: {actual}")
        return actual

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        await call(
            client, "POST", "/v1/devices", 201, "register_device_b",
            body={"operation_id": str(uuid.uuid4()), "device_id": str(device_b)},
            device=device_b,
        )
        await call(
            client, "POST", "/v1/devices", 201, "register_other_account",
            body={"operation_id": str(uuid.uuid4()), "device_id": str(other_device)},
            device=other_device, account=other_account,
        )
        initial = await call(
            client, "GET", "/v1/sync/bootstrap", 200, "device_b_bootstrap",
            device=device_b,
        )
        initial_notes = initial["entities"]["notes"]
        checks["bootstrap_account_snapshot"] = (
            len(initial_notes) == 1
            and initial_notes[0]["note_id"] == str(note_id)
            and initial_notes[0]["revision"] == 1
        )
        base_text = initial_notes[0]["content_text"]
        base_watermark = initial["high_watermark"]
        other = await call(
            client, "GET", "/v1/sync/bootstrap", 200, "other_account_bootstrap",
            device=other_device, account=other_account,
        )
        checks["account_isolation"] = not other["entities"]["notes"]
        device_a_text = base_text + "\nWindows 修订。"
        device_b_text = base_text + "\n第二设备离线修订。"
        first = await call(
            client, "PUT", f"/v1/notes/{note_id}/revisions", 200, "device_a_revision",
            body={"operation_id": str(uuid.uuid4()), "base_revision": 1,
                  "content_text": device_a_text},
        )
        checks["first_edit_revision_two"] = first["revision"] == 2
        stale_body = {
            "operation_id": str(uuid.uuid4()), "base_revision": 1,
            "content_text": device_b_text,
        }
        conflict = await call(
            client, "PUT", f"/v1/notes/{note_id}/revisions", 409,
            "device_b_stale_edit", body=stale_body, device=device_b,
        )
        repeated = await call(
            client, "PUT", f"/v1/notes/{note_id}/revisions", 409,
            "device_b_retry_same_operation", body=stale_body, device=device_b,
        )
        checks["fixed_conflict_result"] = (
            conflict == repeated and conflict["code"] == "REVISION_CONFLICT"
            and conflict["server_revision"] == 2
        )
        before_merge = await call(
            client, "GET", "/v1/sync/bootstrap", 200, "device_b_read_three_way",
            device=device_b,
        )
        revisions = before_merge["entities"]["note_revisions"]
        conflicts = before_merge["entities"]["note_conflicts"]
        checks["three_way_text_retained"] = (
            [item["content_text"] for item in revisions] == [base_text, device_a_text]
            and len(conflicts) == 1
            and conflicts[0]["proposed_text"] == device_b_text
            and conflicts[0]["conflict_id"] == conflict["conflict_id"]
        )
        merged_text = base_text + "\nWindows 修订。\n第二设备离线修订。"
        merged = await call(
            client, "POST", f"/v1/notes/{note_id}/resolve", 200,
            "user_resolves_conflict",
            body={"operation_id": str(uuid.uuid4()), "base_revision": 2,
                  "conflict_ids": [conflict["conflict_id"]], "content_text": merged_text},
            device=device_b,
        )
        checks["merge_revision_three"] = merged["revision"] == 3
        after_merge = await call(
            client, "GET", "/v1/sync/bootstrap", 200, "device_a_reads_merge"
        )
        checks["merged_text_visible"] = (
            after_merge["entities"]["notes"][0]["content_text"] == merged_text
            and after_merge["entities"]["note_conflicts"][0]["resolved_revision"] == 3
        )
        deleted = await call(
            client, "DELETE", f"/v1/notes/{note_id}", 200, "device_a_delete_note",
            body={"operation_id": str(uuid.uuid4()), "base_revision": 3},
        )
        checks["delete_revision_four"] = deleted["revision"] == 4
        late = await call(
            client, "PUT", f"/v1/notes/{note_id}/revisions", 410,
            "device_b_late_update_rejected",
            body={"operation_id": str(uuid.uuid4()), "base_revision": 1,
                  "content_text": device_b_text},
            device=device_b,
        )
        checks["deleted_note_cannot_resurrect"] = late["code"] == "ENTITY_DELETED"
        page = await call(
            client, "GET", f"/v1/sync/changes?after={base_watermark}&limit=2",
            200, "changes_first_page", device=device_b,
        )
        boundary = page["high_watermark"]
        changes = list(page["changes"])
        while page["has_more"]:
            cursor = page["next_after"]
            page = await call(
                client, "GET",
                f"/v1/sync/changes?after={cursor}&until={boundary}&limit=2",
                200, f"changes_page_after_{cursor}", device=device_b,
            )
            changes.extend(page["changes"])
        checks["paged_cursor_no_gap"] = [item["change_seq"] for item in changes] == list(
            range(base_watermark + 1, boundary + 1)
        )
        checks["tombstone_in_feed"] = any(
            item["event_type"] == "note_deleted"
            and item["deleted_at"] is not None for item in changes
        )
        final = await call(
            client, "GET", "/v1/sync/bootstrap", 200, "device_b_final_bootstrap",
            device=device_b,
        )
        checks["tombstone_persists"] = (
            final["entities"]["notes"][0]["deleted_at"] is not None
            and final["entities"]["notes"][0]["revision"] == 4
        )
        workspace_note_id = uuid.uuid4()
        await call(
            client, "POST", "/v1/notes", 201, "workspace_note_created",
            body={"operation_id": str(uuid.uuid4()), "note_id": str(workspace_note_id),
                  "task_id": seed_chain["task_id"], "topic_id": seed_chain["topic_id"],
                  "content_text": "基础版：我需要检查线程池。"},
        )
        workspace_device = uuid.uuid4()
        await call(
            client, "POST", "/v1/devices", 201, "workspace_device_registered",
            body={"operation_id": str(uuid.uuid4()), "device_id": str(workspace_device)},
            device=workspace_device,
        )
        workspace_state = run_dir / "artifacts" / "note-workspace"
        workspace_headers = {
            "origin": "http://localhost:8765", "X-AhaDiff-Token": "test-token",
        }
        desktop = TestClient(
            create_desktop_app(ServeState(state_dir=workspace_state, token="test-token")),
            base_url="http://localhost:8765",
        )
        with GrowthLocalRepository(workspace_state / "growth.sqlite") as workspace_ledger:
            workspace_store = GrowthSyncStore(
                workspace_ledger, account_id, workspace_device,
            )
            workspace_sync = GrowthSyncClient(
                workspace_store, client, token(private_key, account_id),
            )
            await workspace_sync.bootstrap()
            workspace_url = "/api/growth/local/sync/workspace"
            assert desktop.get(workspace_url).status_code == 401
            cached = desktop.get(workspace_url, headers=workspace_headers).json()
            before_edit = next(
                item for item in cached["notes"]
                if item["note_id"] == str(workspace_note_id)
            )
            checks["workspace_offline_base_cached"] = (
                before_edit["revision"] == 1
                and before_edit["revisions"][0]["content_text"]
                == "基础版：我需要检查线程池。"
            )
            project_id = before_edit["project_id"]
            assert project_id is not None
            with workspace_ledger.connection:
                workspace_ledger.connection.execute(
                    "INSERT INTO projects(project_id, name, created_at) "
                    "VALUES (?, ?, ?)", (project_id, "同步策略测试项目", now()),
                )
                workspace_ledger.connection.execute(
                    "INSERT INTO project_policies(project_id, updated_at) "
                    "VALUES (?, ?)", (project_id, now()),
                )
                workspace_ledger.grant_cloud_sync(
                    project_id, str(account_id), "http://growth.test",
                )
            await call(
                client, "PUT", f"/v1/notes/{workspace_note_id}/revisions", 200,
                "workspace_server_branch",
                body={"operation_id": str(uuid.uuid4()), "base_revision": 1,
                      "content_text": "服务器版：加上线程池边界。"},
            )
            local_revision = desktop.post(
                f"/api/growth/local/sync/notes/{workspace_note_id}/revisions",
                headers=workspace_headers,
                json={"account_id": str(account_id), "base_revision": 1,
                      "content_text": "本地版：补充异常处理。"},
            )
            assert local_revision.status_code == 201, local_revision.text
            workspace_ledger.set_project_policy(
                project_id, local_processing=True, model_allowed=False,
                cloud_allowed=False,
            )
            checks["workspace_policy_pauses_pending"] = (
                await workspace_sync.push() == 0
                and workspace_ledger.connection.execute(
                    "SELECT COUNT(*) AS total FROM sync_publication_links "
                    "WHERE account_id=? AND operation_id=?",
                    (str(account_id), local_revision.json()["operation_id"]),
                ).fetchone()["total"] == 1
                and workspace_ledger.connection.execute(
                    "SELECT state FROM sync_outbox WHERE operation_id=?",
                    (local_revision.json()["operation_id"],),
                ).fetchone()["state"] == "pending"
            )
            workspace_ledger.grant_cloud_sync(
                project_id, str(account_id), "http://growth.test",
            )
            assert await workspace_sync.push() == 1
            assert await workspace_sync.pull() >= 1
            conflicted = next(
                item for item in desktop.get(
                    workspace_url, headers=workspace_headers,
                ).json()["notes"] if item["note_id"] == str(workspace_note_id)
            )
            open_conflicts = [
                item for item in conflicted["conflicts"]
                if item.get("resolved_revision") is None
            ]
            checks["workspace_three_originals"] = (
                conflicted["note"]["content_text"] == "服务器版：加上线程池边界。"
                and any(item["content_text"] == "基础版：我需要检查线程池。"
                        for item in conflicted["revisions"])
                and len(open_conflicts) == 1
                and open_conflicts[0]["proposed_text"] == "本地版：补充异常处理。"
                and conflicted["drafts"][0]["state"] == "conflict"
            )
            merge = desktop.post(
                f"/api/growth/local/sync/notes/{workspace_note_id}/resolve",
                headers=workspace_headers,
                json={"account_id": str(account_id), "base_revision": 2,
                      "conflict_ids": [open_conflicts[0]["conflict_id"]],
                      "content_text": "合并版：检查线程池边界和异常处理。"},
            )
            assert merge.status_code == 201, merge.text
            assert await workspace_sync.push() == 1
            assert await workspace_sync.pull() >= 1
            merged = next(
                item for item in desktop.get(
                    workspace_url, headers=workspace_headers,
                ).json()["notes"] if item["note_id"] == str(workspace_note_id)
            )
            checks["workspace_merge_persisted"] = (
                merged["revision"] == 3
                and merged["note"]["content_text"]
                == "合并版：检查线程池边界和异常处理。"
                and merged["conflicts"][0]["resolved_revision"] == 3
                and merged["revisions"][-1]["source_conflict_ids"]
                == [open_conflicts[0]["conflict_id"]]
            )
            await call(
                client, "PUT", f"/v1/notes/{workspace_note_id}/revisions", 200,
                "workspace_second_server_branch",
                body={"operation_id": str(uuid.uuid4()), "base_revision": 3,
                      "content_text": "服务器第二次修订。"},
            )
            second_local = desktop.post(
                f"/api/growth/local/sync/notes/{workspace_note_id}/revisions",
                headers=workspace_headers,
                json={"account_id": str(account_id), "base_revision": 3,
                      "content_text": "本机第二次修订。"},
            )
            assert second_local.status_code == 201, second_local.text
            assert await workspace_sync.push() == 1
            assert await workspace_sync.pull() >= 1
            raced = next(
                item for item in desktop.get(
                    workspace_url, headers=workspace_headers,
                ).json()["notes"] if item["note_id"] == str(workspace_note_id)
            )
            second_conflict = next(
                item for item in raced["conflicts"]
                if item.get("resolved_revision") is None
            )
            queued_resolution = desktop.post(
                f"/api/growth/local/sync/notes/{workspace_note_id}/resolve",
                headers=workspace_headers,
                json={"account_id": str(account_id), "base_revision": 4,
                      "conflict_ids": [second_conflict["conflict_id"]],
                      "content_text": "本机排队的合并正文。"},
            )
            assert queued_resolution.status_code == 201, queued_resolution.text
            await call(
                client, "POST", f"/v1/notes/{workspace_note_id}/resolve", 200,
                "workspace_other_device_resolves_first",
                body={"operation_id": str(uuid.uuid4()), "base_revision": 4,
                      "conflict_ids": [second_conflict["conflict_id"]],
                      "content_text": "另一设备先提交的合并正文。"},
            )
            assert await workspace_sync.pull() >= 1
            assert await workspace_sync.push() == 0
            stale_resolution = workspace_ledger.connection.execute(
                "SELECT state, response_json FROM sync_outbox WHERE operation_id=?",
                (queued_resolution.json()["operation_id"],),
            ).fetchone()
            checks["workspace_stale_resolution_preserved"] = (
                stale_resolution is not None
                and stale_resolution["state"] == "conflict"
                and json.loads(stale_resolution["response_json"])["code"]
                == "CONFLICT_ALREADY_RESOLVED"
                and workspace_ledger.connection.execute(
                    "SELECT content_text FROM sync_note_drafts WHERE operation_id=?",
                    (queued_resolution.json()["operation_id"],),
                ).fetchone()["content_text"] == "本机排队的合并正文。"
            )
            delete = desktop.request(
                "DELETE", f"/api/growth/local/sync/notes/{workspace_note_id}",
                headers=workspace_headers,
                json={"account_id": str(account_id), "base_revision": 5},
            )
            assert delete.status_code == 201, delete.text
            assert await workspace_sync.push() == 1
            assert await workspace_sync.pull() >= 1
            removed = next(
                item for item in desktop.get(
                    workspace_url, headers=workspace_headers,
                ).json()["notes"] if item["note_id"] == str(workspace_note_id)
            )
            checks["workspace_delete_and_recovery"] = (
                removed["deleted_at"] is not None
                and removed["drafts"][0]["state"] == "recovery"
                and removed["drafts"][0]["content_text"] == "本地版：补充异常处理。"
            )
            record_local(
                "desktop_workspace_conflict_merge_delete",
                {"base": before_edit, "conflict": conflicted,
                 "merged": merged, "deleted": removed},
                all(checks[name] for name in (
                    "workspace_offline_base_cached", "workspace_three_originals",
                    "workspace_policy_pauses_pending", "workspace_merge_persisted",
                    "workspace_stale_resolution_preserved",
                    "workspace_delete_and_recovery",
                )), workspace_device,
            )
        outbox_note_id = uuid.uuid4()
        await call(
            client, "POST", "/v1/notes", 201, "create_outbox_note",
            body={"operation_id": str(uuid.uuid4()), "note_id": str(outbox_note_id),
                  "task_id": seed_chain["task_id"], "topic_id": seed_chain["topic_id"],
                  "content_text": "离线同步测试笔记。"},
        )
        local_db_a = run_dir / "device-a.sqlite"
        local_db_b = run_dir / "device-b.sqlite"
        with GrowthLocalRepository(local_db_a) as ledger_a:
            store_a = GrowthSyncStore(ledger_a, account_id, device_a)
            client_a = GrowthSyncClient(store_a, client, token(private_key, account_id))
            await client_a.bootstrap()
            retry_operation = store_a.queue_note_revision(
                outbox_note_id, 1, "离线同步测试笔记。网络中断后仍保留。"
            )

            def fail_request(_request: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("验收注入：响应超时，结果未知")

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(fail_request), base_url="http://growth.test"
            ) as failed_http:
                failed_client = GrowthSyncClient(
                    store_a, failed_http, token(private_key, account_id)
                )
                try:
                    await failed_client.push()
                except httpx.ReadTimeout:
                    pass
                else:
                    raise AssertionError("故障注入未触发")
            checks["timeout_keeps_same_operation"] = (
                len(store_a.pending()) == 1
                and store_a.pending()[0]["operation_id"] == retry_operation
            )
            local_records["after_timeout"] = store_a.pending()
            record_local(
                "timeout_keeps_outbox", {"operation_id": retry_operation,
                                         "pending": local_records["after_timeout"]},
                checks["timeout_keeps_same_operation"], device_a,
            )
        with GrowthLocalRepository(local_db_a) as reopened_a:
            store_a = GrowthSyncStore(reopened_a, account_id, device_a)
            client_a = GrowthSyncClient(store_a, client, token(private_key, account_id))
            checks["restart_restores_outbox"] = (
                len(store_a.pending()) == 1
                and store_a.pending()[0]["operation_id"] == retry_operation
            )
            local_records["after_restart"] = store_a.pending()
            record_local(
                "restart_restores_outbox", {"pending": local_records["after_restart"]},
                checks["restart_restores_outbox"], device_a,
            )
            checks["retry_commits_once"] = await client_a.push() == 1
            ack = reopened_a.connection.execute(
                "SELECT state, response_json FROM sync_outbox WHERE operation_id=?",
                (retry_operation,),
            ).fetchone()
            checks["outbox_ack_revision_two"] = (
                ack is not None and ack["state"] == "acked"
                and json.loads(ack["response_json"])["revision"] == 2
                and reopened_a.connection.execute(
                    "SELECT state FROM sync_note_drafts WHERE operation_id=?",
                    (retry_operation,),
                ).fetchone()["state"] == "synced"
            )
            local_records["after_ack"] = {
                "outbox": dict(ack),
                "draft": dict(reopened_a.connection.execute(
                    "SELECT * FROM sync_note_drafts WHERE operation_id=?",
                    (retry_operation,),
                ).fetchone()),
            }
            record_local(
                "retry_acknowledged", local_records["after_ack"],
                checks["retry_commits_once"] and checks["outbox_ack_revision_two"], device_a,
            )
        with GrowthLocalRepository(local_db_b) as ledger_b:
            store_b = GrowthSyncStore(ledger_b, account_id, device_b)
            client_b = GrowthSyncClient(store_b, client, token(private_key, account_id))
            await client_b.bootstrap()
            remote = ledger_b.connection.execute(
                "SELECT payload_json FROM sync_entities WHERE account_id=? "
                "AND entity_type='note' AND entity_id=?",
                (str(account_id), str(outbox_note_id)),
            ).fetchone()
            checks["other_device_reads_outbox_edit"] = (
                remote is not None
                and json.loads(remote["payload_json"])["content_text"]
                == "离线同步测试笔记。网络中断后仍保留。"
            )
            offline_operation = store_b.queue_note_revision(
                outbox_note_id, 2, "旧设备尚未上传的正文。"
            )
            await call(
                client, "DELETE", f"/v1/notes/{outbox_note_id}", 200,
                "delete_while_other_device_offline",
                body={"operation_id": str(uuid.uuid4()), "base_revision": 2},
            )
            checks["pulls_deletion"] = await client_b.pull(limit=2) >= 1
            draft = ledger_b.connection.execute(
                "SELECT state, content_text FROM sync_note_drafts WHERE operation_id=?",
                (offline_operation,),
            ).fetchone()
            pending = ledger_b.connection.execute(
                "SELECT state FROM sync_outbox WHERE operation_id=?",
                (offline_operation,),
            ).fetchone()
            checks["deleted_pending_text_in_recovery"] = (
                draft is not None and draft["state"] == "recovery"
                and draft["content_text"] == "旧设备尚未上传的正文。"
                and pending is not None and pending["state"] == "deleted"
                and await client_b.push() == 0
            )
            local_records["after_tombstone"] = {
                "draft": dict(draft), "outbox": dict(pending),
                "cursor": store_b.cursor(),
            }
            record_local(
                "tombstone_moves_draft_to_recovery", local_records["after_tombstone"],
                checks["deleted_pending_text_in_recovery"], device_b,
            )

    with connect(dsn) as conn:
        row = conn.execute(
            "SELECT revision, content_text, content_hash, deleted_at FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account_id, note_id),
        ).fetchone()
        revisions_db = conn.execute(
            "SELECT revision, content_text, source_conflict_ids FROM growth_note_revisions "
            "WHERE account_id=%s AND note_id=%s ORDER BY revision", (account_id, note_id),
        ).fetchall()
        conflicts_db = conn.execute(
            "SELECT conflict_id, base_revision, observed_server_revision, proposed_text, "
            "resolved_revision FROM growth_note_conflicts "
            "WHERE account_id=%s AND note_id=%s", (account_id, note_id),
        ).fetchall()
        events = conn.execute(
            "SELECT event_id, event_type, trace_id FROM growth_domain_events "
            "WHERE account_id=%s ORDER BY occurred_at, event_id", (account_id,),
        ).fetchall()
    checks["database_keeps_all_branches"] = (
        row is not None and row["revision"] == 4
        and row["content_hash"].strip() == hashlib.sha256(merged_text.encode()).hexdigest()
        and [item["content_text"] for item in revisions_db]
        == [base_text, device_a_text, merged_text]
        and len(conflicts_db) == 1
        and conflicts_db[0]["proposed_text"] == device_b_text
        and conflicts_db[0]["resolved_revision"] == 3
    )
    save_json(run_dir / "artifacts" / "postgres.json", {
        "note": row, "revisions": revisions_db, "conflicts": conflicts_db,
        "changes": changes,
    })
    save_json(run_dir / "artifacts" / "local-sync.json", local_records)
    save_json(run_dir / "chain.json", {
        "seed": "seed-m2", "account_id": str(account_id), "device_a": str(device_a),
        "device_b": str(device_b), "note_id": str(note_id),
        "conflict_id": conflict["conflict_id"], "base_watermark": base_watermark,
        "final_watermark": boundary,
    })
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(item, default=str, ensure_ascii=False) + "\n" for item in events),
        encoding="utf-8",
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    save_json(run_dir / "result.json", {"status": status, "checks": checks})
    save_json(run_dir / "manifest.json", {
        "run_id": run_dir.name, "stage": "M3", "case_id": "S02-cloud-replay",
        "mode": "replay", "started_at": steps[0]["started_at"],
        "finished_at": now(), "check_status": status, "stage_status": "BLOCKED",
        "stage_blockers": ["无 macOS 实机", "未执行桌面 UI 人工操作", "离线回答/进度尚未完整接入"],
        "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
    })
    (run_dir / "report.md").write_text(
        "# M3 S02 云端双设备回放\n\n"
        "在隔离 PostgreSQL 中，两个设备身份读取同一笔记初始版本。设备 A 写入 revision 2；"
        "设备 B 基于 revision 1 提交，服务端保存冲突分支并固定 409 结果。"
        "用户合并后得到 revision 3；删除生成 revision 4 tombstone，迟到的离线写入得到 410。"
        "bootstrap 和固定边界分页 changes 可读取相应版本；本地 SQLite outbox 在响应超时、"
        "进程重开后保留同一操作 ID，另一设备拉取 tombstone 后保留未上传正文。"
        "本机服务 HTTP 入口验证离线修订、三方原文、合并来源、冲突解决事件、删除传播与"
        "恢复副本；另一设备先解决冲突时，本机停止过期合并请求并保留原文；"
        "未授权请求被拒绝。\n\n"
        f"行为检查：**{status}**。两个身份都运行在 Windows 进程里；未运行 macOS、"
        "桌面 UI 或离线回答全链路，不能计入 S02 live 验收。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(f"M3 cloud checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m3-sync-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    database_name = "growth_m3_" + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        expected = ["001_initial", "002_learning", "003_sync", "004_memory", "005_review",
                    "006_modules", "007_module_tasks", "008_chat_topics",
                    "009_topic_maintenance", "010_development_events",
                    "011_exports", "012_task_drafts", "013_chat_model_receipts", "014_review_answer_text"]
        if apply_migrations(dsn) != expected:
            raise AssertionError("M3 migrations did not apply")
        asyncio.run(exercise(run_dir, dsn, local))
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
            save_json(run_dir / "manifest.json", {
                "run_id": run_id, "stage": "M3", "mode": "replay",
                "check_status": "FAIL", "stage_status": "BLOCKED",
                "database_name": database_name, "error": str(exc),
            })
            (run_dir / "report.md").write_text(
                f"# M3 云端回放失败\n\n`{type(exc).__name__}: {exc}`。此运行保持 FAIL。\n",
                encoding="utf-8",
            )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
