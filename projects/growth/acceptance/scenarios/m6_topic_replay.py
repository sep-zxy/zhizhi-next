"""Replay topic merge/split/history and standalone notes on real PostgreSQL."""

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
from m1_cloud_smoke import ISSUER, token
from psycopg import sql
from starlette.testclient import TestClient

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.sync import GrowthSyncClient, GrowthSyncStore
from ahadiff.serve import ServeState
from ahadiff.serve import create_app as create_desktop_app

ROOT = Path(__file__).resolve().parents[2]
OLD_NOTE = "登录成功说明应用识别了用户身份。"
NEW_NOTE = "识别身份之后仍需检查当前用户是否有权修改资源。"
TEMP_NOTE = "有了 JWT 就不需要资源权限检查。"


async def exercise(run_dir: Path, dsn: str) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    app = create_app(CloudSettings(dsn, AuthVerifier(issuer=ISSUER,
                                                     public_key_pem=public_pem)))
    account, foreign, device, second_device, foreign_device = (
        uuid.uuid4() for _ in range(5)
    )
    old_topic, target, authentication, authorization = (
        uuid.uuid4() for _ in range(4)
    )
    old_note, new_note = uuid.uuid4(), uuid.uuid4()
    temporary_note, export_id = uuid.uuid4(), uuid.uuid4()
    headers = {"Authorization": f"Bearer {token(key, account)}",
               "X-Device-Id": str(device)}
    second_headers = {**headers, "X-Device-Id": str(second_device)}
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
        for owner, identifier in ((account, device), (account, second_device),
                                  (foreign, foreign_device)):
            response = await client.post(
                "/v1/devices", json={"operation_id": str(uuid.uuid4()),
                                     "device_id": str(identifier)},
                headers={"Authorization": f"Bearer {token(key, owner)}"},
            )
            assert response.status_code == 201, response.text
        for topic_id, title in ((old_topic, "登录机制"), (target, "Web 认证与授权")):
            await call(client, "POST", "/v1/topics", 201, f"create_{title}",
                       body={"operation_id": str(uuid.uuid4()),
                             "topic_id": str(topic_id), "title": title})
        listed = await call(client, "GET", "/v1/topics", 200,
                            "list_owned_topics")
        foreign_listed = await call(client, "GET", "/v1/topics", 200,
                                    "foreign_account_lists_no_topics",
                                    use_headers=foreign_headers)
        checks["topic_list_account_scoped"] = (
            {row["topic_id"] for row in listed["topics"]}
            == {str(old_topic), str(target)}
            and foreign_listed["topics"] == []
        )
        for note_id, topic_id, content, label in (
            (old_note, old_topic, OLD_NOTE, "old_user_note"),
            (new_note, target, NEW_NOTE, "new_user_note"),
        ):
            await call(client, "POST", "/v1/notes", 201, label,
                       body={"operation_id": str(uuid.uuid4()),
                             "note_id": str(note_id), "topic_id": str(topic_id),
                             "content_text": content})
        (run_dir / "artifacts").mkdir(exist_ok=True)
        local_ledger = GrowthLocalRepository(run_dir / "artifacts" / "topic-sync.sqlite")
        sync_store = GrowthSyncStore(local_ledger, account, second_device)
        sync_client = GrowthSyncClient(
            sync_store, client, second_headers["Authorization"].removeprefix("Bearer ")
        )
        await sync_client.bootstrap()
        stale = await call(
            client, "POST", f"/v1/topics/{old_topic}/merge", 409,
            "reject_stale_merge",
            body={"operation_id": str(uuid.uuid4()),
                  "target_topic_id": str(target),
                  "source_base_revision": 2, "target_base_revision": 1},
        )
        checks["stale_merge_rejected"] = stale["code"] == "REVISION_CONFLICT"
        merge_body = {"operation_id": str(uuid.uuid4()),
                      "target_topic_id": str(target),
                      "source_base_revision": 1, "target_base_revision": 1}
        merged = await call(client, "POST", f"/v1/topics/{old_topic}/merge", 200,
                            "merge_prior_topic", body=merge_body)
        repeated = await call(client, "POST", f"/v1/topics/{old_topic}/merge", 200,
                              "retry_merge", body=merge_body)
        checks["merge_idempotent"] = merged == repeated
        old_read = await call(client, "GET", f"/v1/topics/{old_topic}", 200,
                              "read_old_alias", use_headers=second_headers)
        checks["old_alias_resolves_to_target"] = (
            old_read["canonical_topic_id"] == str(target)
            and old_read["is_alias"]
            and {row["note_id"] for row in old_read["notes"]}
            == {str(old_note), str(new_note)}
        )
        after_merge = await call(client, "GET", "/v1/sync/bootstrap", 200,
                                 "merge_visible_second_device",
                                 use_headers=second_headers)
        checks["original_text_preserved_after_merge"] = (
            {row["content_text"] for row in after_merge["entities"]["notes"]}
            == {OLD_NOTE, NEW_NOTE}
            and after_merge["entities"]["topic_aliases"][0]["alias_topic_id"]
            == str(old_topic)
        )
        split_body = {
            "operation_id": str(uuid.uuid4()), "base_revision": 2,
            "children": [
                {"topic_id": str(authentication), "title": "身份认证",
                 "note_ids": [str(old_note)]},
                {"topic_id": str(authorization), "title": "资源授权",
                 "note_ids": [str(new_note)]},
            ],
        }
        split = await call(client, "POST", f"/v1/topics/{target}/split", 201,
                           "split_with_explicit_note_links", body=split_body)
        checks["two_children_created"] = (
            split["revision"] == 3 and len(split["children"]) == 2
        )
        auth_read = await call(client, "GET", f"/v1/topics/{authentication}", 200,
                               "read_authentication_child")
        permission_read = await call(client, "GET", f"/v1/topics/{authorization}", 200,
                                     "read_authorization_child")
        checks["explicit_split_links_readable"] = (
            [row["content_text"] for row in auth_read["notes"]] == [OLD_NOTE]
            and [row["content_text"] for row in permission_read["notes"]] == [NEW_NOTE]
            and auth_read["topic"]["parent_topic_id"] == str(target)
        )
        archive = await call(client, "PATCH", f"/v1/topics/{authorization}", 200,
                             "archive_authorization",
                             body={"operation_id": str(uuid.uuid4()),
                                   "base_revision": 1, "title": "资源授权",
                                   "status": "archived"})
        reactivate = await call(client, "PATCH", f"/v1/topics/{authorization}", 200,
                                "reactivate_authorization",
                                body={"operation_id": str(uuid.uuid4()),
                                      "base_revision": 2, "title": "资源授权",
                                      "status": "active"})
        checks["archive_reactivation_same_id"] = (
            archive["revision"] == 2 and reactivate["revision"] == 3
            and reactivate["topic_id"] == str(authorization)
        )
        cycle = await call(client, "POST", f"/v1/topics/{target}/merge", 409,
                           "reject_parent_into_child_cycle",
                           body={"operation_id": str(uuid.uuid4()),
                                 "target_topic_id": str(authorization),
                                 "source_base_revision": 3,
                                 "target_base_revision": 3})
        checks["merge_cycle_rejected"] = cycle["code"] == "TOPIC_CYCLE"
        foreign_read = await call(client, "GET", f"/v1/topics/{target}", 404,
                                  "foreign_account_cannot_read",
                                  use_headers=foreign_headers)
        checks["account_isolation"] = foreign_read["code"] == "TOPIC_NOT_FOUND"
        bootstrap = await call(client, "GET", "/v1/sync/bootstrap", 200,
                               "read_maintenance_history_second_device",
                               use_headers=second_headers)
        checks["history_in_sync_snapshot"] = (
            len(bootstrap["entities"]["topic_moves"]) == 3
            and len(bootstrap["entities"]["topic_links"]) == 2
        )
        await call(client, "POST", "/v1/notes", 201, "create_temporary_note",
                   body={"operation_id": str(uuid.uuid4()),
                         "note_id": str(temporary_note), "topic_id": str(target),
                         "content_text": TEMP_NOTE})
        await call(client, "PUT", f"/v1/notes/{temporary_note}/revisions", 200,
                   "revise_temporary_note",
                   body={"operation_id": str(uuid.uuid4()),
                         "base_revision": 1,
                         "content_text": TEMP_NOTE + "待核实。"})
        conflict = await call(client, "PUT",
                              f"/v1/notes/{temporary_note}/revisions", 409,
                              "retain_stale_temporary_branch",
                              body={"operation_id": str(uuid.uuid4()),
                                    "base_revision": 1,
                                    "content_text": TEMP_NOTE + "另一台设备的原话。"})
        checks["conflict_branch_retained"] = (
            conflict["code"] == "REVISION_CONFLICT"
            and bool(conflict["conflict_id"])
        )
        await call(client, "DELETE", f"/v1/notes/{temporary_note}", 200,
                   "delete_temporary_note",
                   body={"operation_id": str(uuid.uuid4()), "base_revision": 2})
        created_export = await call(client, "POST", "/v1/exports", 201,
                                    "create_reme_independent_export",
                                    body={"operation_id": str(uuid.uuid4()),
                                          "export_id": str(export_id)})
        exported = await call(client, "GET", f"/v1/exports/{export_id}?format=json",
                              200, "download_json_on_second_device",
                              use_headers=second_headers)
        exported_notes = {row["note_id"]: row for row in exported["tables"]["notes"]}
        checks["json_export_preserves_history"] = (
            created_export["export_id"] == str(export_id)
            and exported_notes[str(old_note)]["content_text"] == OLD_NOTE
            and exported_notes[str(new_note)]["content_text"] == NEW_NOTE
            and exported_notes[str(temporary_note)]["deleted_at"] is not None
            and len(exported["tables"]["note_conflicts"]) == 1
            and len(exported["tables"]["topic_aliases"]) == 1
            and len(exported["tables"]["topic_links"]) == 2
        )
        markdown_response = await client.get(
            f"/v1/exports/{export_id}?format=markdown", headers=second_headers,
        )
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"step": "download_markdown", "time":
                                     datetime.now(UTC).isoformat(),
                                     "actual_status": markdown_response.status_code,
                                     "verdict": "PASS" if markdown_response.status_code == 200
                                     else "FAIL"}, ensure_ascii=False) + "\n")
        checks["markdown_export_readable_without_reme"] = (
            markdown_response.status_code == 200
            and OLD_NOTE in markdown_response.text
            and NEW_NOTE in markdown_response.text
            and TEMP_NOTE in markdown_response.text
            and "## 完整结构化历史" in markdown_response.text
        )
        foreign_export = await call(client, "GET", f"/v1/exports/{export_id}",
                                    404, "foreign_account_cannot_export",
                                    use_headers=foreign_headers)
        checks["export_account_isolation"] = (
            foreign_export["code"] == "EXPORT_NOT_FOUND"
        )
        desktop_state = ServeState(
            state_dir=run_dir / "artifacts" / "desktop", token="desktop-token",
        )
        with GrowthLocalRepository(desktop_state.state_dir / "growth.sqlite") as desktop_db:
            GrowthSyncStore(
                desktop_db, account, second_device,
                cloud_origin="http://127.0.0.1:9001",
            )
            desktop_db.connection.execute(
                "UPDATE sync_accounts SET bootstrapped=1 WHERE account_id=?",
                (str(account),),
            )
            desktop_db.connection.commit()
        original_async_client = httpx.AsyncClient

        def local_cloud_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            return original_async_client(
                *args, transport=httpx.ASGITransport(app=app), **kwargs,
            )

        with patch("ahadiff.serve.routes_growth.httpx.AsyncClient", local_cloud_client):
            desktop = TestClient(
                create_desktop_app(desktop_state), base_url="http://localhost:8765",
            )
            desktop_headers = {"Origin": "http://localhost:8765",
                               "X-AhaDiff-Token": "desktop-token"}
            desktop_access = {
                "cloud_url": "http://127.0.0.1:9001",
                "access_token": second_headers["Authorization"].removeprefix("Bearer "),
                "account_id": str(account),
            }
            desktop_export = desktop.post(
                "/api/growth/local/cloud/exports",
                headers=desktop_headers,
                json={**desktop_access, "export_id": str(uuid.uuid4())},
            )
            desktop_list = desktop.post(
                "/api/growth/local/cloud/topics", headers=desktop_headers,
                json=desktop_access,
            )
            desktop_alias = desktop.post(
                f"/api/growth/local/cloud/topics/{old_topic}",
                headers=desktop_headers, json=desktop_access,
            )
            desktop_archive = desktop.post(
                f"/api/growth/local/cloud/topics/{authorization}/update",
                headers=desktop_headers,
                json={**desktop_access, "operation_id": str(uuid.uuid4()),
                      "base_revision": 3, "title": "资源授权",
                      "status": "archived"},
            )
            desktop_reactivate = desktop.post(
                f"/api/growth/local/cloud/topics/{authorization}/update",
                headers=desktop_headers,
                json={**desktop_access, "operation_id": str(uuid.uuid4()),
                      "base_revision": 4, "title": "资源授权",
                      "status": "active"},
            )
        checks["desktop_topic_list_alias_and_update"] = (
            desktop_list.status_code == 200
            and {row["topic_id"] for row in desktop_list.json()["topics"]}
            == {str(old_topic), str(target), str(authentication), str(authorization)}
            and desktop_alias.status_code == 200
            and desktop_alias.json()["canonical_topic_id"] == str(target)
            and desktop_archive.status_code == 200
            and desktop_archive.json()["revision"] == 4
            and desktop_reactivate.status_code == 200
            and desktop_reactivate.json()["revision"] == 5
        )
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({
                "step": "desktop_topic_list_alias_archive_reactivate",
                "time": datetime.now(UTC).isoformat(),
                "statuses": [desktop_list.status_code, desktop_alias.status_code,
                             desktop_archive.status_code,
                             desktop_reactivate.status_code],
                "verdict": "PASS" if checks["desktop_topic_list_alias_and_update"]
                else "FAIL",
            }, ensure_ascii=False) + "\n")
        desktop_payload = desktop_export.json()
        desktop_history = json.loads(desktop_payload.get("json_text", "{}"))
        desktop_notes = {
            row["note_id"]: row for row in desktop_history.get("tables", {}).get("notes", [])
        }
        checks["desktop_export_keeps_originals_without_reme"] = (
            desktop_export.status_code == 200
            and desktop_notes.get(str(old_note), {}).get("content_text") == OLD_NOTE
            and desktop_notes.get(str(new_note), {}).get("content_text") == NEW_NOTE
            and desktop_notes.get(str(temporary_note), {}).get("deleted_at") is not None
            and len(desktop_history.get("tables", {}).get("topic_aliases", [])) == 1
            and TEMP_NOTE in desktop_payload.get("markdown_text", "")
        )
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({
                "step": "desktop_export_both_formats", "time": datetime.now(UTC).isoformat(),
                "actual_status": desktop_export.status_code,
                "export_id": desktop_payload.get("export_id"),
                "verdict": "PASS" if checks["desktop_export_keeps_originals_without_reme"]
                else "FAIL",
            }, ensure_ascii=False) + "\n")
        if desktop_export.status_code != 200:
            raise AssertionError(f"桌面导出失败：{desktop_payload}")
        (run_dir / "artifacts" / "desktop-export.json").write_text(
            desktop_payload["json_text"] + "\n", encoding="utf-8",
        )
        (run_dir / "artifacts" / "desktop-export.md").write_text(
            desktop_payload["markdown_text"], encoding="utf-8",
        )
        pulled = await sync_client.pull(limit=7)
        cached_aliases = local_ledger.connection.execute(
            "SELECT payload_json FROM sync_entities WHERE account_id=? "
            "AND entity_type='topic_alias' AND deleted_at IS NULL",
            (str(account),),
        ).fetchall()
        cached_moves = local_ledger.connection.execute(
            "SELECT entity_id FROM sync_entities WHERE account_id=? "
            "AND entity_type='topic_move' AND deleted_at IS NULL",
            (str(account),),
        ).fetchall()
        cached_links = local_ledger.connection.execute(
            "SELECT entity_id FROM sync_entities WHERE account_id=? "
            "AND entity_type='topic_link' AND deleted_at IS NULL",
            (str(account),),
        ).fetchall()
        checks["topic_history_reaches_local_delta_cache"] = (
            pulled > 0 and len(cached_aliases) == 1
            and json.loads(cached_aliases[0]["payload_json"])["canonical_topic_id"]
            == str(target)
            and len(cached_moves) == 3 and len(cached_links) == 2
        )
        local_ledger.close()
        artifact = run_dir / "artifacts"
        artifact.mkdir(exist_ok=True)
        (artifact / "growth-export.json").write_text(
            json.dumps(exported, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (artifact / "growth-export.md").write_text(markdown_response.text,
                                                   encoding="utf-8")
    with connect(dsn) as conn:
        rows = {
            "topics": conn.execute(
                "SELECT topic_id, title, status, revision, parent_topic_id "
                "FROM growth_topics WHERE account_id=%s ORDER BY title", (account,),
            ).fetchall(),
            "notes": conn.execute(
                "SELECT note_id, task_id, topic_id, revision, content_text, author "
                "FROM growth_notes WHERE account_id=%s ORDER BY note_id", (account,),
            ).fetchall(),
            "note_revisions": conn.execute(
                "SELECT note_id, revision, content_hash FROM growth_note_revisions "
                "WHERE account_id=%s ORDER BY note_id, revision", (account,),
            ).fetchall(),
            "aliases": conn.execute(
                "SELECT alias_topic_id, canonical_topic_id FROM growth_topic_aliases "
                "WHERE account_id=%s", (account,),
            ).fetchall(),
            "moves": conn.execute(
                "SELECT entity_type, entity_id, from_topic_id, to_topic_id, action "
                "FROM growth_topic_moves WHERE account_id=%s ORDER BY moved_at, move_id",
                (account,),
            ).fetchall(),
        }
    artifact = run_dir / "artifacts"
    (artifact / "topic-rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    checks["standalone_note_history_kept"] = (
        len(rows["notes"]) == 3
        and all(note["task_id"] is None and note["topic_id"] == target
                and note["author"] == "user" for note in rows["notes"])
        and len(rows["note_revisions"]) == 5
        and len(rows["moves"]) == 3
    )
    result = "PASS" if all(checks.values()) else "FAIL"
    (run_dir / "result.json").write_text(
        json.dumps({"status": result, "checks": checks},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if result != "PASS":
        raise AssertionError(f"M6 topic checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    run_id = f"m6-topic-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    run_dir.mkdir(parents=True)
    database_name = "growth_m6_" + uuid.uuid4().hex[:12]
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
                    "010_development_events", "011_exports", "012_task_drafts",
                    "013_chat_model_receipts", "014_review_answer_text"]
        if applied != expected:
            raise AssertionError(f"M6 迁移不完整：{applied}")
        asyncio.run(exercise(run_dir, dsn))
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
                        "stage_blockers": ["S04 代码与 live 聊天、双端 UI 和重装尚未验收"]},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (run_dir / "report.md").write_text(
            "# M6 主题维护回放\n\n"
            "合成用户经 API 创建既有主题和两篇手写笔记；在线合并保留旧 ID 别名与"
            "原文，拆分由显式笔记关联组成。归档后同一子主题重新激活，"
            "跨账号读取和父主题并入子主题均被拒绝。JSON/Markdown 导出保留"
            "冲突分支和删除记录，生成与下载均不依赖 ReMe。"
            "本机 sidecar 以已同步账号和设备身份调用真实云 API，"
            "完成主题列表、旧 ID 解析、归档/重新激活与两种格式导出。\n\n"
            f"结果：**{result['status']}**；M6/S04 live 仍未验收。\n",
            encoding="utf-8",
        )
        print(run_dir)


if __name__ == "__main__":
    main()
