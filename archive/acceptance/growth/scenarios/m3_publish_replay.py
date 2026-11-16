"""Check explicit local publication through the real cloud API and PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from m1_cloud_smoke import ISSUER, save_json, token
from m2_cloud_replay import LOCAL_RUN
from psycopg import sql
from starlette.testclient import TestClient

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect
from ahadiff.growth.learning import GrowthLearningService
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.publish import publication_plan, queue_publication
from ahadiff.growth.sync import GrowthSyncStore, connect_and_sync
from ahadiff.serve.app import create_app as create_desktop_app
from ahadiff.serve.state import ServeState

ROOT = Path(__file__).resolve().parents[2]


async def exercise(run_dir: Path, dsn: str) -> dict[str, bool]:
    source = ROOT / "acceptance" / "runs" / LOCAL_RUN / "local.sqlite"
    local_db = run_dir / "artifacts" / "growth.sqlite"
    with sqlite3.connect(source) as old, sqlite3.connect(local_db) as new:
        old.backup(new)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    account_id = uuid.uuid4()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public)))
    access_token = token(key, account_id)
    checks: dict[str, bool] = {}
    sidecar_dir = run_dir / "artifacts" / "sidecar"
    sidecar_dir.mkdir()
    with sqlite3.connect(source) as old, sqlite3.connect(sidecar_dir / "growth.sqlite") as new:
        old.backup(new)
    with TestClient(create_desktop_app(ServeState(
        state_dir=sidecar_dir, token="local-test-token",
    )), base_url="http://localhost:8765") as sidecar:
        project = sidecar.get(
            "/api/growth/local", headers={"X-AhaDiff-Token": "local-test-token"}
        ).json()["projects"][0]["project_id"]
        preview_body = {"project_id": project}
        denied = sidecar.post(
            "/api/growth/local/sync/preview",
            headers={"Origin": "http://localhost:8765"}, json=preview_body,
        )
        preview = sidecar.post(
            "/api/growth/local/sync/preview",
            headers={"Origin": "http://localhost:8765",
                     "X-AhaDiff-Token": "local-test-token"},
            json=preview_body,
        )
        checks["desktop_preview_requires_local_token"] = (
            denied.status_code == 401 and preview.status_code == 200
            and preview.json()["count"] == 12
        )
        policy_url = f"/api/growth/local/projects/{project}/policy"
        headers = {"Origin": "http://localhost:8765",
                   "X-AhaDiff-Token": "local-test-token"}
        bypass = sidecar.put(policy_url, headers=headers, json={
            "local_processing": True, "model_allowed": False,
            "cloud_allowed": True,
        })
        model_only = sidecar.put(policy_url, headers=headers, json={
            "local_processing": True, "model_allowed": True,
            "cloud_allowed": False,
        })
        checks["project_switches_are_independent_and_cloud_needs_approval"] = (
            bypass.status_code == 422 and model_only.status_code == 200
            and model_only.json()["model_allowed"]
            and not model_only.json()["cloud_allowed"]
        )
    with GrowthLocalRepository(local_db) as ledger:
        project_id = ledger.connection.execute("SELECT project_id FROM projects").fetchone()[0]
        plan = publication_plan(ledger, project_id)
        checks["desktop_preview_matches_publication"] = (
            preview.json()["digest"] == plan["digest"]
        )
        local_path = ledger.connection.execute(
            "SELECT canonical_local_path FROM bindings LIMIT 1"
        ).fetchone()[0]
        encoded = json.dumps(plan, ensure_ascii=False)
        checks["preview_is_exact_and_excludes_raw_source"] = (
            plan["count"] == 12
            and "patch_text" not in encoded
            and "canonical_local_path" not in encoded
            and local_path not in encoded
            and "content" not in plan["operations"][2]["payload"]["source_refs"][0]
        )
        store = GrowthSyncStore(ledger, account_id, uuid.uuid4())
        stale_rejected = False
        try:
            queue_publication(store, plan, "0" * 64)
        except ValueError:
            stale_rejected = True
        checks["stale_approval_queues_nothing"] = (
            stale_rejected
            and ledger.connection.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as client:
            first = await connect_and_sync(
                ledger, client, access_token,
                publish_project_id=project_id, approved_digest=plan["digest"],
            )
            second = await connect_and_sync(
                ledger, client, access_token,
                publish_project_id=project_id, approved_digest=plan["digest"],
            )
        checks["all_local_operations_uploaded_once"] = (
            first["queued"] == 12 and first["uploaded"] == 12
            and first["outbox"]["pending"] == 0
            and second["queued"] == 0 and second["uploaded"] == 0
        )
        checks["cursor_acknowledged"] = (
            first["cursor"] > 0 and first["cursor"] == first["acknowledged_cursor"]
        )
        checks["approval_enables_only_cloud_switch"] = (
            ledger.project_policy(project_id)["cloud_allowed"]
            and not ledger.project_policy(project_id)["model_allowed"]
            and ledger.project_policy(project_id)["local_processing"]
        )
        task_id = ledger.connection.execute("SELECT task_id FROM growth_tasks").fetchone()[0]
        new_note_id = GrowthLearningService(ledger).save_user_note(
            task_id, "云许可开启后新增的本机笔记。", trace_id=str(uuid.uuid4()),
        )
        checks["new_note_and_outbox_share_local_commit"] = (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM engineering_notes WHERE note_id=?", (new_note_id,)
            ).fetchone()[0] == 1
            and ledger.connection.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE target_type='note' "
                "AND target_id=? AND state='pending'", (new_note_id,),
            ).fetchone()[0] == 1
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as client:
            auto = await connect_and_sync(ledger, client, access_token)
        checks["approved_project_auto_uploads_new_note"] = (
            auto["queued"] == 0 and auto["uploaded"] == 1
            and auto["outbox"]["pending"] == 0
        )
        ledger.set_project_policy(
            project_id, local_processing=True, model_allowed=True,
            cloud_allowed=False,
        )
        paused_note_id = GrowthLearningService(ledger).save_user_note(
            task_id, "暂停云同步时保留的本机笔记。", trace_id=str(uuid.uuid4()),
        )
        checks["disabled_cloud_keeps_new_note_local"] = (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM engineering_notes WHERE note_id=?", (paused_note_id,)
            ).fetchone()[0] == 1
            and ledger.connection.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE target_id=?", (paused_note_id,),
            ).fetchone()[0] == 0
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as client:
            paused = await connect_and_sync(ledger, client, access_token)
            resume_plan = publication_plan(ledger, project_id)
            resumed = await connect_and_sync(
                ledger, client, access_token,
                publish_project_id=project_id,
                approved_digest=resume_plan["digest"],
            )
        checks["cloud_pause_and_reapproval_preserve_model_switch"] = (
            paused["uploaded"] == 0 and resumed["queued"] == 1
            and resumed["uploaded"] == 1
            and ledger.project_policy(project_id)["model_allowed"]
            and ledger.project_policy(project_id)["cloud_allowed"]
        )
        held_note_id = GrowthLearningService(ledger).save_user_note(
            task_id, "已入队但临时关闭云同步的笔记。", trace_id=str(uuid.uuid4()),
        )
        ledger.set_project_policy(
            project_id, local_processing=True, model_allowed=True,
            cloud_allowed=False,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as client:
            held = await connect_and_sync(ledger, client, access_token)
            held_plan = publication_plan(ledger, project_id)
            released = await connect_and_sync(
                ledger, client, access_token,
                publish_project_id=project_id,
                approved_digest=held_plan["digest"],
            )
        checks["disabled_cloud_holds_existing_outbox_operation"] = (
            held["uploaded"] == 0 and held["outbox"]["pending"] == 1
            and released["queued"] == 0 and released["uploaded"] == 1
            and released["outbox"]["pending"] == 0
        )
        ids = {
            row["kind"]: row["source_id"] for row in plan["operations"]
            if row["kind"] in {"project", "feature", "snapshot", "analysis", "task", "note"}
        }

    with GrowthLocalRepository(run_dir / "artifacts" / "other-device.sqlite") as other:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as client:
            remote = await connect_and_sync(other, client, access_token)
        received = {
            row["entity_type"]: row["entity_id"]
            for row in other.connection.execute(
                "SELECT entity_type, entity_id FROM sync_entities "
                "WHERE entity_type IN ('project', 'feature', 'snapshot', 'analysis', 'task')"
            )
        }
        remote_notes = {
            row["entity_id"] for row in other.connection.execute(
                "SELECT entity_id FROM sync_entities WHERE entity_type='note'"
            )
        }
        checks["other_device_receives_same_ids"] = (
            all(received.get(kind) == value for kind, value in ids.items()
                if kind != "note")
            and {ids["note"], new_note_id, paused_note_id, held_note_id} <= remote_notes
            and remote["device_id"] != first["device_id"]
        )

    with GrowthLocalRepository(local_db) as ledger:
        secret_note_id = GrowthLearningService(ledger).save_user_note(
            task_id, "sk-123456789012345678901234567890",
            trace_id=str(uuid.uuid4()),
        )
        checks["unsafe_note_remains_local_without_outbox"] = (
            ledger.connection.execute(
                "SELECT COUNT(*) FROM engineering_notes WHERE note_id=?", (secret_note_id,)
            ).fetchone()[0] == 1
            and ledger.connection.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE target_id=?", (secret_note_id,),
            ).fetchone()[0] == 0
            and ledger.project_policy(project_id)["last_publish_error"] is not None
        )

    legacy_db = run_dir / "artifacts" / "v11-publication.sqlite"
    with sqlite3.connect(local_db) as current, sqlite3.connect(legacy_db) as legacy:
        current.backup(legacy)
        legacy.execute("DROP TABLE local_chat_model_requests")
        legacy.execute("DROP TABLE local_development_event_captures")
        legacy.execute("DROP TABLE sync_publication_links")
        legacy.execute("DROP TABLE project_policies")
        legacy.execute("PRAGMA user_version=11")
        legacy.commit()
    with GrowthLocalRepository(legacy_db) as migrated:
        links = migrated.connection.execute(
            "SELECT COUNT(*) FROM sync_publication_links WHERE project_id=?",
            (project_id,),
        ).fetchone()[0]
        checks["v11_pending_publication_policy_backfilled"] = (
            migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 14
            and migrated.project_policy(project_id)["cloud_allowed"]
            and migrated.project_policy(project_id)["cloud_account_id"] == str(account_id)
            and links == 15
        )

    privacy_db = run_dir / "artifacts" / "privacy.sqlite"
    with sqlite3.connect(source) as old, sqlite3.connect(privacy_db) as new:
        old.backup(new)
    with GrowthLocalRepository(privacy_db) as privacy:
        for label, text in (
            ("secret", "sk-123456789012345678901234567890"),
            ("absolute_path", "C:\\Users\\example\\private.txt"),
        ):
            with privacy.connection:
                privacy.connection.execute(
                    "UPDATE engineering_notes SET content_text=?", (text,)
                )
            try:
                publication_plan(privacy, project_id)
            except ValueError:
                checks[f"reject_{label}_in_user_note"] = True
            else:
                checks[f"reject_{label}_in_user_note"] = False

    with connect(dsn) as conn:
        rows = {
            name: conn.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE account_id=%s",
                (account_id,),
            ).fetchone()["total"]
            for name, table in {
                "projects": "growth_projects", "features": "growth_features",
                "snapshots": "growth_snapshots",
                "analyses": "growth_analysis_runs", "attempts": "growth_learning_attempts",
                "notes": "growth_notes", "operations": "growth_sync_operations",
            }.items()
        }
        checks["postgres_has_full_learning_chain"] = rows == {
            "projects": 1, "features": 1, "snapshots": 1, "analyses": 1,
            "attempts": 2, "notes": 4, "operations": 22,
        }
    save_json(run_dir / "artifacts" / "publication-summary.json", {
        "plan_digest": plan["digest"], "count": plan["count"],
        "first": first, "second": second, "auto": auto,
        "paused": paused, "resumed": resumed, "held": held,
        "released": released, "remote": remote,
        "postgres_rows": rows, "checks": checks,
    })
    return checks


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    run_id = f"m3-publish-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    name = "growth_m3_publish_" + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={name}")
        apply_migrations(dsn)
        checks = asyncio.run(exercise(run_dir, dsn))
        status = "PASS" if all(checks.values()) else "FAIL"
        save_json(run_dir / "result.json", {"status": status, "checks": checks})
        save_json(run_dir / "manifest.json", {
            "run_id": run_id, "stage": "M3", "case_id": "S01-local-cloud-publication",
            "mode": "replay", "check_status": status, "stage_status": "BLOCKED",
            "database_name": name,
            "stage_blockers": ["无 macOS 实机", "无外部 Supabase/Auth", "无真实模型与 ReMe"],
        })
        (run_dir / "report.md").write_text(
            "# M3 本机项目发布回放\n\n"
            f"本机完整学习链经真实云 API 与隔离 PostgreSQL 检查：**{status}**。"
            "此回放不计入 S01 live 验收。\n", encoding="utf-8",
        )
        if status != "PASS":
            raise AssertionError(checks)
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
