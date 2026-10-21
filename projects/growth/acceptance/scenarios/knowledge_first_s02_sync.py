"""Continue a real S02 model card through two Windows workspaces and Wiki."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ahadiff.growth.knowledge_learning import (
    answer_question,
    card_detail,
    complete_learning,
    confirm_explanation,
    save_learning,
)
from ahadiff.growth.cloud.memory import ReMeAdapter, reme_note_path, run_once
from ahadiff.growth.cloud.db import connect
from ahadiff.growth.knowledge_sync import resolve_knowledge_conflict
from ahadiff.growth.knowledge_wiki import (
    create_draft,
    latest_draft,
    publish_draft,
    set_vault,
    update_draft,
)
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.publish import publication_plan
from ahadiff.growth.sync import GrowthSyncStore, connect_and_sync
from knowledge_sync_full_chain import _launch_cloud
from m1_production_cloud_smoke import JwksHandler, free_port, stop
from m4_production_worker_smoke import CONFIG, REME, start_process, stop_reme, wait_reme


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def _continue(
    run: Path, paused: dict[str, object], origin: str, token: str,
) -> dict[str, object]:
    card_id = str(paused["card_id"])
    source_db = run / "fixture" / "search-demo" / ".ahadiff" / "growth.sqlite"
    second_db = run / "device-b" / ".ahadiff" / "growth.sqlite"
    second_db.parent.mkdir(parents=True, exist_ok=False)
    first = GrowthLocalRepository(source_db)
    second = GrowthLocalRepository(second_db)
    try:
        before = card_detail(first, card_id)
        _require(before["learning"]["stage"] == "understanding", "设备 A 未暂停在理解题")
        _require(not before["learning"]["answers"], "暂停前已有正式作答")
        _require(
            before["card"]["followup_mark"] == "revisit"
            and len(before["conversation"]) == 1
            and before["conversation"][0]["request_id"] == paused["stream_request_id"],
            "设备 A 未持久保存卡内对话或主动回看标记",
        )
        project_id = str(paused["project_id"])
        plan = publication_plan(first, project_id)
        async with httpx.AsyncClient(
            base_url=origin, timeout=60.0, trust_env=False,
        ) as client:
            first_sync = await connect_and_sync(
                first, client, token, publish_project_id=project_id,
                approved_digest=plan["digest"],
            )
            _require(first_sync["knowledge_uploaded"] == 1, "设备 A 未上传唯一正式卡")
            second_sync = await connect_and_sync(second, client, token)
            _require(second_sync["knowledge_pulled"] == 1, "设备 B 未下载正式卡")
            resumed = card_detail(second, card_id)
            question = next(q for q in resumed["questions"] if q["kind"] == "understanding")
            _require(
                resumed["card"]["concept_id"] == paused["concept_id"]
                and resumed["card"]["card_version"] == before["card"]["card_version"]
                and resumed["learning"]["stage"] == "understanding"
                and resumed["learning"]["option_drafts"].get(question["question_id"]) == "A"
                and resumed["learning"]["note_text"] == before["learning"]["note_text"]
                and resumed["card"]["followup_mark"] == "revisit"
                and resumed["card"]["followup_reason"] == before["card"]["followup_reason"]
                and len(resumed["conversation"]) == 1
                and resumed["conversation"][0]["request_id"] == paused["stream_request_id"]
                and resumed["conversation"][0]["reply_text"] == before["conversation"][0]["reply_text"]
                and not resumed["learning"]["answers"],
                "设备 B 未恢复同一卡、未提交选项、对话、主动标记及亲写笔记",
            )
            _require(
                second.connection.execute(
                    "SELECT COUNT(*) FROM knowledge_vault_settings"
                ).fetchone()[0] == 0,
                "设备 B 不应预设 Obsidian vault",
            )
            understanding = answer_question(
                second, card_id, request_id=str(uuid.uuid4()),
                question_id=question["question_id"], version=question["version"],
                option_id="A",
            )
            answered = next(q for q in understanding["questions"] if q["kind"] == "understanding")
            _require(len(answered["explanations"]) == 4, "理解题未返回四项解析")
            confirm_explanation(
                second, card_id, question["question_id"], request_id=str(uuid.uuid4()),
            )
            prediction = next(q for q in resumed["questions"] if q["kind"] == "prediction")
            save_learning(
                second, card_id, stage="prediction",
                option_drafts={prediction["question_id"]: "B"},
            )
            partial_push = await connect_and_sync(second, client, token)
            partial_pull = await connect_and_sync(first, client, token)
            midway = card_detail(first, card_id)
            _require(
                midway["learning"]["stage"] == "prediction"
                and midway["learning"]["option_drafts"].get(prediction["question_id"]) == "B"
                and midway["learning"]["answers"]["understanding"]["explanation_confirmed"],
                "设备 A 未回读设备 B 的首次判定、解析与预测题草稿",
            )
            predicted = answer_question(
                second, card_id, request_id=str(uuid.uuid4()),
                question_id=prediction["question_id"], version=prediction["version"],
                option_id="B",
            )
            predicted_question = next(
                q for q in predicted["questions"] if q["kind"] == "prediction"
            )
            _require(len(predicted_question["explanations"]) == 4, "预测题未返回四项解析")
            confirm_explanation(
                second, card_id, prediction["question_id"], request_id=str(uuid.uuid4()),
            )
            completed = complete_learning(second, card_id)
            _require(completed["learning"]["stage"] == "completed", "设备 B 未完成首次学习")
            draft = create_draft(second, card_id, request_id=str(uuid.uuid4()))
            _require(draft["status"] == "draft", "无 vault 的设备 B 未保留应用内草稿")
            manual = "\n验收人工补充：旧请求仍可完成，但它不能覆盖最新搜索结果。\n"
            draft = update_draft(second, draft["draft_id"], draft["markdown"] + manual)
            _require(
                second.connection.execute(
                    "SELECT COUNT(*) FROM knowledge_wiki_articles"
                ).fetchone()[0] == 0,
                "设备 B 未确认时出现 Wiki 正式文章",
            )
            completed_push = await connect_and_sync(second, client, token)
            completed_pull = await connect_and_sync(first, client, token)
            on_first = card_detail(first, card_id)
            pulled_draft = latest_draft(first, card_id)
            _require(
                on_first["learning"]["stage"] == "completed"
                and pulled_draft is not None
                and pulled_draft["draft_id"] == draft["draft_id"],
                "设备 A 未回读设备 B 的学习完成与 Wiki 草稿",
            )
            vault = run / "obsidian-vault"
            (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
            set_vault(first, str(vault), "工程成长伴侣/S02")
            article = publish_draft(first, draft["draft_id"], request_id=str(uuid.uuid4()))
            target = vault / article["relative_path"]
            _require(target.is_file() and manual.strip() in target.read_text(encoding="utf-8"),
                     "设备 A 未把用户确认草稿写入配置的 vault")
            article_push = await connect_and_sync(first, client, token)
            article_pull = await connect_and_sync(second, client, token)
            remote_article = second.connection.execute(
                "SELECT article_id,relative_path FROM knowledge_wiki_articles "
                "WHERE concept_id=?", (paused["concept_id"],),
            ).fetchone()
            _require(
                remote_article is not None
                and remote_article["article_id"] == article["article_id"]
                and remote_article["relative_path"] == article["relative_path"]
                and second.connection.execute(
                    "SELECT COUNT(*) FROM knowledge_vault_settings"
                ).fetchone()[0] == 0,
                "设备 B 未保持同一文章身份或错误同步了设备 A 的本机 vault",
            )
            task_row = first.connection.execute(
                "SELECT payload_json FROM sync_entities WHERE account_id=? "
                "AND entity_type='task' AND entity_id=?",
                (str(first_sync["account_id"]), card_id),
            ).fetchone()
            _require(task_row is not None, "云端未保留 S02 正式卡与主题关系")
            topic_id = json.loads(task_row["payload_json"])["topic_id"]
            note_id = str(uuid.uuid4())
            note_operation_id = str(uuid.uuid4())
            note_base = "N-base：S02RACE 我原以为先发的搜索请求总会先返回，现已知道响应顺序不固定。"
            create_body = {
                "cloud_url": origin, "access_token": token,
                "account_id": str(first_sync["account_id"]),
                "operation_id": note_operation_id, "note_id": note_id,
                "task_id": card_id, "content_text": note_base,
            }
            # 服务端完成提交后丢弃客户端收到的正文，原 ID 和原正文直接重试。
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:8782", timeout=60.0, trust_env=False,
            ) as local_client:
                local_headers = {"Origin": "http://127.0.0.1:8782"}
                auth = await local_client.post("/api/auth/token", headers=local_headers)
                auth.raise_for_status()
                local_headers["X-AhaDiff-Token"] = auth.json()["token"]
                create_path = f"/api/growth/local/cloud/topics/{topic_id}/notes"
                committed = await local_client.post(
                    create_path, headers=local_headers, json=create_body,
                )
                _require(committed.status_code == 201, "旧笔记首次提交失败")
                await committed.aclose()
                retried = await local_client.post(
                    create_path, headers=local_headers, json=create_body,
                )
                _require(retried.status_code == 201, "旧笔记同 ID 重试失败")
                retried_body = retried.json()
                _require(
                    retried_body["note_id"] == note_id
                    and retried_body["revision"] == 1,
                    "响应丢失重试没有复用唯一笔记或基础版本",
                )
            note_first_pull = await connect_and_sync(first, client, token)
            note_second_pull = await connect_and_sync(second, client, token)
            account_id = uuid.UUID(str(first_sync["account_id"]))
            first_store = GrowthSyncStore(
                first, account_id, uuid.UUID(str(first_sync["device_id"])),
            )
            second_store = GrowthSyncStore(
                second, account_id, uuid.UUID(str(second_sync["device_id"])),
            )
            first_note = next(n for n in first_store.note_workspace() if n["note_id"] == note_id)
            second_note = next(n for n in second_store.note_workspace() if n["note_id"] == note_id)
            _require(
                first_note["note"]["content_text"] == note_base
                and second_note["note"]["content_text"] == note_base
                and first_note["revision"] == second_note["revision"] == 1,
                "两设备未取回同一份 N-base 原文",
            )
            note_mac = note_base + "\nN-mac：我补充记录，旧请求可能最后完成。"
            note_win = note_base + "\nN-win：我补充记录，只有最新请求可以 render。"
            note_merged = "N-merged：\n" + note_win + "\nN-mac：我补充记录，旧请求可能最后完成。"
            mac_operation = second_store.queue_note_revision(
                uuid.UUID(note_id), 1, note_mac,
            )
            win_operation = first_store.queue_note_revision(
                uuid.UUID(note_id), 1, note_win,
            )
            note_win_push = await connect_and_sync(first, client, token)
            note_mac_conflict = await connect_and_sync(second, client, token)
            conflicted_note = next(
                n for n in second_store.note_workspace() if n["note_id"] == note_id
            )
            _require(
                conflicted_note["revision"] == 2
                and conflicted_note["note"]["content_text"] == note_win
                and len(conflicted_note["conflicts"]) == 1
                and conflicted_note["conflicts"][0]["proposed_text"] == note_mac
                and any(d["content_text"] == note_mac for d in conflicted_note["drafts"]),
                "N-mac 离线分支未保留为可见冲突及草稿",
            )
            conflict_id = str(conflicted_note["conflicts"][0]["conflict_id"])
            merge_operation = second_store.queue_note_resolution(
                uuid.UUID(note_id), 2, [uuid.UUID(conflict_id)], note_merged,
            )
            note_merge_push = await connect_and_sync(second, client, token)
            note_merge_pull = await connect_and_sync(first, client, token)
            merged_first = next(
                n for n in first_store.note_workspace() if n["note_id"] == note_id
            )
            merged_second = next(
                n for n in second_store.note_workspace() if n["note_id"] == note_id
            )
            for merged_note in (merged_first, merged_second):
                _require(
                    merged_note["revision"] == 3
                    and merged_note["note"]["content_text"] == note_merged
                    and [r["content_text"] for r in merged_note["revisions"]]
                    == [note_base, note_win, note_merged]
                    and len(merged_note["conflicts"]) == 1
                    and merged_note["conflicts"][0]["proposed_text"] == note_mac
                    and merged_note["conflicts"][0]["resolved_revision"] == 3,
                    "N-base/N-mac/N-win/N-merged 未在双端保留完整历史",
                )
            base_note = card_detail(first, card_id)["learning"]["note_text"]
            note_a = base_note + " 设备 A 补充：取消请求须另行实现。"
            note_b = base_note + " 设备 B 补充：旧结果不能更新页面。"
            save_learning(first, card_id, note_text=note_a)
            save_learning(second, card_id, note_text=note_b)
            conflict_a_push = await connect_and_sync(first, client, token)
            conflict_b_detect = await connect_and_sync(second, client, token)
            _require(card_id in conflict_b_detect["knowledge_conflicts"],
                     "离线双端笔记未显式报告冲突")
            _require(card_detail(second, card_id)["learning"]["note_text"] == note_b,
                     "冲突检测覆盖了设备 B 的本机笔记")
            account_id = str(first_sync["account_id"])
            resolve_knowledge_conflict(second, account_id, card_id, "keep_local", origin)
            conflict_b_push = await connect_and_sync(second, client, token)
            conflict_a_pull = await connect_and_sync(first, client, token)
            _require(card_detail(first, card_id)["learning"]["note_text"] == note_b,
                     "显式保留设备 B 内容后未回读到设备 A")
            return {
                "status": "KNOWLEDGE_SEGMENT_PASS", "case_id": "S02",
                "scope": "git_to_wiki_stream_retry_legacy_note_merge_and_card_note_conflict",
                "card_id": card_id,
                "concept_id": paused["concept_id"], "head_sha": paused["head_sha"],
                "first_sync": first_sync, "second_sync": second_sync,
                "partial_push": partial_push, "partial_pull": partial_pull,
                "completed_push": completed_push, "completed_pull": completed_pull,
                "article_push": article_push, "article_pull": article_pull,
                "article_id": article["article_id"],
                "article_path": str(target), "vault_on_second_device": False,
                "note_id": note_id, "note_topic_id": topic_id,
                "note_creation_operation_id": note_operation_id,
                "note_creation_retry": retried_body,
                "note_first_pull": note_first_pull,
                "note_second_pull": note_second_pull,
                "note_mac_operation_id": mac_operation,
                "note_win_operation_id": win_operation,
                "note_win_push": note_win_push,
                "note_mac_conflict": note_mac_conflict,
                "note_conflict_id": conflict_id,
                "note_merge_operation_id": merge_operation,
                "note_merge_push": note_merge_push,
                "note_merge_pull": note_merge_pull,
                "note_final_revision": merged_first["revision"],
                "conflict_a_push": conflict_a_push,
                "conflict_b_detect": conflict_b_detect,
                "conflict_b_push": conflict_b_push,
                "conflict_a_pull": conflict_a_pull,
                "first_answer": understanding["learning"]["answers"]["understanding"],
                "second_answer": predicted["learning"]["answers"]["prediction"],
            }
    finally:
        first.close()
        second.close()


async def _verify_memory(
    run: Path, dsn: str, origin: str, reme_url: str, token: str,
    result: dict[str, object], account: uuid.UUID,
) -> dict[str, object]:
    note_id = str(result["note_id"])
    card_id = str(result["card_id"])
    device_id = str(result["first_sync"]["device_id"])
    headers = {"Authorization": f"Bearer {token}", "X-Device-Id": device_id}
    path = reme_note_path(account, uuid.UUID(note_id))
    with connect(dsn) as conn:
        saved = conn.execute(
            "SELECT revision, content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(note_id)),
        ).fetchone()
    _require(saved is not None and saved["revision"] == 3, "ReMe 故障前业务笔记不存在")
    async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
        before = await cloud.get(f"/v1/tasks/{card_id}/journey", headers=headers)
        before.raise_for_status()
        before_body = before.json()
        _require(
            any(event["source_type"] == "note" and event["source_id"] == note_id
                for event in before_body["timeline"]),
            "成长时间线缺少真实用户笔记",
        )
        unavailable = await cloud.get(
            "/v1/growth/context", params={"query": "S02RACE"}, headers=headers,
        )
        _require(unavailable.status_code == 503, "ReMe 未启动时未明确提示不可用")

    outage_outcomes: list[str] = []
    async with httpx.AsyncClient(base_url=reme_url, timeout=2, trust_env=False) as unavailable_reme:
        for _ in range(6):
            outcome = await run_once(dsn, ReMeAdapter(unavailable_reme))
            if outcome is None:
                break
            outage_outcomes.append(outcome)
            if outcome == "failed":
                break
    _require("failed" in outage_outcomes, "真实 ReMe 故障未产生持久失败任务")
    with connect(dsn) as conn:
        persisted = conn.execute(
            "SELECT revision, content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(note_id)),
        ).fetchone()
        failed = conn.execute(
            "SELECT state, attempt_count FROM growth_memory_jobs WHERE account_id=%s "
            "AND source_type='note' AND source_id=%s AND source_version=3",
            (account, uuid.UUID(note_id)),
        ).fetchone()
    _require(
        persisted == saved and failed is not None and failed["state"] == "failed",
        "ReMe 故障影响了业务笔记或未保留失败任务",
    )

    workspace = run / "reme-workspace"
    workspace.mkdir(exist_ok=False)
    environment = os.environ.copy()
    reme = start_process([
        str(REME), "start", f"config={CONFIG}", f"workspace_dir={workspace}",
        f"service.port={reme_url.rsplit(':', 1)[1]}",
    ], run, "reme", environment)
    try:
        version = wait_reme(reme, reme_url)
        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET next_retry_at=now() "
                "WHERE account_id=%s AND state='failed'", (account,),
            )
        recovery_outcomes: list[str] = []
        async with httpx.AsyncClient(base_url=reme_url, timeout=25, trust_env=False) as memory:
            adapter = ReMeAdapter(memory)
            for _ in range(12):
                outcome = await run_once(dsn, adapter)
                if outcome is None:
                    break
                recovery_outcomes.append(outcome)
            _require("succeeded" in recovery_outcomes, "ReMe 恢复后未完成新版笔记投影")
            projected = workspace / Path(path)
            _require(
                projected.is_file() and "N-merged" in projected.read_text(encoding="utf-8"),
                "真实 ReMe 文件未保存最新合并原文",
            )
            reindex = await memory.post("/reindex", json={})
            reindex.raise_for_status()
            found: list[str] = []
            for _ in range(30):
                found = await adapter.search_paths("S02RACE", limit=20)
                if path in found:
                    break
                await asyncio.sleep(1)
            _require(path in found, "真实 ReMe 检索未命中当前笔记路径")
        async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
            context = await cloud.get(
                "/v1/growth/context", params={"query": "S02RACE"}, headers=headers,
            )
            context.raise_for_status()
            context_body = context.json()
            journey = await cloud.get(f"/v1/tasks/{card_id}/journey", headers=headers)
            journey.raise_for_status()
            journey_body = journey.json()
        _require(
            context_body["memory_status"] == "current"
            and any(n["note_id"] == note_id and n["content_text"] == saved["content_text"]
                    for n in context_body["notes"])
            and any(m["source_id"] == note_id and m["source_version"] == 3
                    for m in journey_body["memory_refs"]),
            "ReMe 路径未按账号与当前版本回源至 S02 时间线",
        )
        return {
            "status": "PASS", "reme_version": version,
            "outage_outcomes": outage_outcomes,
            "recovery_outcomes": recovery_outcomes,
            "note_path": path, "search_paths": found,
            "context_note_ids": [n["note_id"] for n in context_body["notes"]],
            "timeline_note_ids": [e["source_id"] for e in journey_body["timeline"]
                                  if e["source_type"] == "note"],
            "memory_note_refs": [m for m in journey_body["memory_refs"]
                                 if m["source_type"] == "note"],
        }
    finally:
        stop_reme(reme)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--admin-dsn", required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve(strict=True)
    root = Path(__file__).resolve().parents[2]
    if run.parent != (root / "acceptance" / "runs").resolve():
        raise ValueError("S02 run 目录必须位于本仓库 acceptance/runs 内")
    paused = json.loads((run / "model-paused-result.json").read_text(encoding="utf-8"))
    if paused["case_id"] != "S02" or paused["stage"] != "paused_before_understanding_answer":
        raise ValueError("S02 第一设备尚未停在真实待提交选项")
    database = "growth_s02_" + uuid.uuid4().hex[:12]
    dsn = make_conninfo(args.admin_dsn, dbname=database)
    account = uuid.uuid4()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "growth-s02-live", "alg": "RS256", "use": "sig"})
    JwksHandler.document = json.dumps({"keys": [public_jwk]}).encode("utf-8")
    issuer_server = ThreadingHTTPServer(("127.0.0.1", 0), JwksHandler)
    issuer_thread = threading.Thread(
        target=issuer_server.serve_forever, daemon=True,
    )
    issuer_thread.start()
    issuer = f"http://127.0.0.1:{issuer_server.server_port}/auth/v1"
    jwks_url = f"http://127.0.0.1:{issuer_server.server_port}/.well-known/jwks.json"
    cloud_port = free_port()
    origin = f"http://127.0.0.1:{cloud_port}"
    reme_url = f"http://127.0.0.1:{free_port()}"
    token = jwt.encode({
        "iss": issuer, "aud": "authenticated", "sub": str(account),
        "role": "authenticated",
        "exp": datetime.now(UTC) + timedelta(minutes=30),
    }, private_key, algorithm="RS256", headers={"kid": "growth-s02-live"})
    process = None
    try:
        with psycopg.connect(args.admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        process = _launch_cloud(run, dsn, issuer, jwks_url, cloud_port, reme_url)
        result = asyncio.run(_continue(run, paused, origin, token))
        result["memory"] = asyncio.run(
            _verify_memory(run, dsn, origin, reme_url, token, result, account)
        )
        result["database"] = database
        (run / "s02-sync-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"S02_KNOWLEDGE_CROSS_DEVICE_PASS {run}")
    finally:
        stop(process)
        issuer_server.shutdown()
        issuer_server.server_close()


if __name__ == "__main__":
    main()
