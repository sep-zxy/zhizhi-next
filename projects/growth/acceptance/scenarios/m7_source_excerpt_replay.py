"""Verify explicit excerpt sync and account isolation on local PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations

ISSUER = "https://growth-excerpt-test.invalid/auth/v1"
ROOT = Path(__file__).resolve().parents[2]


async def exercise(dsn: str) -> dict[str, bool]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public)))
    account, other = uuid.uuid4(), uuid.uuid4()
    first, second, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    project, feature, snapshot, ref, batch_ref = (uuid.uuid4() for _ in range(5))
    content = "export const answer = 2;\n"
    sha = hashlib.sha256(content.encode()).hexdigest()

    def headers(who: uuid.UUID, device: uuid.UUID | None) -> dict[str, str]:
        claims = {
            "iss": ISSUER, "aud": "authenticated", "sub": str(who),
            "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=10),
        }
        result = {
            "Authorization": f"Bearer {jwt.encode(claims, private, algorithm='RS256')}",
            "X-Trace-Id": str(uuid.uuid4()),
        }
        if device:
            result["X-Device-Id"] = str(device)
        return result

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test",
    ) as client:
        async def post(path: str, body: dict[str, object], expected: int,
                       who: uuid.UUID = account, device: uuid.UUID | None = first,
                       ) -> httpx.Response:
            response = await client.post(path, json=body, headers=headers(who, device))
            assert response.status_code == expected, (path, response.status_code, response.text)
            return response

        for who, device in ((account, first), (account, second), (other, outsider)):
            await post("/v1/devices", {
                "operation_id": str(uuid.uuid4()), "device_id": str(device),
            }, 201, who, None)
        await post("/v1/projects", {
            "operation_id": str(uuid.uuid4()), "project_id": str(project),
            "name": "片段同步验收", "sync_policy": {
                "cloud_allowed": True, "model_allowed": False,
            },
        }, 201)
        await post("/v1/features", {
            "operation_id": str(uuid.uuid4()), "feature_id": str(feature),
            "project_id": str(project), "label": "答案修正", "base_ref": "HEAD",
            "start_base_sha": "a" * 40,
        }, 201)
        await post("/v1/snapshots", {
            "operation_id": str(uuid.uuid4()), "project_id": str(project),
            "feature_id": str(feature), "snapshot_id": str(snapshot),
            "resolved_base_sha": "a" * 40, "head_sha": "b" * 40,
            "effective_tree_hash": "c" * 64, "diff_hash": "d" * 64,
            "capture_scope": {"kind": "worktree", "selected_untracked": []},
            "privacy_policy_version": "local-v1",
            "source_refs": [{
                "source_ref_id": str(ref), "relative_path": "example.ts",
                "blob_hash": sha,
            }, {
                "source_ref_id": str(batch_ref), "relative_path": "batch.ts",
                "blob_hash": sha,
            }],
        }, 201)
        before = await client.get("/v1/sync/bootstrap", headers=headers(account, second))
        assert before.status_code == 200, before.text
        assert before.json()["entities"]["source_excerpts"] == []
        assert content not in before.text
        watermark = before.json()["high_watermark"]
        base = {
            "excerpt_id": str(uuid.uuid4()), "source_ref_id": str(ref),
            "snapshot_id": str(snapshot), "start_line": 1, "end_line": 1,
            "content_text": content, "content_hash": sha,
        }
        tampered = await post("/v1/source-excerpts", {
            **base, "operation_id": str(uuid.uuid4()), "content_hash": "0" * 64,
        }, 422)
        approved = await post("/v1/source-excerpts", {
            **base, "operation_id": str(uuid.uuid4()),
        }, 201)
        conflict = await post("/v1/source-excerpts", {
            **base, "operation_id": str(uuid.uuid4()),
            "excerpt_id": str(uuid.uuid4()),
        }, 409)
        batch = await post("/v1/sync/operations", {"operations": [{
            "method": "POST", "path": "/v1/source-excerpts",
            "payload": {
                **base, "operation_id": str(uuid.uuid4()),
                "source_ref_id": str(batch_ref), "excerpt_id": str(uuid.uuid4()),
            },
        }]}, 200)
        after = await client.get("/v1/sync/bootstrap", headers=headers(account, second))
        assert after.status_code == 200, after.text
        excerpts = after.json()["entities"]["source_excerpts"]
        outsider_view = await client.get(
            "/v1/sync/bootstrap", headers=headers(other, outsider),
        )
        assert outsider_view.status_code == 200, outsider_view.text
        changes = await client.get(
            f"/v1/sync/changes?after={watermark}", headers=headers(account, second),
        )
        assert changes.status_code == 200, changes.text
        return {
            "default_metadata_only": content not in before.text,
            "tampered_hash_rejected": tampered.json().get("code") == "UNSAFE_SOURCE_EXCERPT",
            "approved_excerpt_written": approved.json().get("source_ref_id") == str(ref),
            "immutable_approval": conflict.json().get("code") == "SOURCE_EXCERPT_MISMATCH",
            "batch_outbox_route": batch.json()["results"][0]["status"] == 201,
            "second_device_bootstrap": len(excerpts) == 2
                and all(item["content_text"] == content
                        and item["blob_hash"] == sha for item in excerpts),
            "other_account_isolated": outsider_view.json()["entities"]["source_excerpts"] == [],
            "incremental_change": any(
                row["entity_type"] == "source_excerpt"
                and row["payload"]["content_text"] == content
                for row in changes.json()["changes"]
            ),
        }


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    database = "growth_excerpt_" + uuid.uuid4().hex[:12]
    dsn = make_conninfo(admin_dsn, dbname=database)
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    try:
        applied = apply_migrations(dsn)
        checks = asyncio.run(exercise(dsn))
        assert checks and all(checks.values()), checks
        report = {
            "scenario": "approved source excerpt across devices",
            "environment": "isolated local PostgreSQL",
            "migration_015_applied": "015_source_excerpts" in applied,
            "checks": checks,
            "verdict": "PASS",
        }
        out = ROOT / "acceptance" / "runs" / "m7-source-excerpt-20260926-v1" / "report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


if __name__ == "__main__":
    main()
