"""Replay note projection failure, recovery, revision and deletion through the worker."""

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
from m1_cloud_smoke import ISSUER, now, save_json, token
from m2_cloud_replay import LOCAL_RUN
from m2_cloud_replay import exercise as seed_m2
from psycopg import sql

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect
from ahadiff.growth.cloud.memory import (
    ReMeAdapter,
    bootstrap_note_jobs,
    current_notes_for_paths,
    reme_note_path,
    run_once,
)

ROOT = Path(__file__).resolve().parents[2]


async def exercise(run_dir: Path, dsn: str, local: dict[str, Any]) -> None:
    seed_dir = run_dir / "seed-m2"
    (seed_dir / "artifacts").mkdir(parents=True)
    await seed_m2(seed_dir, dsn, local)
    chain = json.loads((seed_dir / "chain.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(chain["account_id"])
    note_id = uuid.UUID(chain["note_id"])
    path = reme_note_path(account_id, note_id)
    with connect(dsn) as conn:
        device = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s ORDER BY last_seen LIMIT 1",
            (account_id,),
        ).fetchone()
        assert device is not None
        device_id = device["device_id"]
        # This replay exercises note ordering; attempt projection has its own live check.
        conn.execute(
            "UPDATE growth_memory_jobs SET next_retry_at=now()+interval '1 day' "
            "WHERE account_id=%s AND source_type='attempt' AND state='pending'",
            (account_id,),
        )
    checks: dict[str, bool] = {}
    steps: list[dict[str, Any]] = []
    files: dict[str, dict[str, Any]] = {}
    fault = {"write_unavailable": True}
    calls: list[dict[str, Any]] = []

    def record(label: str, actual: Any, passed: bool) -> None:
        step = {"step": label, "started_at": now(), "finished_at": now(),
                "device_id": str(device_id), "actual": actual,
                "verdict": "PASS" if passed else "FAIL"}
        steps.append(step)
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(step, ensure_ascii=False, default=str) + "\n")

    def fake_reme(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"path": request.url.path, "body": body})
        if request.url.path == "/write":
            if fault["write_unavailable"]:
                return httpx.Response(503, json={"error": "injected outage"})
            files[body["path"]] = body
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/delete":
            files.pop(body["path"], None)
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/search":
            return httpx.Response(200, json={"metadata": {
                "results": [{"path": item} for item in files],
            }})
        return httpx.Response(404)

    initial_count = bootstrap_note_jobs(dsn)
    checks["existing_note_has_job"] = initial_count == 1
    record("bootstrap_existing_note", {"count": initial_count},
           checks["existing_note_has_job"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(fake_reme), base_url="http://reme.test"
    ) as reme_http:
        adapter = ReMeAdapter(reme_http)
        outcome = await run_once(dsn, adapter)
        with connect(dsn) as conn:
            failed = conn.execute(
                "SELECT state, attempt_count, last_error FROM growth_memory_jobs "
                "WHERE account_id=%s AND source_id=%s AND source_version=1",
                (account_id, note_id),
            ).fetchone()
            saved = conn.execute(
                "SELECT content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
                (account_id, note_id),
            ).fetchone()
        checks["outage_preserves_note_and_job"] = (
            outcome == "failed" and failed is not None and failed["state"] == "failed"
            and failed["attempt_count"] == 1 and saved is not None
            and saved["content_text"] == local["learning"]["notes"][0]["content_text"]
            and not files
        )
        record("reme_outage", {"job": failed, "note_saved": saved is not None},
               checks["outage_preserves_note_and_job"])
        assert saved is not None
        saved_text = str(saved["content_text"])
        fault["write_unavailable"] = False
        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET next_retry_at=now() "
                "WHERE account_id=%s AND source_id=%s", (account_id, note_id),
            )
        outcome = await run_once(dsn, adapter)
        with connect(dsn) as conn:
            projected = conn.execute(
                "SELECT state, attempt_count FROM growth_memory_jobs "
                "WHERE account_id=%s AND source_id=%s AND source_version=1",
                (account_id, note_id),
            ).fetchone()
        checks["worker_recovers_same_version"] = (
            outcome == "succeeded" and projected is not None
            and projected["state"] == "succeeded" and projected["attempt_count"] == 2
            and str(files[path]["content"]) == saved_text
        )
        record("worker_recovers", {"job": projected, "reme_path": path},
               checks["worker_recovers_same_version"])
        search_paths = await adapter.search_paths("工程笔记")
        visible = current_notes_for_paths(dsn, account_id, search_paths)
        foreign = current_notes_for_paths(dsn, uuid.uuid4(), search_paths)
        checks["search_rechecks_account_and_version"] = (
            len(visible) == 1 and visible[0]["note_id"] == str(note_id) and not foreign
        )
        record("source_checked_search", {"paths": search_paths, "visible": visible},
               checks["search_rechecks_account_and_version"])

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        app = create_app(CloudSettings(
            dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem)
        ))
        headers = {"Authorization": f"Bearer {token(private_key, account_id)}",
                   "X-Device-Id": str(device_id)}
        revised_text = saved_text + "\n记忆版本二。"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
        ) as cloud_http:
            revised = await cloud_http.put(
                f"/v1/notes/{note_id}/revisions", headers=headers,
                json={"operation_id": str(uuid.uuid4()), "base_revision": 1,
                      "content_text": revised_text},
            )
            checks["revision_is_business_write"] = revised.status_code == 200
            record("revise_note", revised.json(), checks["revision_is_business_write"])
            with connect(dsn) as conn:
                conn.execute(
                    "UPDATE growth_memory_jobs SET state='running', "
                    "lease_until=now()-interval '1 second' "
                    "WHERE account_id=%s AND source_id=%s AND source_version=2",
                    (account_id, note_id),
                )
            outcome = await run_once(dsn, adapter)
            checks["expired_lease_recovers"] = (
                outcome == "succeeded" and str(files[path]["content"]) == revised_text
                and int(files[path]["metadata"]["growth_source_version"]) == 2
            )
            record("recover_expired_lease", {"outcome": outcome,
                                            "memory": files.get(path)},
                   checks["expired_lease_recovers"])
            deleted = await cloud_http.request(
                "DELETE", f"/v1/notes/{note_id}", headers=headers,
                json={"operation_id": str(uuid.uuid4()), "base_revision": 2},
            )
            checks["delete_is_business_write"] = deleted.status_code == 200
            record("delete_note", deleted.json(), checks["delete_is_business_write"])
        stale_visible = current_notes_for_paths(dsn, account_id, [path])
        checks["delete_filters_before_worker"] = stale_visible == []
        record("filter_deleted_before_projection", {"visible": stale_visible},
               checks["delete_filters_before_worker"])
        outcome = await run_once(dsn, adapter)
        with connect(dsn) as conn:
            current = conn.execute(
                "SELECT source_version, deleted_at FROM growth_memory_current "
                "WHERE account_id=%s AND source_id=%s", (account_id, note_id),
            ).fetchone()
            jobs = conn.execute(
                "SELECT source_version, action, state, attempt_count, projection_key "
                "FROM growth_memory_jobs WHERE account_id=%s AND source_id=%s "
                "ORDER BY source_version", (account_id, note_id),
            ).fetchall()
        checks["delete_removes_derived_memory"] = (
            outcome == "succeeded" and path not in files and current is not None
            and current["source_version"] == 3 and current["deleted_at"] is not None
            and current_notes_for_paths(dsn, account_id, [path]) == []
        )
        record("remove_derived_memory", {"outcome": outcome, "current": current},
               checks["delete_removes_derived_memory"])
        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET state='pending', next_retry_at=now() "
                "WHERE account_id=%s AND source_id=%s AND source_version=1",
                (account_id, note_id),
            )
        outcome = await run_once(dsn, adapter)
        checks["old_job_cannot_resurrect"] = outcome == "skipped" and path not in files
        record("reject_old_projection", {"outcome": outcome},
               checks["old_job_cannot_resurrect"])

    save_json(run_dir / "artifacts" / "memory.json", {
        "calls": calls, "jobs": jobs, "current": current,
        "final_reme_paths": list(files),
    })
    save_json(run_dir / "chain.json", {
        "seed": "seed-m2", "account_id": str(account_id), "note_id": str(note_id),
        "reme_path": path, "source_versions": [1, 2, 3],
    })
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in calls),
        encoding="utf-8",
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    save_json(run_dir / "result.json", {"status": status, "checks": checks})
    save_json(run_dir / "manifest.json", {
        "run_id": run_dir.name, "stage": "M4", "case_id": "S03-memory-replay",
        "mode": "replay", "check_status": status, "stage_status": "BLOCKED",
        "stage_blockers": ["ReMe 真实服务尚未运行", "复习调度尚未接入", "live 模型未接入"],
        "started_at": steps[0]["started_at"], "finished_at": now(),
        "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
    })
    (run_dir / "report.md").write_text(
        "# M4 记忆投影故障回放\n\n"
        "M2 的用户笔记先进入与业务保存同事务的投影队列。模拟 ReMe 503 后，"
        "笔记原文和待重试任务仍在 PostgreSQL；恢复后同一来源版本写入，"
        "检索结果经账号、删除状态和版本回源。revision 2 的过期 worker 租约恢复后更新投影；"
        "删除版本先阻止检索，再删除派生文件；旧版本任务重放不能复活内容。\n\n"
        f"检查：**{status}**。ReMe 端点由验收模拟器实现，未运行真实 ReMe，"
        "此结果不能计入 S01/S03 的 live 验收。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(f"M4 memory checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m4-memory-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    database_name = "growth_m4_" + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        expected = ["001_initial", "002_learning", "003_sync", "004_memory", "005_review",
                    "006_modules", "007_module_tasks", "008_chat_topics",
                    "009_topic_maintenance", "010_development_events",
                    "011_exports", "012_task_drafts", "013_chat_model_receipts", "014_review_answer_text"]
        if apply_migrations(dsn) != expected:
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
                f"# M4 记忆回放失败\n\n`{type(exc).__name__}: {exc}`。此运行保持 FAIL。\n",
                encoding="utf-8",
            )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
