"""Verify the desktop sync bridge against the cloud API and isolated PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from m1_cloud_smoke import ISSUER, save_json, token
from m2_cloud_replay import LOCAL_RUN
from m2_cloud_replay import exercise as seed_m2
from psycopg import sql
from starlette.testclient import TestClient

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.sync import GrowthSyncStore, SyncOriginMismatchError, connect_and_sync
from ahadiff.serve import routes_growth
from ahadiff.serve.app import create_app as create_desktop_app
from ahadiff.serve.state import ServeState

ROOT = Path(__file__).resolve().parents[2]


class DropRegistrationResponse(httpx.AsyncBaseTransport):
    def __init__(self, app: Any):
        self.inner = httpx.ASGITransport(app=app)
        self.dropped = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if request.url.path == "/v1/devices" and not self.dropped:
            self.dropped = True
            await response.aclose()
            raise httpx.ReadTimeout("injected lost registration response", request=request)
        return response


async def exercise(run_dir: Path, dsn: str, local: dict[str, Any]) -> dict[str, bool]:
    seed_dir = run_dir / "seed-m2"
    (seed_dir / "artifacts").mkdir(parents=True)
    await seed_m2(seed_dir, dsn, local)
    chain = json.loads((seed_dir / "chain.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(chain["account_id"])
    note_id = uuid.UUID(chain["note_id"])
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)))
    auth_token = token(private_key, account_id)
    checks: dict[str, bool] = {}
    database = run_dir / "artifacts" / "desktop-growth.sqlite"
    dropped = DropRegistrationResponse(app)
    async with httpx.AsyncClient(transport=dropped, base_url="http://growth.test") as client:
        with GrowthLocalRepository(database) as ledger:
            try:
                await connect_and_sync(ledger, client, auth_token)
            except httpx.ReadTimeout:
                pass
            else:
                raise AssertionError("registration response fault was not injected")
            retained = ledger.connection.execute(
                "SELECT device_id, bootstrapped FROM sync_accounts WHERE account_id=?",
                (str(account_id),),
            ).fetchone()
            checks["registration_timeout_keeps_device_locally"] = (
                dropped.dropped and retained is not None and retained["bootstrapped"] == 0
            )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        identity = await client.get(
            "/v1/account", headers={"Authorization": f"Bearer {auth_token}"}
        )
        checks["account_identity_from_verified_token"] = (
            identity.status_code == 200
            and identity.json() == {"account_id": str(account_id)}
        )
        with GrowthLocalRepository(database) as ledger:
            first = await connect_and_sync(ledger, client, auth_token)
            device_id = uuid.UUID(first["device_id"])
            checks["retry_uses_original_device_id"] = (
                retained is not None and first["device_id"] == retained["device_id"]
            )
            cached = ledger.connection.execute(
                "SELECT payload_json FROM sync_entities WHERE account_id=? "
                "AND entity_type='note' AND entity_id=?",
                (str(account_id), str(note_id)),
            ).fetchone()
            checks["first_connection_bootstraps_cloud_note"] = (
                cached is not None and first["cursor"] == first["acknowledged_cursor"]
                and first["outbox"]["pending"] == 0
            )
            store = GrowthSyncStore(ledger, account_id, device_id)
            operation_id = store.queue_note_revision(
                note_id, 1, "桌面同步桥接的本机手写修订。"
            )
            queued = ledger.connection.execute(
                "SELECT state FROM sync_outbox WHERE operation_id=?", (operation_id,)
            ).fetchone()
            checks["note_revision_queued_before_network"] = (
                queued is not None and queued["state"] == "pending"
            )
            second = await connect_and_sync(ledger, client, auth_token)
            settled = ledger.connection.execute(
                "SELECT state FROM sync_outbox WHERE operation_id=?", (operation_id,)
            ).fetchone()
            checks["bridge_uploads_and_acknowledges"] = (
                second["device_id"] == str(device_id) and second["uploaded"] == 1
                and second["cursor"] == second["acknowledged_cursor"]
                and settled is not None and settled["state"] == "acked"
            )
        with GrowthLocalRepository(database) as reopened:
            third = await connect_and_sync(reopened, client, auth_token)
            checks["restart_reuses_device_and_cursor"] = (
                third["device_id"] == str(device_id)
                and third["cursor"] == second["cursor"]
                and third["uploaded"] == 0
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://other.test"
            ) as other_origin:
                try:
                    await connect_and_sync(reopened, other_origin, auth_token)
                except SyncOriginMismatchError:
                    checks["account_cache_rejects_other_cloud_origin"] = True
                else:
                    checks["account_cache_rejects_other_cloud_origin"] = False
        wrong = await client.get(
            "/v1/account", headers={"Authorization": "Bearer invalid"}
        )
        checks["invalid_token_rejected"] = wrong.status_code == 401
    original_async_client = httpx.AsyncClient

    def remote_client(*_args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original_async_client(
            transport=httpx.ASGITransport(app=app),
            base_url=kwargs["base_url"], timeout=kwargs["timeout"],
            follow_redirects=kwargs["follow_redirects"],
        )

    desktop = create_desktop_app(ServeState(
        state_dir=run_dir / "artifacts" / "sidecar", token="local-test-token",
    ))
    request_body = {
        "cloud_url": "http://127.0.0.1:8999", "access_token": auth_token,
    }
    with TestClient(desktop, base_url="http://localhost:8765") as sidecar:
        headers = {"X-AhaDiff-Token": "local-test-token",
                   "Origin": "http://localhost:8765"}
        unauthorized = sidecar.post(
            "/api/growth/local/sync", headers={"Origin": "http://localhost:8765"},
            json=request_body,
        )
        invalid_origin = sidecar.post(
            "/api/growth/local/sync", headers=headers,
            json=request_body | {"cloud_url": "http://example.com"},
        )
        with patch.object(routes_growth.httpx, "AsyncClient", side_effect=remote_client):
            through_desktop = sidecar.post(
                "/api/growth/local/sync", headers=headers, json=request_body,
            )
        checks["desktop_route_requires_token_and_secure_origin"] = (
            unauthorized.status_code == 401 and invalid_origin.status_code == 422
        )
        checks["desktop_route_calls_cloud_without_returning_token"] = (
            through_desktop.status_code == 200
            and through_desktop.json()["account_id"] == str(account_id)
            and auth_token not in through_desktop.text
        )
    with connect(dsn) as conn:
        note = conn.execute(
            "SELECT revision, content_text FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account_id, note_id),
        ).fetchone()
        operations = conn.execute(
            "SELECT COUNT(*) AS total FROM growth_sync_operations "
            "WHERE account_id=%s AND operation_id=%s", (account_id, operation_id),
        ).fetchone()
        checks["cloud_contains_one_revision_and_operation"] = (
            note is not None and note["revision"] == 2
            and note["content_text"] == "桌面同步桥接的本机手写修订。"
            and operations is not None and operations["total"] == 1
        )
    save_json(run_dir / "artifacts" / "bridge-summary.json", {
        "first": first, "second": second, "third": third,
        "operation_id": operation_id, "checks": checks,
    })
    return checks


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m3-bridge-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    database_name = "growth_m3_bridge_" + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        apply_migrations(dsn)
        checks = asyncio.run(exercise(run_dir, dsn, local))
        status = "PASS" if all(checks.values()) else "FAIL"
        save_json(run_dir / "result.json", {"status": status, "checks": checks})
        save_json(run_dir / "manifest.json", {
            "run_id": run_id, "stage": "M3", "case_id": "S02-desktop-sync-bridge",
            "mode": "replay", "check_status": status, "stage_status": "BLOCKED",
            "database_name": database_name,
            "stage_blockers": ["无 macOS 实机", "无真实 Supabase Auth", "无完整桌面同步界面"],
        })
        (run_dir / "report.md").write_text(
            "# M3 桌面同步桥接回放\n\n"
            "经验证的账号令牌确定账号 ID；首次连接注册设备并 bootstrap，"
            "本机 SQLite 入队的笔记修订经云端批量接口上传、拉取和确认，重启复用设备 ID 与游标。"
            f"隔离 PostgreSQL 行为检查：**{status}**。此回放不计入 S02 live 验收。\n",
            encoding="utf-8",
        )
        if status != "PASS":
            raise AssertionError(f"M3 bridge checks failed: {checks}")
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
            save_json(run_dir / "manifest.json", {
                "run_id": run_id, "stage": "M3", "mode": "replay",
                "check_status": "FAIL", "stage_status": "BLOCKED",
                "database_name": database_name,
            })
            (run_dir / "report.md").write_text(
                f"# M3 桌面同步桥接失败\n\n`{type(exc).__name__}: {exc}`。此运行保持 FAIL。\n",
                encoding="utf-8",
            )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
