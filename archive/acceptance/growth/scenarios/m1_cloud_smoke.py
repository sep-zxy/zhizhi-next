"""Exercise the M1 metadata boundary against an isolated real PostgreSQL database."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect

ROOT = Path(__file__).resolve().parents[2]
LOCAL_RUN = "m1-20260925T060054Z-736aa25f"
ISSUER = "https://growth-test.invalid/auth/v1"


def now() -> str:
    return datetime.now(UTC).isoformat()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def token(private_key: rsa.RSAPrivateKey, account_id: uuid.UUID) -> str:
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": "authenticated",
            "sub": str(account_id),
            "role": "authenticated",
            "exp": datetime.now(UTC) + timedelta(minutes=10),
        },
        private_key,
        algorithm="RS256",
    )


async def exercise(run_dir: Path, dsn: str, chain: dict[str, Any]) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    settings = CloudSettings(dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem))
    account_a, account_b = uuid.uuid4(), uuid.uuid4()
    device_a, device_b = uuid.uuid4(), uuid.uuid4()
    trace_id = chain["events"][0]["trace_id"]
    steps: list[dict[str, Any]] = []
    checks: dict[str, bool] = {}

    async def post(
        client: httpx.AsyncClient,
        path: str,
        account: uuid.UUID,
        device: uuid.UUID | None,
        body: dict[str, Any],
        expected: int,
        label: str,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token(private_key, account)}", "X-Trace-Id": trace_id}
        if device is not None:
            headers["X-Device-Id"] = str(device)
        started = now()
        response = await client.post(path, json=body, headers=headers)
        steps.append(
            {
                "step": label,
                "started_at": started,
                "finished_at": now(),
                "device_id": str(device) if device else None,
                "trace_id": trace_id,
                "operation_id": body.get("operation_id"),
                "expected_status": expected,
                "actual_status": response.status_code,
                "actual": response.json(),
                "verdict": "PASS" if response.status_code == expected else "FAIL",
            }
        )
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as record:
            record.write(json.dumps(steps[-1], ensure_ascii=False) + "\n")
        if response.status_code != expected:
            raise AssertionError(
                f"{label}: expected {expected}, got {response.status_code}: {response.text}"
            )
        return response

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://growth.test"
    ) as client:
        await post(
            client,
            "/v1/devices",
            account_a,
            None,
            {"operation_id": str(uuid.uuid4()), "device_id": str(device_a)},
            201,
            "register_a",
        )
        await post(
            client,
            "/v1/devices",
            account_b,
            None,
            {"operation_id": str(uuid.uuid4()), "device_id": str(device_b)},
            201,
            "register_b",
        )
        project = chain["project"]
        project_body = {
            "operation_id": str(uuid.uuid4()),
            "project_id": project["project_id"],
            "name": project["name"],
            "sync_policy": {"cloud_allowed": True, "model_allowed": False},
        }
        first = await post(
            client, "/v1/projects", account_a, device_a, project_body, 201, "create_project"
        )
        repeated = await post(
            client, "/v1/projects", account_a, device_a, project_body, 201, "retry_same_operation"
        )
        checks["idempotent_fixed_response"] = first.json() == repeated.json()
        altered = {**project_body, "name": "另一项目"}
        reuse = await post(
            client, "/v1/projects", account_a, device_a, altered, 409, "reject_changed_operation"
        )
        checks["changed_operation_rejected"] = reuse.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
        feature = chain["feature"]
        feature_body = {
            "operation_id": str(uuid.uuid4()),
            "feature_id": feature["feature_id"],
            "project_id": project["project_id"],
            "label": feature["label"],
            "base_ref": feature["base_ref"],
            "start_base_sha": feature["start_base_sha"],
        }
        await post(client, "/v1/features", account_a, device_a, feature_body, 201, "create_feature")
        other = await post(
            client,
            "/v1/features",
            account_b,
            device_b,
            {**feature_body, "operation_id": str(uuid.uuid4())},
            404,
            "cross_account_project",
        )
        checks["account_boundary"] = other.json()["code"] == "PROJECT_NOT_FOUND"
        snapshot = chain["snapshots"][0]
        analysis = chain["analysis_runs"][0]
        analysis_body: dict[str, Any] = {
            "operation_id": str(uuid.uuid4()),
            "project_id": project["project_id"],
            "feature_id": feature["feature_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "analysis_id": analysis["analysis_id"],
            "resolved_base_sha": snapshot["resolved_base_sha"],
            "head_sha": snapshot["head_sha"],
            "effective_tree_hash": snapshot["effective_tree_hash"],
            "diff_hash": snapshot["diff_hash"],
            "capture_scope": json.loads(snapshot["capture_scope"]),
            "privacy_policy_version": snapshot["privacy_policy_version"],
            "input_fingerprint": analysis["input_fingerprint"],
            "mode": analysis["mode"],
            "status": analysis["status"],
            "upstream_run_id": analysis["upstream_run_id"],
            "source_refs": [
                {key: row[key] for key in ("source_ref_id", "relative_path", "blob_hash")}
                for row in chain["source_refs"]
            ],
        }
        saved = await post(
            client,
            "/v1/analysis-results",
            account_a,
            device_a,
            analysis_body,
            201,
            "save_analysis_metadata",
        )
        checks["source_refs_linked"] = saved.json()["snapshot_id"] == snapshot["snapshot_id"]
        extra = await post(
            client,
            "/v1/analysis-results",
            account_a,
            device_a,
            {**analysis_body, "raw_code": "secret"},
            422,
            "reject_raw_code",
        )
        checks["raw_code_rejected"] = extra.json()["code"] == "INVALID_REQUEST"
        absolute = {
            **analysis_body,
            "source_refs": [
                {**analysis_body["source_refs"][0], "relative_path": "C:\\private\\code.py"}
            ],
        }
        invalid_path = await post(
            client,
            "/v1/analysis-results",
            account_a,
            device_a,
            absolute,
            422,
            "reject_absolute_path",
        )
        checks["absolute_path_rejected"] = invalid_path.json()["code"] == "INVALID_REQUEST"
        scope = {
            **analysis_body,
            "capture_scope": {**analysis_body["capture_scope"], "raw_code": "secret"},
        }
        invalid_scope = await post(
            client, "/v1/analysis-results", account_a, device_a, scope, 422, "reject_scope_extra"
        )
        checks["scope_extra_rejected"] = invalid_scope.json()["code"] == "INVALID_REQUEST"

    # A new ASGI app and fresh database connection represent a cloud process restart.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://growth.test"
    ) as restarted:
        replay = await post(
            restarted,
            "/v1/analysis-results",
            account_a,
            device_a,
            analysis_body,
            201,
            "replay_after_restart",
        )
        checks["restart_keeps_result"] = replay.json() == saved.json()

    with connect(dsn) as conn:
        rows = {
            "project": conn.execute(
                "SELECT project_id, name FROM growth_projects WHERE account_id=%s", (account_a,)
            ).fetchall(),
            "feature": conn.execute(
                "SELECT feature_id, project_id FROM growth_features WHERE account_id=%s",
                (account_a,),
            ).fetchall(),
            "snapshot": conn.execute(
                "SELECT snapshot_id, feature_id, capture_scope "
                "FROM growth_snapshots WHERE account_id=%s",
                (account_a,),
            ).fetchall(),
            "source_refs": conn.execute(
                "SELECT source_ref_id, snapshot_id, relative_path, blob_hash "
                "FROM growth_source_refs WHERE account_id=%s ORDER BY relative_path",
                (account_a,),
            ).fetchall(),
            "analysis": conn.execute(
                "SELECT analysis_id, snapshot_id, upstream_run_id "
                "FROM growth_analysis_runs WHERE account_id=%s",
                (account_a,),
            ).fetchall(),
            "events": conn.execute(
                "SELECT event_id, trace_id, event_type, entity_id "
                "FROM growth_domain_events WHERE account_id=%s ORDER BY occurred_at, event_id",
                (account_a,),
            ).fetchall(),
            "changes": conn.execute(
                "SELECT change_seq, entity_type, entity_id "
                "FROM growth_change_feed WHERE account_id=%s ORDER BY change_seq",
                (account_a,),
            ).fetchall(),
            "operations": conn.execute(
                "SELECT operation_id, status_code FROM growth_sync_operations WHERE account_id=%s",
                (account_a,),
            ).fetchall(),
        }
        b_count_row = conn.execute(
            "SELECT count(*) AS n FROM growth_projects WHERE account_id=%s", (account_b,)
        ).fetchone()
        assert b_count_row is not None
        b_projects = int(b_count_row["n"])
    checks["postgres_ids_match_local"] = (
        str(rows["project"][0]["project_id"]) == project["project_id"]
        and str(rows["feature"][0]["feature_id"]) == feature["feature_id"]
        and str(rows["snapshot"][0]["snapshot_id"]) == snapshot["snapshot_id"]
        and str(rows["analysis"][0]["analysis_id"]) == analysis["analysis_id"]
    )
    checks["postgres_refs_match_local"] = {
        (str(row["source_ref_id"]), row["relative_path"], row["blob_hash"].strip())
        for row in rows["source_refs"]
    } == {
        (row["source_ref_id"], row["relative_path"], row["blob_hash"])
        for row in chain["source_refs"]
    }
    checks["account_b_has_no_project"] = b_projects == 0
    checks["sequence_no_gaps"] = [row["change_seq"] for row in rows["changes"]] == list(
        range(1, len(rows["changes"]) + 1)
    )
    checks["trace_linked"] = all(str(row["trace_id"]) == trace_id for row in rows["events"])
    checks["replay_no_duplicate_changes"] = len(rows["changes"]) == 7
    checks["replay_no_duplicate_operations"] = len(rows["operations"]) == 4
    exported = json.dumps(rows, ensure_ascii=False, default=str)
    checks["no_source_or_absolute_path_in_cloud"] = (
        "def total" not in exported and "C:\\" not in exported and "secret" not in exported
    )
    save_json(run_dir / "artifacts" / "postgres-rows.json", rows)
    save_json(
        run_dir / "chain.json",
        {
            "local_run_id": LOCAL_RUN,
            "account_id": str(account_a),
            "project_id": project["project_id"],
            "feature_id": feature["feature_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "analysis_id": analysis["analysis_id"],
            "source_ref_ids": [row["source_ref_id"] for row in chain["source_refs"]],
            "change_seq": [row["change_seq"] for row in rows["changes"]],
        },
    )
    (run_dir / "steps.jsonl").write_text(
        "".join(json.dumps(step, ensure_ascii=False) + "\n" for step in steps), encoding="utf-8"
    )
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows["events"]),
        encoding="utf-8",
    )
    report = (
        "# M1 云端元数据边界情境\n\n"
        f"本地链路运行 `{LOCAL_RUN}` 的真实 Git 快照、来源和 AhaDiff dry_run 分析 ID "
        "已写入隔离 PostgreSQL。"
        "合成 RSA 令牌经真实鉴权代码验证。重放相同操作返回固定响应；修改同一操作被拒；"
        "另一个账号无法引用该项目；原始源码和本机绝对路径被接口拒绝。"
        "新建应用实例后原记录和 ID 仍在。\n\n"
        f"数据库检查：**{'PASS' if all(checks.values()) else 'FAIL'}**。"
        "此运行没有外部 Supabase 项目、live 模型或 macOS，因此 M1 完整阶段仍为 **BLOCKED**。\n"
    )
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    save_json(
        run_dir / "manifest.json",
        {
            "run_id": run_dir.name,
            "stage": "M1",
            "mode": "dry_run",
            "cloud_validation": "isolated_postgres_and_synthetic_jwt",
            "local_run_id": LOCAL_RUN,
            "started_at": steps[0]["started_at"],
            "finished_at": now(),
            "postgres_version": "17.10",
            "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
            "checks": checks,
            "check_status": "PASS" if all(checks.values()) else "FAIL",
            "stage_status": "BLOCKED",
            "artifact_sha256": {
                "postgres-rows.json": hashlib.sha256(
                    (run_dir / "artifacts" / "postgres-rows.json").read_bytes()
                ).hexdigest()
            },
        },
    )
    save_json(
        run_dir / "result.json",
        {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks},
    )
    if not all(checks.values()):
        raise AssertionError(f"Cloud checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    chain = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m1-cloud-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    run_dir.mkdir(parents=True)
    database_name = "growth_" + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        applied = apply_migrations(dsn)
        expected = ["001_initial", "002_learning", "003_sync", "004_memory", "005_review",
                    "006_modules", "007_module_tasks", "008_chat_topics",
                    "009_topic_maintenance", "010_development_events",
                    "011_exports", "012_task_drafts", "013_chat_model_receipts", "014_review_answer_text"]
        if applied != expected or apply_migrations(dsn):
            raise AssertionError("Schema migration did not apply exactly once")
        asyncio.run(exercise(run_dir, dsn, chain))
    except Exception as exc:
        save_json(
            run_dir / "manifest.json",
            {
                "run_id": run_id,
                "stage": "M1",
                "mode": "dry_run",
                "local_run_id": LOCAL_RUN,
                "database_name": database_name,
                "check_status": "FAIL",
                "stage_status": "BLOCKED",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
        (run_dir / "report.md").write_text(
            "# M1 云端边界运行失败\n\n"
            f"数据库 `{database_name}`，错误：`{type(exc).__name__}: {exc}`。"
            "此运行保持 FAIL，修复后新建 run_id。\n",
            encoding="utf-8",
        )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
