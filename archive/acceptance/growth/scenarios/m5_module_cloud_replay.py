"""Replay S03 module correction and lock against isolated real PostgreSQL."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
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

A1 = "TTL 到了以后会有后台线程自动删除缓存，然后自动去查询新的价格。"
A2 = ("这里没有看到定时任务。每次 get 会比较当前时间与 expiresAt，"
      "到期以后这次调用才执行 load 并覆盖缓存。如果没有下一次访问，就不会主动刷新。")
N1 = ("订单计价现在先经过 PriceCache，再按需要调用 PricingGateway。"
      "这个例子的 TTL 是访问时检查的：到期不会自动刷新，下一次 get 才重新加载。"
      "我不能只看到 expiresAt 就推断项目里存在定时清理线程。"
      "另外，这个 HashMap 实现是否适合并发使用，需要单独检查。")


def _snapshot_body(connection: sqlite3.Connection, chain: dict[str, Any],
                   snapshot_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()
    assert row is not None
    refs = connection.execute(
        "SELECT source_ref_id, relative_path, blob_hash FROM source_refs "
        "WHERE snapshot_id=? AND deleted=0 ORDER BY relative_path", (snapshot_id,)
    ).fetchall()
    return {
        "operation_id": str(uuid.uuid4()), "project_id": chain["project_id"],
        "feature_id": chain["feature_id"], "snapshot_id": snapshot_id,
        "analysis_id": str(uuid.uuid4()),
        "resolved_base_sha": row["resolved_base_sha"], "head_sha": row["head_sha"],
        "effective_tree_hash": row["effective_tree_hash"],
        "diff_hash": row["diff_hash"],
        "capture_scope": json.loads(row["capture_scope"]),
        "privacy_policy_version": row["privacy_policy_version"],
        "input_fingerprint": hashlib.sha256(f"m5:{snapshot_id}".encode()).hexdigest(),
        "mode": "dry_run", "status": "succeeded", "generation_origin": "replay",
        "source_refs": [{key: ref[key] for key in
                         ("source_ref_id", "relative_path", "blob_hash")}
                        for ref in refs],
    }


async def exercise(run_dir: Path, dsn: str, applied: list[str],
                   database_name: str) -> None:
    artifact = run_dir / "artifacts"
    chain = json.loads((run_dir / "chain.json").read_text(encoding="utf-8"))
    first_flow = json.loads((artifact / "flow.json").read_text(encoding="utf-8"))
    second_flow = json.loads((artifact / "flow-after-rebase.json").read_text(encoding="utf-8"))
    with sqlite3.connect(run_dir / "local.sqlite") as db:
        db.row_factory = sqlite3.Row
        first = _snapshot_body(db, chain, chain["snapshot_id"])
        second = _snapshot_body(db, chain, chain["newer_snapshot_id"])
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER,
                                                     public_key_pem=public_pem)))
    account, other, device, other_device = (uuid.uuid4() for _ in range(4))
    module_id = uuid.uuid4()
    opportunity_id, proposal_id, topic_id, task_id, note_id = (
        uuid.uuid4() for _ in range(5)
    )
    headers = {"Authorization": f"Bearer {token(private_key, account)}",
               "X-Device-Id": str(device)}
    other_headers = {"Authorization": f"Bearer {token(private_key, other)}",
                     "X-Device-Id": str(other_device)}
    steps: list[dict[str, Any]] = []
    checks: dict[str, bool] = {}

    async def call(client: httpx.AsyncClient, method: str, path: str, expected: int,
                   label: str, *, body: dict[str, Any] | None = None,
                   use_other: bool = False) -> dict[str, Any]:
        response = await client.request(
            method, path, json=body, headers=other_headers if use_other else headers,
        )
        actual = response.json()
        entry = {"step": label, "time": datetime.now(UTC).isoformat(),
                 "expected_status": expected, "actual_status": response.status_code,
                 "operation_id": body.get("operation_id") if body else None,
                 "actual": actual,
                 "verdict": "PASS" if response.status_code == expected else "FAIL"}
        steps.append(entry)
        with (run_dir / "cloud-steps.jsonl").open(
            "a", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        if response.status_code != expected:
            raise AssertionError(f"{label}: {response.status_code} != {expected}: {actual}")
        return actual

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        for owner, device_id in ((account, device), (other, other_device)):
            response = await client.post(
                "/v1/devices", json={"operation_id": str(uuid.uuid4()),
                                     "device_id": str(device_id)},
                headers={"Authorization": f"Bearer {token(private_key, owner)}"},
            )
            assert response.status_code == 201, response.text
        await call(client, "POST", "/v1/projects", 201, "create_project",
                   body={"operation_id": str(uuid.uuid4()),
                         "project_id": chain["project_id"], "name": "订单计价项目",
                         "sync_policy": {"cloud_allowed": True,
                                         "model_allowed": False}})
        await call(client, "POST", "/v1/features", 201, "create_feature",
                   body={"operation_id": str(uuid.uuid4()),
                         "feature_id": chain["feature_id"],
                         "project_id": chain["project_id"],
                         "label": "价格缓存", "base_ref": "main",
                         "start_base_sha": chain["base_sha"]})
        ttl_refs = [ref["source_ref_id"] for ref in first["source_refs"]
                    if ref["relative_path"].endswith(("PriceCache.java",
                                                       "OrderService.java"))]
        assert len(ttl_refs) == 2
        first["opportunities"] = [{
            "opportunity_id": str(opportunity_id), "title": "理解价格缓存的惰性过期",
            "reason": "OrderService 新增 PriceCache，get 在访问时检查 expiresAt",
            "learning_goal": "解释价格来源与下一次访问何时重新查询",
            "source_refs": ttl_refs, "estimated_minutes": 5,
            "topic_proposals": [{"proposal_id": str(proposal_id),
                                 "title": "订单计价"}],
            "uncertainties": ["PricingGateway 的运行时实现不在生产源码中"],
        }]
        await call(client, "POST", "/v1/analysis-results", 201,
                   "save_first_snapshot", body=first)
        members = sorted({node["file_path"] for node in first_flow["nodes"]})
        mapped_body = {
            "operation_id": str(uuid.uuid4()), "module_id": str(module_id),
            "project_id": chain["project_id"], "snapshot_id": chain["snapshot_id"],
            "index_revision": first_flow["index_revision"],
            "effective_tree_hash": first_flow["effective_tree_hash"],
            "suggested_name": "订单模块候选", "member_paths": members,
            "flow": first_flow,
        }
        mismatch = await call(client, "POST", "/v1/modules", 409,
                              "reject_mismatched_snapshot_hash",
                              body={**mapped_body, "operation_id": str(uuid.uuid4()),
                                    "effective_tree_hash": "0" * 64,
                                    "flow": {**first_flow,
                                             "effective_tree_hash": "0" * 64}})
        checks["mismatched_index_rejected"] = mismatch["code"] == "STALE_INDEX"
        mapped = await call(client, "POST", "/v1/modules", 201,
                            "map_module", body=mapped_body)
        repeat = await call(client, "POST", "/v1/modules", 201,
                            "retry_same_map_operation", body=mapped_body)
        checks["map_idempotent"] = mapped == repeat and mapped["revision"] == 1
        edit = await call(client, "PATCH", f"/v1/modules/{module_id}", 200,
                          "rename_and_lock",
                          body={"operation_id": str(uuid.uuid4()),
                                "base_revision": 1, "name": "订单计价",
                                "locked": True, "member_paths": members})
        checks["user_lock_saved"] = edit["revision"] == 2 and edit["locked"]
        matching = await call(client, "GET", f"/v1/modules/{module_id}/opportunities",
                              200, "find_module_opportunity")
        checks["ttl_opportunity_grounded_in_module"] = (
            len(matching["opportunities"]) == 1
            and matching["opportunities"][0]["opportunity_id"] == str(opportunity_id)
            and set(matching["opportunities"][0]["source_refs"]) == set(ttl_refs)
        )
        await call(client, "POST", f"/v1/topic-proposals/{proposal_id}/decision",
                   200, "confirm_module_topic",
                   body={"operation_id": str(uuid.uuid4()), "decision": "confirm",
                         "topic_id": str(topic_id)})
        task_body = {"operation_id": str(uuid.uuid4()), "task_id": str(task_id),
                     "opportunity_id": str(opportunity_id), "topic_id": str(topic_id),
                     "question": "价格到期后会自动刷新吗？下一次查询在哪里发生？",
                     "followups": ["没有再次访问时会发生什么？"],
                     "module_id": str(module_id),
                     "module_index_revision": first_flow["index_revision"]}
        stale_task = await call(client, "POST", "/v1/tasks", 409,
                                "reject_task_with_wrong_index",
                                body={**task_body, "operation_id": str(uuid.uuid4()),
                                      "module_index_revision": "0" * 64})
        checks["task_rejects_stale_index"] = stale_task["code"] == "STALE_MODULE"
        created_task = await call(client, "POST", "/v1/tasks", 201,
                                  "create_module_card", body=task_body)
        checks["module_card_created"] = created_task["task_id"] == str(task_id)
        first_attempt_id = uuid.uuid4()
        second_attempt_id = uuid.uuid4()
        for number, attempt_id, parent, answer, summary in (
            (1, first_attempt_id, None, A1,
             "代码没有后台刷新线程；TTL 由下一次 get 检查。"),
            (2, second_attempt_id, first_attempt_id, A2,
             "修正回答准确描述访问时过期与惰性加载。"),
        ):
            await call(client, "POST", f"/v1/tasks/{task_id}/attempts", 202,
                       f"save_answer_{number}",
                       body={"operation_id": str(uuid.uuid4()),
                             "attempt_id": str(attempt_id),
                             "parent_attempt_id": str(parent) if parent else None,
                             "answer_text": answer, "hint_level": 0,
                             "actor_origin": "simulated_user"})
            await call(client, "POST", f"/v1/attempts/{attempt_id}/feedback", 200,
                       f"save_replay_feedback_{number}",
                       body={"operation_id": str(uuid.uuid4()),
                             "feedback_origin": "replay",
                             "feedback": {"summary": summary,
                                          "source_refs": ttl_refs}})
        await call(client, "PATCH", f"/v1/tasks/{task_id}/progress", 200,
                   "complete_module_card",
                   body={"operation_id": str(uuid.uuid4()),
                         "base_revision": 3, "progress": "completed"})
        saved_note = await call(client, "POST", "/v1/notes", 201,
                                "save_user_note",
                                body={"operation_id": str(uuid.uuid4()),
                                      "note_id": str(note_id), "task_id": str(task_id),
                                      "topic_id": str(topic_id), "content_text": N1})
        checks["module_note_saved"] = saved_note["note_id"] == str(note_id)
        cross = await call(client, "GET", f"/v1/projects/{chain['project_id']}/modules",
                           404, "cross_account_read_rejected", use_other=True)
        checks["account_isolation"] = cross["code"] == "PROJECT_NOT_FOUND"
        await call(client, "POST", "/v1/analysis-results", 201,
                   "save_rebased_snapshot", body=second)
        refreshed_body = {**mapped_body,
                          "operation_id": str(uuid.uuid4()),
                          "snapshot_id": chain["newer_snapshot_id"],
                          "index_revision": second_flow["index_revision"],
                          "effective_tree_hash": second_flow["effective_tree_hash"],
                          "flow": second_flow, "base_revision": 2}
        refreshed = await call(client, "POST", "/v1/modules", 200,
                               "refresh_locked_module_same_boundary",
                               body=refreshed_body)
        checks["locked_name_kept_on_reindex"] = (
            refreshed["revision"] == 3 and not refreshed["pending_adjustment"]
        )
        stale = await call(client, "PATCH", f"/v1/modules/{module_id}", 409,
                           "reject_stale_edit",
                           body={"operation_id": str(uuid.uuid4()),
                                 "base_revision": 2, "name": "覆盖名称",
                                 "locked": False, "member_paths": members})
        checks["stale_edit_rejected"] = stale["code"] == "REVISION_CONFLICT"
        reduced_members = [path for path in members if not path.endswith("PriceCache.java")]
        kept = {node["symbol"] for node in second_flow["nodes"]
                if node["file_path"] in reduced_members}
        reduced_flow = {**second_flow,
                        "nodes": [node for node in second_flow["nodes"]
                                  if node["symbol"] in kept],
                        "edges": [edge for edge in second_flow["edges"]
                                  if edge["from"] in kept and edge["to"] in kept]}
        pending = await call(client, "POST", "/v1/modules", 200,
                             "propose_changed_boundary",
                             body={**refreshed_body,
                                   "operation_id": str(uuid.uuid4()),
                                   "base_revision": 3,
                                   "member_paths": reduced_members,
                                   "flow": reduced_flow})
        checks["locked_boundary_requires_confirmation"] = (
            pending["revision"] == 4 and pending["pending_adjustment"]
        )
        read = await call(client, "GET",
                          f"/v1/projects/{chain['project_id']}/modules", 200,
                          "read_locked_module")
        module = read["modules"][0]
        checks["original_lock_survives_pending"] = (
            module["name"] == "订单计价" and module["locked"]
            and sorted(module["member_paths"]) == members
            and module["index_revision"].strip() == second_flow["index_revision"]
            and module["pending_adjustment"]["member_paths"] == reduced_members
        )
        bootstrap = await call(client, "GET", "/v1/sync/bootstrap", 200,
                               "module_in_bootstrap")
        checks["module_sync_visible"] = (
            bootstrap["entities"]["modules"][0]["module_id"] == str(module_id)
        )
        task = next(row for row in bootstrap["entities"]["tasks"]
                    if row["task_id"] == str(task_id))
        answers = sorted(row["answer_text"] for row in
                         bootstrap["entities"]["attempts"])
        note = next(row for row in bootstrap["entities"]["notes"]
                    if row["note_id"] == str(note_id))
        checks["old_card_and_user_text_survive_rebase"] = (
            task["module_id"] == str(module_id)
            and task["module_index_revision"].strip() == first_flow["index_revision"]
            and answers == sorted([A1, A2]) and note["content_text"] == N1
            and note["author"] == "user"
        )
        accepted = await call(client, "PATCH", f"/v1/modules/{module_id}", 200,
                              "accept_pending_boundary",
                              body={"operation_id": str(uuid.uuid4()),
                                    "base_revision": 4, "name": "订单计价",
                                    "locked": True, "member_paths": reduced_members,
                                    "resolve_pending": True})
        checks["explicit_adjustment_applied"] = (
            accepted["revision"] == 5 and not accepted["pending_adjustment"]
        )
        final = await call(client, "GET",
                           f"/v1/projects/{chain['project_id']}/modules", 200,
                           "read_confirmed_boundary")
        checks["confirmed_boundary_visible"] = (
            final["modules"][0]["member_paths"] == reduced_members
            and final["modules"][0]["pending_adjustment"] is None
            and final["modules"][0]["name"] == "订单计价"
        )
    with connect(dsn) as conn:
        task_row = conn.execute(
            "SELECT module_id, module_index_revision, source_refs, progress "
            "FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, task_id),
        ).fetchone()
        attempts = conn.execute(
            "SELECT attempt_id, parent_attempt_id, answer_text, feedback_origin, "
            "feedback FROM growth_learning_attempts WHERE account_id=%s "
            "AND task_id=%s ORDER BY created_at, attempt_id",
            (account, task_id),
        ).fetchall()
        note_row = conn.execute(
            "SELECT note_id, author, content_text, source_refs, revision "
            "FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, note_id),
        ).fetchone()
        jobs = conn.execute(
            "SELECT source_version, state, action FROM growth_memory_jobs "
            "WHERE account_id=%s AND source_id=%s",
            (account, note_id),
        ).fetchall()
    assert task_row is not None and note_row is not None
    checks["module_card_source_chain_persisted"] = (
        task_row["module_id"] == module_id
        and task_row["module_index_revision"].strip() == first_flow["index_revision"]
        and set(task_row["source_refs"]) == set(ttl_refs)
        and task_row["progress"] == "completed"
        and {row["answer_text"] for row in attempts} == {A1, A2}
        and all(row["feedback_origin"] == "replay" and row["feedback"]
                for row in attempts)
        and note_row["author"] == "user" and note_row["content_text"] == N1
        and set(note_row["source_refs"]) == set(ttl_refs)
    )
    checks["note_projection_queued_without_reme"] = (
        len(jobs) == 1 and jobs[0]["source_version"] == 1
        and jobs[0]["state"] == "pending" and jobs[0]["action"] == "upsert"
    )
    (artifact / "module-card-rows.json").write_text(
        json.dumps({"task": task_row, "attempts": attempts, "note": note_row,
                    "memory_jobs": jobs}, ensure_ascii=False, indent=2,
                   default=str) + "\n", encoding="utf-8",
    )
    result = "PASS" if all(checks.values()) else "FAIL"
    (run_dir / "cloud-result.json").write_text(
        json.dumps({"status": result, "checks": checks, "migrations": applied,
                    "database_name": database_name,
                    "account_id": str(account), "module_id": str(module_id)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cloud_check_status"] = result
    manifest["check_status"] = result if manifest["check_status"] == "PASS" else "FAIL"
    manifest["database_name"] = database_name
    manifest["stage_blockers"] = [
        "S03 的真实 ReMe 停止、恢复及检索回源尚未执行",
        "模型 live 语义、桌面 UI 与 Windows/macOS 双平台未验收",
    ]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    with (run_dir / "report.md").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "\n## 云端模块回放\n\n"
            f"PostgreSQL 独立库 `{database_name}`；迁移 `001`–`{len(applied):03d}`。"
            "模块来源到 TTL 卡片、两次模拟回答、回放反馈、用户笔记与待投影任务"
            "已贯通；用户命名/锁定、同边界重建索引、边界变化待确认、冲突、跨账号隔离"
            f"及同步快照检查：**{result}**。此结果仍是局部 replay，不是 S03 live PASS。\n"
        )
    if result != "PASS":
        raise AssertionError(f"M5 cloud checks failed: {checks}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：m5_module_cloud_replay.py <M5 CodeGraph run directory>")
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    run_dir = Path(sys.argv[1]).resolve()
    if (run_dir / "cloud-steps.jsonl").exists() or (run_dir / "cloud-result.json").exists():
        raise ValueError("云端回放只允许在未使用的新 M5 run 上执行")
    database_name = "growth_m5_" + uuid.uuid4().hex[:12]
    try:
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
            raise AssertionError(f"M5 迁移不完整：{applied}")
        asyncio.run(exercise(run_dir, dsn, applied, database_name))
    except Exception as exc:
        if not (run_dir / "cloud-result.json").exists():
            (run_dir / "cloud-result.json").write_text(
                json.dumps({"status": "FAIL", "database_name": database_name,
                            "error": f"{type(exc).__name__}: {exc}"},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["cloud_check_status"] = "FAIL"
        manifest["check_status"] = "FAIL"
        manifest["stage_status"] = "BLOCKED"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        with (run_dir / "report.md").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"\n云端回放失败：`{type(exc).__name__}: {exc}`。\n")
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
