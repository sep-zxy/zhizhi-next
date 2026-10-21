"""Real two-device knowledge continuation against an isolated PostgreSQL cloud."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
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

from m1_production_cloud_smoke import (
    JwksHandler, await_ready, free_port, stop,
)
from ahadiff.growth.knowledge_first import cards
from ahadiff.growth.knowledge_learning import (
    LearningMaterial, answer_question, card_detail, complete_learning,
    confirm_explanation,
    revise_material, save_learning, save_material, set_followup_mark,
)
from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.growth.cloud.memory import ReMeAdapter, reme_note_path, run_once
from ahadiff.growth.knowledge_sync import resolve_knowledge_conflict
from ahadiff.growth.publish import publication_plan
from ahadiff.growth.sync import connect_and_sync
from ahadiff.growth.acceptance import FIXTURES
from ahadiff.serve.app import create_app
from ahadiff.serve.state import ServeState
from m4_production_worker_smoke import CONFIG, REME, start_process, stop_reme, wait_reme


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "acceptance" / "runs"
S01_NOTE_TEXT = (
    "我以前把 CompletableFuture 当成“每个任务自动新建线程”。这次代码把两个查询交给同一个 "
    "executor，实际线程由执行器安排。thenCombine 组合结果，join 会等待，所以这段代码并不意味着"
    "调用方从头到尾都不阻塞。以后排查并发问题，我要先找执行器在哪里配置。"
)
S03_NOTE_TEXT = (
    "订单计价现在先经过 PriceCache，再按需要调用 PricingGateway。这个例子的 TTL 是访问时检查的："
    "到期不会自动刷新，下一次 get 才重新加载。我不能只看到 expiresAt 就推断项目里存在定时清理线程。"
    "另外，这个 HashMap 实现是否适合并发使用，需要单独检查。"
)


def _copy_database(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as original, sqlite3.connect(target) as copy:
        original.backup(copy)


def _existing_real_material(
    ledger: GrowthLocalRepository, card_id: str, version: int,
) -> LearningMaterial:
    """Reuse a real model-produced lesson solely to exercise version transport."""
    c = ledger.connection
    row = c.execute(
        "SELECT * FROM card_learning_materials WHERE card_id=? AND card_version=?",
        (card_id, version),
    ).fetchone()
    questions = c.execute(
        "SELECT * FROM card_objective_questions WHERE card_id=? AND card_version=?",
        (card_id, version),
    ).fetchall()
    if row is None or len(questions) != 2:
        raise AssertionError("跨设备版本夹具缺少既有真实模型题包")
    question_data = {
        question["kind"]: {
            "stem": question["stem"],
            "scenario": question["scenario"],
            "options": json.loads(question["options_json"]),
            "correct_option_id": question["correct_option_id"],
            "explanations": json.loads(question["explanations_json"]),
            "reasoning": question["reasoning"],
            "boundary": question["boundary"],
            "source_refs": json.loads(question["source_refs_json"]),
        }
        for question in questions
    }
    return LearningMaterial.model_validate({
        "front_question": row["front_question"],
        "back_summary": row["back_summary"],
        "back_mechanism": row["back_mechanism"],
        "back_boundary": row["back_boundary"],
        "misconceptions": json.loads(row["misconceptions_json"]),
        "source_refs": json.loads(row["source_refs_json"]),
        **question_data,
    })


def _launch_cloud(
    attempt: Path, dsn: str, issuer: str, jwks_url: str, port: int,
    reme_url: str | None = None,
) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment.update({
        "GROWTH_DATABASE_URL": dsn,
        "GROWTH_AUTH_ISSUER": issuer,
        "GROWTH_AUTH_JWKS_URL": jwks_url,
        "GROWTH_AUTH_AUDIENCE": "authenticated",
        "GROWTH_API_HOST": "127.0.0.1",
        "GROWTH_API_PORT": str(port),
    })
    if reme_url is None:
        environment.pop("GROWTH_REME_URL", None)
    else:
        environment["GROWTH_REME_URL"] = reme_url
    log_path = attempt / "cloud.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "ahadiff.growth.cloud"],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
    await_ready(process, f"http://127.0.0.1:{port}")
    return process


async def _exercise(
    source: Path, attempt: Path, origin: str, token: str,
) -> dict[str, object]:
    first_db = attempt / "device-a.sqlite"
    second_db = attempt / "device-b.sqlite"
    _copy_database(source, first_db)
    first = GrowthLocalRepository(first_db)
    second = GrowthLocalRepository(second_db)
    try:
        first_cards = cards(first)
        if len(first_cards) < 4:
            raise AssertionError("源设备不足四类真实知识卡")
        project_ids = {
            project_id for card in first_cards for project_id in card["project_ids"]
        }
        if len(project_ids) != 1:
            raise AssertionError("本次双设备验收要求同一来源项目")
        project_id = next(iter(project_ids))
        plan = publication_plan(first, project_id)
        async with httpx.AsyncClient(
            base_url=origin, timeout=60.0, trust_env=False,
        ) as client:
            first_sync = await connect_and_sync(
                first, client, token, publish_project_id=project_id,
                approved_digest=plan["digest"],
            )
            if first_sync["knowledge_uploaded"] < 4:
                raise AssertionError(f"首设备知识卡未全部上传：{first_sync}")
            second_sync = await connect_and_sync(second, client, token)
            if second_sync["knowledge_pulled"] < 4:
                raise AssertionError(f"第二设备知识卡未全部下载：{second_sync}")
            for card in first_cards:
                card_id = card["card_id"]
                a = card_detail(first, card_id)
                b = card_detail(second, card_id)
                if (a["card"]["concept_id"] != b["card"]["concept_id"]
                        or a["card"]["card_version"] != b["card"]["card_version"]
                        or a["learning"]["stage"] != b["learning"]["stage"]
                        or len(a["questions"]) != len(b["questions"])
                        or len(a["conversation"]) != len(b["conversation"])):
                    raise AssertionError(f"第二设备学习状态不连续：{card_id}")
            chosen = next(
                card for card in first_cards
                if card["evidence_count"] >= 2
            )
            chosen_id = chosen["card_id"]
            save_learning(
                second, chosen_id,
                note_text="第二设备接续后的个人补充：记录实际条件与边界。",
            )
            set_followup_mark(
                second, chosen_id, "revisit", "第二设备手动标记回看",
            )
            second_push = await connect_and_sync(second, client, token)
            if second_push["knowledge_uploaded"] < 1:
                raise AssertionError("第二设备笔记与回看标记未上传")
            first_pull = await connect_and_sync(first, client, token)
            synced = card_detail(first, chosen_id)
            if (synced["learning"]["note_text"] !=
                    "第二设备接续后的个人补充：记录实际条件与边界。"
                    or synced["card"]["followup_mark"] != "revisit"):
                raise AssertionError("首设备未回读第二设备学习状态")
            if first_pull["knowledge_conflicts"]:
                raise AssertionError("顺序同步不应产生冲突")
            old_article = first.connection.execute(
                "SELECT concept_id,article_id,relative_path FROM knowledge_wiki_articles "
                "WHERE concept_id=?", (synced["card"]["concept_id"],),
            ).fetchone()
            current_version = int(synced["card"]["card_version"])
            material = _existing_real_material(first, chosen_id, current_version)
            revise_material(
                first, chosen_id, request_id=str(uuid.uuid4()),
                expected_version=current_version,
                reason="双设备续学验收：显式修订题包，核对新旧学习记录连续性。",
            )
            save_material(
                first, chosen_id, material,
                provider_name="sync-fixture-existing-real-material",
                payload_hash=hashlib.sha256(
                    f"knowledge-sync:{chosen_id}:{current_version + 1}".encode()
                ).hexdigest(),
            )
            save_learning(first, chosen_id, stage="back")
            first_new = card_detail(first, chosen_id)
            understanding = next(
                q for q in first_new["questions"] if q["kind"] == "understanding"
            )
            prediction = next(
                q for q in first_new["questions"] if q["kind"] == "prediction"
            )
            save_learning(
                first, chosen_id, stage="understanding",
                option_drafts={understanding["question_id"]: "A"},
            )
            revision_push = await connect_and_sync(first, client, token)
            if revision_push["knowledge_uploaded"] < 1:
                raise AssertionError("新版本及未提交选项未同步")
            revision_pull = await connect_and_sync(second, client, token)
            continued = card_detail(second, chosen_id)
            if (continued["card"]["card_version"] != current_version + 1
                    or continued["learning"]["stage"] != "understanding"
                    or continued["learning"]["option_drafts"].get(
                        understanding["question_id"]
                    ) != "A"):
                raise AssertionError("第二设备未恢复卡版本与未提交选择")
            answer_question(
                second, chosen_id, request_id=str(uuid.uuid4()),
                question_id=understanding["question_id"],
                version=understanding["version"], option_id="A",
            )
            confirm_explanation(
                second, chosen_id, understanding["question_id"],
                request_id=str(uuid.uuid4()),
            )
            save_learning(
                second, chosen_id, stage="prediction",
                option_drafts={prediction["question_id"]: "B"},
            )
            answer_push = await connect_and_sync(second, client, token)
            answer_pull = await connect_and_sync(first, client, token)
            a_continued = card_detail(first, chosen_id)
            if (not a_continued["learning"]["answers"]["understanding"][
                    "explanation_confirmed"
                ] or a_continued["learning"]["option_drafts"].get(
                    prediction["question_id"]
                ) != "B"):
                raise AssertionError("首设备未恢复客观判定、解析和预测草稿")
            answer_question(
                first, chosen_id, request_id=str(uuid.uuid4()),
                question_id=prediction["question_id"],
                version=prediction["version"], option_id="B",
            )
            confirm_explanation(
                first, chosen_id, prediction["question_id"],
                request_id=str(uuid.uuid4()),
            )
            complete_learning(first, chosen_id)
            completed_push = await connect_and_sync(first, client, token)
            completed_pull = await connect_and_sync(second, client, token)
            completed = card_detail(second, chosen_id)
            article = second.connection.execute(
                "SELECT concept_id,article_id,relative_path FROM knowledge_wiki_articles "
                "WHERE concept_id=?", (completed["card"]["concept_id"],),
            ).fetchone()
            if (completed["learning"]["stage"] != "completed"
                    or len(completed["learning_history"]) < 1
                    or not all(
                        completed["learning"]["answers"][kind][
                            "explanation_confirmed"
                        ] for kind in ("understanding", "prediction")
                    )
                    or not old_article or not article
                    or tuple(old_article) != tuple(article)):
                raise AssertionError("第二设备完成状态、旧历史或 Wiki 身份丢失")
            account_id = str(first_sync["account_id"])
            save_learning(
                first, chosen_id, note_text="设备 A 离线编辑：保留本机和云端冲突证据。",
            )
            save_learning(
                second, chosen_id, note_text="设备 B 离线编辑：选择保留本机版本。",
            )
            conflict_a_push = await connect_and_sync(first, client, token)
            conflict_b_detect = await connect_and_sync(second, client, token)
            if chosen_id not in conflict_b_detect["knowledge_conflicts"]:
                raise AssertionError("并发离线笔记未报告冲突")
            local_note = card_detail(second, chosen_id)["learning"]["note_text"]
            if local_note != "设备 B 离线编辑：选择保留本机版本。":
                raise AssertionError("冲突检测覆盖了设备 B 的本机内容")
            conflict_state = attempt / "conflict-device" / ".ahadiff"
            conflict_state.mkdir(parents=True)
            _copy_database(second_db, conflict_state / "growth.sqlite")
            conflict_ui_state = attempt / "conflict-ui-device" / ".ahadiff"
            conflict_ui_state.mkdir(parents=True)
            _copy_database(second_db, conflict_ui_state / "growth.sqlite")
            local_token = uuid.uuid4().hex
            local_app = create_app(ServeState(
                state_dir=conflict_state, token=local_token,
            ))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=local_app),
                base_url="http://127.0.0.1:8765", trust_env=False,
            ) as local_client:
                local_headers = {
                    "X-AhaDiff-Token": local_token,
                    "Origin": "http://127.0.0.1:8765",
                }
                conflict_list = await local_client.get(
                    "/api/growth/local/knowledge/sync/conflicts",
                    headers=local_headers,
                )
                if (conflict_list.status_code != 200 or
                        chosen_id not in {
                            row["card_id"] for row in
                            conflict_list.json()["conflicts"]
                        }):
                    raise AssertionError(
                        f"本地 API 未列出真实知识卡冲突："
                        f"{conflict_list.status_code} {conflict_list.text}"
                    )
                resolved = await local_client.post(
                    f"/api/growth/local/knowledge/sync/conflicts/"
                    f"{chosen_id}/resolve",
                    headers=local_headers,
                    json={"account_id": account_id, "decision": "keep_local"},
                )
                if resolved.status_code != 200:
                    raise AssertionError(
                        f"本地 API 无法解决冲突：{resolved.status_code} "
                        f"{resolved.text}"
                    )
            with GrowthLocalRepository(conflict_state / "growth.sqlite") as copy:
                if (copy.connection.execute(
                    "SELECT conflict_json FROM knowledge_sync_state "
                    "WHERE account_id=? AND card_id=?",
                    (account_id, chosen_id),
                ).fetchone()[0] is not None or
                        card_detail(copy, chosen_id)["learning"]["note_text"]
                        != local_note):
                    raise AssertionError("本地 API 解决冲突时丢失本机笔记")
            resolve_knowledge_conflict(
                second, account_id, chosen_id, "keep_local", origin,
            )
            conflict_b_push = await connect_and_sync(second, client, token)
            conflict_a_pull = await connect_and_sync(first, client, token)
            if (conflict_b_push["knowledge_uploaded"] < 1
                    or card_detail(first, chosen_id)["learning"]["note_text"]
                    != local_note):
                raise AssertionError("显式保留本机后未同步到设备 A")
            save_learning(
                first, chosen_id, note_text="设备 A 再次离线编辑：即将采用云端。",
            )
            save_learning(
                second, chosen_id, note_text="设备 B 再次编辑：云端胜出。",
            )
            second_latest = await connect_and_sync(second, client, token)
            accept_detect = await connect_and_sync(first, client, token)
            if chosen_id not in accept_detect["knowledge_conflicts"]:
                raise AssertionError("第二次并发编辑未报告冲突")
            resolve_knowledge_conflict(
                first, account_id, chosen_id, "accept_remote", origin,
            )
            accepted = card_detail(first, chosen_id)
            if accepted["learning"]["note_text"] != "设备 B 再次编辑：云端胜出。":
                raise AssertionError("显式采用云端后未更新设备 A")
            if first.connection.execute(
                "SELECT conflict_json FROM knowledge_sync_state "
                "WHERE account_id=? AND card_id=?", (account_id, chosen_id),
            ).fetchone()[0] is not None:
                raise AssertionError("冲突解决状态未清空")
            return {
                "source_card_count": len(first_cards),
                "card_ids": [card["card_id"] for card in first_cards],
                "first_sync": first_sync,
                "second_sync": second_sync,
                "second_push": second_push,
                "first_pull": first_pull,
                "roundtrip_card_id": chosen_id,
                "roundtrip_note": synced["learning"]["note_text"],
                "roundtrip_mark": synced["card"]["followup_mark"],
                "revision_version": completed["card"]["card_version"],
                "revision_source": "既有真实模型题包的双设备传输夹具",
                "revision_push": revision_push,
                "revision_pull": revision_pull,
                "answer_push": answer_push,
                "answer_pull": answer_pull,
                "completed_push": completed_push,
                "completed_pull": completed_pull,
                "wiki_identity_stable": True,
                "conflict_a_push": conflict_a_push,
                "conflict_b_detect": conflict_b_detect,
                "conflict_b_push": conflict_b_push,
                "conflict_a_pull": conflict_a_pull,
                "second_latest": second_latest,
                "accept_detect": accept_detect,
                "explicit_conflict_resolution": True,
                "conflict_local_api": "PASS",
                "conflict_ui_repo": str(conflict_ui_state.parent),
                "vault_on_second_device": second.connection.execute(
                    "SELECT COUNT(*) FROM knowledge_vault_settings"
                ).fetchone()[0],
            }
    finally:
        first.close()
        second.close()


async def _verify_s01_memory(
    source_run: Path, attempt: Path, dsn: str, origin: str,
    reme_url: str, token: str, account: uuid.UUID, sync_result: dict[str, object],
    executable: Path | None = None,
    card_id_override: str | None = None,
    existing_note_id: str | None = None,
) -> dict[str, object]:
    if card_id_override is None:
        first_result = json.loads(
            (source_run / "full-chain-result.json").read_text(encoding="utf-8")
        )
        card_id = next(item["card_id"] for item in first_result if item["kind"] == "concurrency")
    else:
        card_id = card_id_override
    expected_answer_ids: list[str] = []
    if card_id_override is not None:
        with psycopg.connect(dsn) as cloud:
            bundle = cloud.execute(
                "SELECT bundle FROM growth_knowledge_bundles "
                "WHERE account_id=%s AND card_id=%s",
                (account, uuid.UUID(card_id)),
            ).fetchone()
        if bundle is None:
            raise AssertionError("S01 安装版原卡题包尚未上传云端")
        expected_answer_ids = [item["answer_id"] for item in
                               bundle[0]["tables"]["card_objective_answers"]]
        if len(expected_answer_ids) != 2:
            raise AssertionError("S01 云端原卡缺少两道首次客观作答")
    note_id = uuid.UUID(existing_note_id) if existing_note_id else uuid.uuid4()
    note_text = S01_NOTE_TEXT
    with psycopg.connect(dsn) as conn:
        topic = conn.execute(
            "SELECT topic_id FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(card_id)),
        ).fetchone()
    if topic is None:
        raise AssertionError("S01 卡片未上传云端")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Device-Id": str(sync_result["first_sync"]["device_id"]),
    }
    async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
        if existing_note_id is None:
            created = await cloud.post("/v1/notes", headers=headers, json={
                "operation_id": str(uuid.uuid4()), "note_id": str(note_id),
                "task_id": card_id, "topic_id": str(topic[0]),
                "content_text": note_text,
            })
            if created.status_code != 201:
                raise AssertionError(f"S01 原文笔记未持久提交：{created.status_code} {created.text}")
        else:
            with psycopg.connect(dsn) as conn:
                saved = conn.execute(
                    "SELECT task_id,topic_id,content_text,revision FROM growth_notes "
                    "WHERE account_id=%s AND note_id=%s",
                    (account, note_id),
                ).fetchone()
            if saved != (uuid.UUID(card_id), topic[0], note_text, 1):
                raise AssertionError("S01 安装版输入的 N1 未以原文挂在同一卡和主题")
        unavailable = await cloud.get(
            "/v1/growth/context", params={"query": "CompletableFuture"}, headers=headers,
        )
        if unavailable.status_code != 503:
            raise AssertionError("真实 ReMe 停机时应明确报告记忆不可用")
    second = GrowthLocalRepository(attempt / "device-b.sqlite")
    try:
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            pull = await connect_and_sync(second, cloud, token)
        note_row = second.connection.execute(
            "SELECT revision,payload_json FROM sync_entities WHERE entity_type='note' "
            "AND entity_id=?", (str(note_id),),
        ).fetchone()
        if (note_row is None or note_row["revision"] != 1
                or json.loads(note_row["payload_json"])["content_text"] != note_text):
            raise AssertionError("第二设备未取回 S01 用户原文笔记")
    finally:
        second.close()
    async with httpx.AsyncClient(base_url=reme_url, timeout=2, trust_env=False) as unavailable_reme:
        failed = await run_once(dsn, ReMeAdapter(unavailable_reme))
    if failed != "failed":
        raise AssertionError(f"ReMe 故障未留下失败投影：{failed}")
    with psycopg.connect(dsn) as conn:
        persisted = conn.execute(
            "SELECT revision,content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, note_id),
        ).fetchone()
    if persisted != (1, note_text):
        raise AssertionError("ReMe 故障改变了用户原文")
    workspace = attempt / "reme-workspace"
    workspace.mkdir(exist_ok=False)
    reme = start_process([
        str(REME), "start", f"config={CONFIG}", f"workspace_dir={workspace}",
        f"service.port={reme_url.rsplit(':', 1)[1]}",
    ], attempt, "reme", os.environ.copy())
    try:
        version = wait_reme(reme, reme_url)
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_jobs SET next_retry_at=now() WHERE account_id=%s "
                "AND source_type='note' AND source_id=%s", (account, note_id),
            )
        path = reme_note_path(account, note_id)
        async with httpx.AsyncClient(base_url=reme_url, timeout=25, trust_env=False) as memory:
            recovered = await run_once(dsn, ReMeAdapter(memory))
            if recovered != "succeeded":
                raise AssertionError(f"真实 ReMe 恢复后未成功投影：{recovered}")
            file = workspace / Path(path)
            if not file.is_file() or note_text not in file.read_text(encoding="utf-8"):
                raise AssertionError("真实 ReMe 文件未保留用户原文")
            indexed = await memory.post("/reindex", json={})
            indexed.raise_for_status()
            paths: list[str] = []
            for _ in range(30):
                paths = await ReMeAdapter(memory).search_paths("CompletableFuture", limit=20)
                if path in paths:
                    break
                await asyncio.sleep(1)
            if path not in paths:
                raise AssertionError("真实 ReMe 检索未命中 S01 笔记")
        async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
            context = await cloud.get(
                "/v1/growth/context", params={"query": "CompletableFuture"}, headers=headers,
            )
            context.raise_for_status()
            context_body = context.json()
            journey = await cloud.get(f"/v1/tasks/{card_id}/journey", headers=headers)
            journey.raise_for_status()
            journey_body = journey.json()
        if not (
            context_body["memory_status"] == "current"
            and any(item["note_id"] == str(note_id)
                    and item["content_text"] == note_text for item in context_body["notes"])
            and any(item["source_id"] == str(note_id)
                    and item["source_version"] == 1 for item in journey_body["memory_refs"])
            and any(item["source_id"] == str(note_id)
                    for item in journey_body["timeline"])
        ):
            raise AssertionError("S01 记忆结果未按账号、版本回源到时间线")
        question = "我对 CompletableFuture 修正过什么误解？"
        async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
            fresh_context = await cloud.get(
                "/v1/growth/context", params={"query": question}, headers=headers,
            )
            fresh_context.raise_for_status()
            memory_context = fresh_context.json()
            if not any(item["note_id"] == str(note_id)
                       for item in memory_context["notes"]):
                raise AssertionError("S01 新会话原问题没有检索到 N1 来源")
            linked_answers = {item["attempt_id"] for item in memory_context["attempts"]
                              if item.get("source_type") == "objective_answer"
                              and item.get("retrieval_basis") == "retrieved_note_same_task"}
            if not set(expected_answer_ids).issubset(linked_answers):
                raise AssertionError("S01 检索 N1 后没有回源同一卡的两道客观作答")
        local_state = attempt / "s01-local-app" / ".ahadiff"
        local_state.mkdir(parents=True)
        _copy_database(attempt / "device-a.sqlite", local_state / "growth.sqlite")
        local_token = uuid.uuid4().hex
        local_app = create_app(ServeState(state_dir=local_state, token=local_token))
        local_headers = {
            "X-AhaDiff-Token": local_token,
            "Origin": "http://127.0.0.1:8765",
        }
        access = {"cloud_url": origin, "access_token": token,
                  "account_id": str(account)}
        session_id = uuid.uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=local_app),
            base_url="http://127.0.0.1:8765", timeout=120,
            trust_env=False,
        ) as local_client:
            started_session = await local_client.post(
                "/api/growth/local/cloud/chats/start", headers=local_headers,
                json={**access, "session_id": str(session_id), "project_id": None},
            )
            if started_session.status_code != 201:
                raise AssertionError(
                    f"S01 本地入口建会话失败：{started_session.status_code} "
                    f"{started_session.text}"
                )
            sent_question = await local_client.post(
                f"/api/growth/local/cloud/chats/{session_id}/messages",
                headers=local_headers,
                json={**access, "message_id": str(uuid.uuid4()),
                      "content_text": question},
            )
            if sent_question.status_code != 201:
                raise AssertionError(
                    f"S01 本地入口发送问题失败：{sent_question.status_code} "
                    f"{sent_question.text}"
                )
            preview_response = await local_client.post(
                f"/api/growth/local/cloud/chats/{session_id}/preview",
                headers=local_headers,
                json={**access, "provider_name": "deepseek"},
            )
            if preview_response.status_code != 200:
                raise AssertionError(
                    f"S01 本地入口预览失败：{preview_response.status_code} "
                    f"{preview_response.text}"
                )
            preview = preview_response.json()
            if note_text not in preview["payload_text"] or str(note_id) not in preview["payload_text"]:
                raise AssertionError("真实模型批准载荷缺少 N1 原文或来源 ID")
            request_id = uuid.uuid4()
            assistant_id = uuid.uuid4()
            generate_body = {
                **access, "provider_name": "deepseek",
                "request_id": str(request_id), "message_id": str(assistant_id),
                "approved_payload_hash": preview["approval_hash"],
                "approved": True,
            }
            for retry in range(3):
                generated = await local_client.post(
                    f"/api/growth/local/cloud/chats/{session_id}/generate",
                    headers=local_headers, json=generate_body,
                )
                if generated.status_code != 502:
                    break
                if retry == 2:
                    break
                renewed = await local_client.post(
                    f"/api/growth/local/cloud/chats/{session_id}/preview",
                    headers=local_headers,
                    json={**access, "provider_name": "deepseek"},
                )
                if (renewed.status_code != 200
                        or renewed.json()["payload_text"] != preview["payload_text"]):
                    raise AssertionError("S01 模型失败后重新批准的载荷发生变化")
                preview = renewed.json()
                request_id = uuid.uuid4()
                assistant_id = uuid.uuid4()
                generate_body.update({
                    "request_id": str(request_id),
                    "message_id": str(assistant_id),
                    "approved_payload_hash": preview["approval_hash"],
                })
                await asyncio.sleep(2 * (retry + 1))
            if generated.status_code != 200 or generated.json()["sync_state"] != "acked":
                raise AssertionError(
                    f"S01 本地入口真实模型回复未确认：{generated.status_code} "
                    f"{generated.text}"
                )
            answer = generated.json()["message"]["content_text"]
            replay = await local_client.post(
                f"/api/growth/local/cloud/chats/{session_id}/generate",
                headers=local_headers, json=generate_body,
            )
            if (replay.status_code != 200 or replay.json()["sync_state"] != "acked"
                    or replay.json()["message"] != generated.json()["message"]):
                raise AssertionError(
                    f"S01 同请求重试未返回同一助手回复：{replay.status_code} "
                    f"{replay.text}"
                )
            changed_message = await local_client.post(
                f"/api/growth/local/cloud/chats/{session_id}/generate",
                headers=local_headers,
                json={**generate_body, "message_id": str(uuid.uuid4())},
            )
            if changed_message.status_code != 409:
                raise AssertionError("S01 同一请求 ID 改换消息 ID 未被拒绝")
            saved_chat = await local_client.post(
                f"/api/growth/local/cloud/chats/{session_id}",
                headers=local_headers, json=access,
            )
            saved_chat.raise_for_status()
        restarted_app = create_app(ServeState(state_dir=local_state, token=local_token))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted_app),
            base_url="http://127.0.0.1:8765", timeout=30,
            trust_env=False,
        ) as restarted_client:
            after_restart = await restarted_client.post(
                f"/api/growth/local/cloud/chats/{session_id}/generate",
                headers=local_headers, json=generate_body,
            )
        if (after_restart.status_code != 200
                or after_restart.json()["message"] != generated.json()["message"]):
            raise AssertionError("S01 本地服务重建后未持久返回同一助手回复")
        if not (all(word in answer.lower() for word in (
            "executor", "thencombine", "join",
        )) and any(word in answer for word in ("线程", "任务"))
                and any(word in answer for word in ("等待", "阻塞"))):
            raise AssertionError(f"真实模型未根据来源解释 S01 旧误解：{answer}")
        messages = saved_chat.json()["messages"]
        if (len(messages) != 2 or messages[0]["role"] != "user"
                or messages[1]["role"] != "assistant"
                or messages[1]["message_id"] != str(assistant_id)):
            raise AssertionError("S01 新会话没有保存唯一的一问一答")
        installed_chat: dict[str, object] | None = None
        if executable is not None:
            credentials = attempt / "s01-ui-cloud-credentials.json"
            credentials.write_text(json.dumps({
                "cloud_url": origin, "access_token": token,
                "account_id": str(account),
            }, ensure_ascii=False), encoding="utf-8")
            preparation = attempt / "s01-ui-memory-preparation.json"
            preparation.write_text(json.dumps({
                "mode": "live_observation", "account_id": str(account),
                "note_id": str(note_id), "attempt_ids": expected_answer_ids,
                "provider_name": "deepseek",
            }, ensure_ascii=False), encoding="utf-8")
            output = attempt / "s01-installed-memory-chat.json"
            ui = await asyncio.to_thread(
                subprocess.run,
                ["node", str(ROOT / "acceptance" / "scenarios" /
                             "m7_s01_chat_ui.mjs"),
                 str(executable), str(attempt / "s01-local-app"),
                 str(credentials), str(preparation), str(output)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                timeout=360, check=False,
            )
            if ui.returncode != 0:
                raise AssertionError(
                    "S01 安装版重启后真实记忆对话失败：" + ui.stderr[-2000:]
                )
            observed = json.loads(output.read_text(encoding="utf-8"))
            reply = observed.get("assistant", {}).get("content_text", "")
            if (observed["status"] != "observed_pending_audit"
                    or str(note_id) not in observed["preview"]["note_ids"]
                    or observed["preview"]["memory_status"] != "current"
                    or not ("executor" in reply.lower() and "join" in reply.lower()
                            and any(word in reply for word in ("线程", "任务")))):
                raise AssertionError("S01 安装版记忆来源或模型语义审计失败")
            with psycopg.connect(dsn) as conn:
                stored = conn.execute(
                    "SELECT role,origin,content_text FROM growth_chat_messages "
                    "WHERE account_id=%s AND session_id=%s ORDER BY created_at,message_id",
                    (account, uuid.UUID(observed["session_id"])),
                ).fetchall()
            if (len(stored) != 2 or stored[0] != ("user", "user", question)
                    or stored[1] != ("assistant", "live", reply)):
                raise AssertionError("S01 安装版会话未唯一持久化真实模型一问一答")
            installed_chat = {
                "status": "PASS", "session_id": observed["session_id"],
                "memory_note_id": str(note_id), "model_name": observed["assistant"]["model_name"],
                "ui_evidence": output.name,
            }
        return {
            "status": "PASS", "card_id": card_id, "note_id": str(note_id),
            "source_revision": 1, "second_device_pull": pull,
            "reme_version": version, "failure_outcome": failed,
            "recovery_outcome": recovered, "reme_path": path,
            "memory_status": context_body["memory_status"],
            "cloud_note_ids": [item["note_id"] for item in context_body["notes"]],
            "new_session_id": str(session_id),
            "new_question": question, "model_reply": answer,
            "model_request_id": generated.json()["message"]["provider_request_id"],
            "model_name": generated.json()["message"]["model_name"],
            "model_generation_attempts": retry + 1,
            "approved_payload_hash": preview["approval_hash"],
            "local_route": "PASS", "same_request_replay": "PASS",
            "restart_replay": "PASS", "mismatched_replay_rejected": "PASS",
            "source_note_in_prompt": True,
            "linked_objective_answer_ids": expected_answer_ids,
            "installed_chat": installed_chat,
        }
    finally:
        stop_reme(reme)


async def _verify_installed_s02_response_loss(
    source_run: Path, attempt: Path, dsn: str, origin: str,
    token: str, account: uuid.UUID, installer: Path, executable: Path,
    race_card: dict[str, object] | None = None,
) -> dict[str, object]:
    if race_card is None:
        source_cards = json.loads(
            (source_run / "full-chain-result.json").read_text(encoding="utf-8")
        )
        race_card_id = next(
            item["card_id"] for item in source_cards if item["kind"] == "request-race"
        )
    else:
        race_card_id = str(race_card["card_id"])
    with psycopg.connect(dsn) as cloud:
        row = cloud.execute(
            "SELECT topic_id FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(race_card_id)),
        ).fetchone()
    if row is None:
        raise AssertionError("S02 搜索竞态卡未发布到隔离云")
    topic_id = str(row[0])
    note_text = (
        "我以前默认先发出的请求先返回。现在用一个递增编号识别最新请求，"
        "只有它能更新界面。旧请求仍可能完成，但结果不会覆盖最新输入。"
    )
    credentials = attempt / "ui-cloud-credentials.json"
    credentials.write_text(json.dumps({
        "cloud_url": origin, "port": int(origin.rsplit(":", 1)[1]),
        "access_token": token, "account_id": str(account),
    }, ensure_ascii=False), encoding="utf-8")
    prior = attempt / "s02-ui-prior.json"
    prior.write_text(json.dumps({
        "status": "PASS", "topic_id": topic_id, "task_id": race_card_id,
        "base_text": note_text,
    }, ensure_ascii=False), encoding="utf-8")
    output = attempt / "s02-installed-response-loss.json"
    candidate_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    workspace = attempt / "s01-local-app"
    command = [
        "node", str(ROOT / "acceptance" / "scenarios" /
                    "m7_s02_lost_response_ui.mjs"),
        str(executable), str(installer), str(workspace),
        str(credentials), str(prior), str(output), candidate_hash,
    ]
    completed = await asyncio.to_thread(
        subprocess.run, command, cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", timeout=240, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "S02 当前安装版响应丢失重试失败："
            + completed.stderr[-1500:]
        )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    if evidence["status"] != "PASS" or not all(evidence["checks"].values()):
        raise AssertionError("S02 安装版响应丢失断言未全部通过")
    note_id = evidence["first_request"]["note_id"]
    with psycopg.connect(dsn) as cloud:
        notes = cloud.execute(
            "SELECT revision, content_text, topic_id FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(note_id)),
        ).fetchall()
    if notes != [(1, note_text, uuid.UUID(topic_id))]:
        raise AssertionError("S02 重试后云端目标笔记不是唯一的原文 revision 1")
    second = GrowthLocalRepository(attempt / "device-b.sqlite")
    try:
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            pulled = await connect_and_sync(second, cloud, token)
        cached = second.connection.execute(
            "SELECT revision,payload_json FROM sync_entities "
            "WHERE entity_type='note' AND entity_id=?", (note_id,),
        ).fetchone()
        if (cached is None or cached["revision"] != 1
                or json.loads(cached["payload_json"])["content_text"] != note_text):
            raise AssertionError("S02 第二设备未取回安装版创建的唯一 N-base 原文")
    finally:
        second.close()
    return {
        "status": "PASS", "candidate_sha256": candidate_hash,
        "installed_exe_sha256": evidence["installed_exe_sha256"],
        "race_card_id": race_card_id, "topic_id": topic_id,
        "note_id": note_id, "note_text": note_text,
        "same_operation_id": evidence["checks"]["identical_operation_and_note_id"],
        "same_server_result": evidence["checks"]["identical_server_result"],
        "second_device_note_revision": cached["revision"],
        "second_device_pull": pulled,
        "ui_evidence": output.name,
    }


async def _verify_installed_s02_knowledge_revision(
    source_run: Path, attempt: Path, dsn: str, account: uuid.UUID,
    executable: Path,
    race_card: dict[str, object] | None = None,
) -> dict[str, object]:
    if race_card is None:
        source_cards = json.loads(
            (source_run / "full-chain-result.json").read_text(encoding="utf-8")
        )
        race = next(item for item in source_cards if item["kind"] == "request-race")
        workspace = attempt / "s01-local-app"
    else:
        race = race_card
        workspace = Path(str(race_card["workspace"]))
    card_id = race["card_id"]
    output = attempt / "s02-installed-knowledge-revision.json"
    completed = await asyncio.to_thread(
        subprocess.run,
        ["node", str(ROOT / "acceptance" / "scenarios" /
                     "m7_s02_knowledge_revision_ui.mjs"),
         str(executable), str(workspace),
         str(attempt / "ui-cloud-credentials.json"), card_id,
         race["snapshot_id"], str(output)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=600, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "S02 安装版知识卡修订、暂停续学或 Wiki 确认失败："
            + completed.stderr[-2000:]
        )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    if evidence["status"] != "PASS" or not all(evidence["checks"].values()):
        raise AssertionError("S02 安装版知识卡修订断言未全部通过")
    with psycopg.connect(dsn) as cloud:
        remote = cloud.execute(
            "SELECT revision,bundle FROM growth_knowledge_bundles "
            "WHERE account_id=%s AND card_id=%s",
            (account, uuid.UUID(card_id)),
        ).fetchone()
        excerpts = cloud.execute(
            "SELECT start_line,end_line,content_text FROM growth_source_excerpts "
            "WHERE account_id=%s AND snapshot_id=%s",
            (account, uuid.UUID(race["snapshot_id"])),
        ).fetchall()
    if remote is None:
        raise AssertionError("S02 新版知识卡未到达云端")
    bundle = remote[1]
    tables = bundle["tables"]
    materials = tables["card_learning_materials"]
    answers = tables["card_objective_answers"]
    wiki = tables["knowledge_wiki_articles"]
    if (int(tables["knowledge_cards"][0]["card_version"]) != evidence["after_version"]
            or not any(int(item["card_version"]) == evidence["after_version"]
                       for item in materials)
            or len([item for item in answers
                    if item["question_id"] in evidence["question_ids"]]) != 2
            or not any(item["relative_path"] == evidence["wiki_path"] for item in wiki)
            or not any(start == 1 and end == 10 and "latestRequest" in content
                       for start, end, content in excerpts)):
        raise AssertionError("S02 云端题包、两次作答、Wiki 或批准源码与安装版不一致")
    return {
        "status": "PASS", "card_id": card_id,
        "card_version": evidence["after_version"],
        "bundle_revision": remote[0],
        "learning_stage": evidence["after_stage"],
        "wiki_path": evidence["wiki_path"],
        "approved_source_excerpt_count": len(excerpts),
        "ui_evidence": output.name,
    }


async def _verify_installed_s01_first_learning(
    source_run: Path, attempt: Path, executable: Path,
) -> dict[str, object]:
    """Start with a clean clone, then drive the installed Git-to-Wiki flow."""
    manifest = json.loads((source_run / "fixture-manifest.json").read_text(encoding="utf-8"))
    commit_sha = next(
        str(group["commits"][0]) for group in manifest["groups"]
        if group["kind"] == "concurrency"
    )
    clone = attempt / "s01-ui-fresh-repo"
    cloned = await asyncio.to_thread(
        subprocess.run,
        ["git", "clone", "--no-local", "--quiet", "--",
         str(source_run / "fixture" / "repo"), str(clone)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=90, check=False,
    )
    if cloned.returncode != 0:
        raise AssertionError("S01 全新 Git 仓库克隆失败：" + cloned.stderr[-1000:])
    vault = attempt / "s01-ui-vault"
    output = attempt / "s01-installed-first-learning.json"
    completed = await asyncio.to_thread(
        subprocess.run,
        ["node", str(ROOT / "acceptance" / "scenarios" /
                     "m7_s01_knowledge_first_ui.mjs"),
         str(executable), str(clone), commit_sha, str(vault), str(output)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=600, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "S01 安装版首次 Git→候选→成卡→学习→Wiki 失败：" + completed.stderr[-2000:]
        )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    if evidence["status"] != "PASS" or not all(evidence["checks"].values()):
        raise AssertionError("S01 安装版首次知识链的界面断言未全部通过")
    ledger = GrowthLocalRepository(clone / ".ahadiff" / "growth.sqlite")
    try:
        card = card_detail(ledger, evidence["card_id"])
        snapshot = ledger.connection.execute(
            "SELECT head_sha FROM snapshots WHERE snapshot_id=?",
            (evidence["snapshot_id"],),
        ).fetchone()
        analysis = ledger.connection.execute(
            "SELECT mode,status FROM analysis_runs WHERE snapshot_id=?",
            (evidence["snapshot_id"],),
        ).fetchall()
        answers = ledger.connection.execute(
            "SELECT COUNT(*) FROM card_objective_answers WHERE card_id=?",
            (evidence["card_id"],),
        ).fetchone()[0]
        reads = ledger.connection.execute(
            "SELECT COUNT(*) FROM card_explanation_reads WHERE card_id=?",
            (evidence["card_id"],),
        ).fetchone()[0]
        article = ledger.connection.execute(
            "SELECT relative_path,file_hash FROM knowledge_wiki_articles "
            "WHERE concept_id=?", (card["card"]["concept_id"],),
        ).fetchone()
        if (snapshot is None or snapshot["head_sha"] != commit_sha
                or not any(row["mode"] == "live" and row["status"] == "succeeded"
                           for row in analysis)
                or not any(source["commit_sha"] == commit_sha
                           for source in card["sources"])
                or card["learning"]["stage"] != "completed"
                or len(card["questions"]) != 2
                or any(len(question["options"]) != 4 for question in card["questions"])
                or answers != 2 or reads != 2 or article is None):
            raise AssertionError("S01 本机 SQLite 独立审计与安装版界面结果不一致")
        wiki = (vault / article["relative_path"]).resolve(strict=True)
        if (not wiki.is_relative_to(vault.resolve())
                or hashlib.sha256(wiki.read_bytes()).hexdigest() != article["file_hash"]
                or article["relative_path"] != evidence["wiki_relative_path"]):
            raise AssertionError("S01 Obsidian 原子写入或文件哈希与本机账本不一致")
        return {
            "status": "PASS", "commit_sha": commit_sha,
            "snapshot_id": evidence["snapshot_id"],
            "card_id": evidence["card_id"],
            "question_count": len(card["questions"]),
            "answer_count": answers,
            "explanation_read_count": reads,
            "wiki_relative_path": article["relative_path"],
            "wiki_sha256": article["file_hash"],
            "ui_evidence": output.name,
        }
    finally:
        ledger.close()


async def _verify_installed_s02_first_learning(
    source_run: Path, attempt: Path, origin: str, token: str,
    executable: Path,
) -> dict[str, object]:
    """Pause the new race card before Q1, continue on B, publish Wiki on A."""
    manifest = json.loads((source_run / "fixture-manifest.json").read_text(encoding="utf-8"))
    commit_sha = next(
        str(group["commits"][0]) for group in manifest["groups"]
        if group["kind"] == "request-race"
    )
    clone = attempt / "s02-ui-fresh-repo"
    cloned = await asyncio.to_thread(
        subprocess.run,
        ["git", "clone", "--no-local", "--quiet", "--",
         str(source_run / "fixture" / "repo"), str(clone)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=90, check=False,
    )
    if cloned.returncode != 0:
        raise AssertionError("S02 全新 Git 仓库克隆失败：" + cloned.stderr[-1000:])
    vault = attempt / "s02-ui-vault"
    output = attempt / "s02-installed-first-learning.json"
    completed = await asyncio.to_thread(
        subprocess.run,
        ["node", str(ROOT / "acceptance" / "scenarios" /
                     "m7_s01_knowledge_first_ui.mjs"),
         str(executable), str(clone), commit_sha, str(vault), str(output),
         "request-race"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=600, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "S02 安装版首次 Git→候选→成卡→选项草稿失败：" + completed.stderr[-2000:]
        )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    if evidence["status"] != "PASS" or not all(evidence["checks"].values()):
        raise AssertionError("S02 安装版首次知识链的界面断言未全部通过")
    first = GrowthLocalRepository(clone / ".ahadiff" / "growth.sqlite")
    second = GrowthLocalRepository(attempt / "device-b.sqlite")
    try:
        card_id = evidence["card_id"]
        detail = card_detail(first, card_id)
        snapshot = first.connection.execute(
            "SELECT head_sha FROM snapshots WHERE snapshot_id=?",
            (evidence["snapshot_id"],),
        ).fetchone()
        analysis = first.connection.execute(
            "SELECT mode,status FROM analysis_runs WHERE snapshot_id=?",
            (evidence["snapshot_id"],),
        ).fetchall()
        answers = first.connection.execute(
            "SELECT COUNT(*) FROM card_objective_answers WHERE card_id=?", (card_id,),
        ).fetchone()[0]
        reads = first.connection.execute(
            "SELECT COUNT(*) FROM card_explanation_reads WHERE card_id=?", (card_id,),
        ).fetchone()[0]
        understanding = next(question for question in detail["questions"]
                             if question["kind"] == "understanding")
        if (snapshot is None or snapshot[0] != commit_sha
                or not any(row["mode"] == "live" and row["status"] == "succeeded"
                           for row in analysis)
                or detail["learning"]["stage"] != "understanding"
                or len(detail["questions"]) != 2 or answers != 0 or reads != 0
                or detail["learning"]["option_drafts"].get(evidence["paused_question_id"])
                   != understanding["options"][0]["id"]
                or not any(source["commit_sha"] == commit_sha
                           for source in detail["sources"])):
            raise AssertionError("S02 安装版首次卡的真实提交、模型与学习记录不一致")
        topic = first.connection.execute(
            "SELECT topic_id FROM growth_tasks WHERE task_id=?", (card_id,),
        ).fetchone()
        project_ids = next(item for item in cards(first)
                           if item["card_id"] == card_id)["project_ids"]
        if topic is None or not topic[0] or len(project_ids) != 1:
            raise AssertionError("S02 首次卡缺少正式主题或唯一来源项目")
        plan = publication_plan(first, project_ids[0])
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            first_sync = await connect_and_sync(
                first, cloud, token, publish_project_id=project_ids[0],
                approved_digest=plan["digest"],
            )
            second_sync = await connect_and_sync(second, cloud, token)
        pulled = card_detail(second, card_id)
        if (first_sync["knowledge_uploaded"] < 1
                or second_sync["knowledge_pulled"] < 1
                or pulled["card"]["card_id"] != card_id
                or pulled["learning"]["stage"] != "understanding"
                or pulled["learning"]["option_drafts"].get(evidence["paused_question_id"])
                   != understanding["options"][0]["id"]):
            raise AssertionError("S02 安装版首次卡没有以同一身份跨端发布和取回")
    finally:
        first.close()
        second.close()
    credentials = attempt / "ui-cloud-credentials.json"
    credentials.write_text(json.dumps({
        "cloud_url": origin, "port": int(origin.rsplit(":", 1)[1]),
        "access_token": token, "account_id": first_sync["account_id"],
    }, ensure_ascii=False), encoding="utf-8")
    second_workspace = attempt / "s02-first-device-b"
    second_state = second_workspace / ".ahadiff"
    second_state.mkdir(parents=True)
    _copy_database(attempt / "device-b.sqlite", second_state / "growth.sqlite")
    continuation = ROOT / "acceptance" / "scenarios" / "m7_s02_first_continue_ui.mjs"

    async def ui_phase(phase: str, workspace: Path) -> dict[str, object]:
        phase_output = attempt / f"s02-first-{phase}.json"
        result = await asyncio.to_thread(
            subprocess.run,
            ["node", str(continuation), phase, str(executable), str(workspace),
             str(credentials), card_id, str(vault), str(phase_output)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=360, check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"S02 安装版首次 {phase} 失败：{result.stderr[-1800:]}")
        observed = json.loads(phase_output.read_text(encoding="utf-8"))
        if observed["status"] != "PASS" or not all(observed["checks"].values()):
            raise AssertionError(f"S02 安装版首次 {phase} UI 断言未通过")
        return observed

    continued = await ui_phase("resume", second_workspace)
    _copy_database(second_state / "growth.sqlite", attempt / "device-b.sqlite")
    published = await ui_phase("publish", clone)
    observed = await ui_phase("read", second_workspace)
    _copy_database(second_state / "growth.sqlite", attempt / "device-b.sqlite")
    if published["wiki_relative_path"] != observed["wiki_relative_path"]:
        raise AssertionError("S02 第二端未回读 A 人工确认的原 Wiki 路径")
    with GrowthLocalRepository(clone / ".ahadiff" / "growth.sqlite") as first:
        after = card_detail(first, card_id)
        article = first.connection.execute(
            "SELECT relative_path,file_hash FROM knowledge_wiki_articles "
            "WHERE concept_id=?", (after["card"]["concept_id"],),
        ).fetchone()
    with GrowthLocalRepository(attempt / "device-b.sqlite") as second:
        remote = card_detail(second, card_id)
    if (after["learning"]["stage"] != "completed"
            or remote["learning"]["stage"] != "completed"
            or not article or article["relative_path"] != published["wiki_relative_path"]
            or any(not after["learning"]["answers"][kind]["explanation_confirmed"]
                   for kind in ("understanding", "prediction"))
            or len(remote["questions"]) != 2):
        raise AssertionError("S02 B 端续学与 A 端确认 Wiki 后的双端账本不一致")
    wiki_file = (vault / article["relative_path"]).resolve(strict=True)
    if (not wiki_file.is_relative_to(vault.resolve())
            or hashlib.sha256(wiki_file.read_bytes()).hexdigest() != article["file_hash"]):
        raise AssertionError("S02 首次 Wiki 路径或文件哈希与账本不一致")
    return {
        "status": "PASS", "card_id": card_id,
        "snapshot_id": evidence["snapshot_id"],
        "topic_id": topic[0], "commit_sha": commit_sha,
        "workspace": str(clone), "first_sync": first_sync,
        "second_sync": second_sync,
        "wiki_relative_path": article["relative_path"],
        "ui_evidence": [output.name, "s02-first-resume.json",
                        "s02-first-publish.json", "s02-first-read.json"],
        "draft_restored_on_second_device": continued["checks"]["draft_restored_on_second_device"],
    }


async def _verify_installed_s01_same_card_memory(
    source_run: Path, attempt: Path, dsn: str, origin: str,
    reme_url: str, token: str, account: uuid.UUID, executable: Path,
    first_learning: dict[str, object],
) -> dict[str, object]:
    """Publish the exact UI-learned card, then resume its N1 memory story."""
    card_id = str(first_learning["card_id"])
    chain = attempt / "s01-ui-same-card-cloud"
    chain.mkdir()
    _copy_database(
        attempt / "s01-ui-fresh-repo" / ".ahadiff" / "growth.sqlite",
        chain / "device-a.sqlite",
    )
    first = GrowthLocalRepository(chain / "device-a.sqlite")
    second = GrowthLocalRepository(chain / "device-b.sqlite")
    try:
        learned = next((item for item in cards(first) if item["card_id"] == card_id), None)
        if learned is None or learned["learning_stage"] != "completed":
            raise AssertionError("S01 安装版学完的原卡未进入云端发布账本")
        project_ids = learned["project_ids"]
        if len(project_ids) != 1:
            raise AssertionError("S01 原卡来源项目身份不唯一")
        plan = publication_plan(first, project_ids[0])
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            first_sync = await connect_and_sync(
                first, cloud, token,
                publish_project_id=project_ids[0],
                approved_digest=plan["digest"],
            )
            second_sync = await connect_and_sync(second, cloud, token)
        pulled = card_detail(second, card_id)
        if (first_sync["knowledge_uploaded"] < 1
                or second_sync["knowledge_pulled"] < 1
                or pulled["card"]["card_id"] != card_id
                or pulled["learning"]["stage"] != "completed"
                or len(pulled["questions"]) != 2):
            raise AssertionError("S01 安装版原卡未以同一身份在云端和第二设备续接")
    finally:
        first.close()
        second.close()
    with sqlite3.connect(chain / "device-a.sqlite") as connection:
        topic = connection.execute(
            "SELECT topic_id FROM growth_tasks WHERE task_id=?", (card_id,),
        ).fetchone()
    if topic is None or not topic[0]:
        raise AssertionError("S01 安装版原卡没有可写 N1 的正式主题")
    credentials = chain / "ui-cloud-credentials.json"
    credentials.write_text(json.dumps({
        "cloud_url": origin, "access_token": token,
        "account_id": str(account),
    }, ensure_ascii=False), encoding="utf-8")
    preparation = chain / "s01-note-preparation.json"
    note_input: dict[str, str] = {
        "card_id": card_id, "topic_id": topic[0],
        "note_text": S01_NOTE_TEXT,
    }
    preparation.write_text(json.dumps(note_input, ensure_ascii=False), encoding="utf-8")
    script = ROOT / "acceptance" / "scenarios" / "m7_s01_same_card_note_ui.mjs"

    async def note_ui(phase: str, workspace: Path) -> dict[str, object]:
        output = chain / f"s01-installed-note-{phase}.json"
        completed = await asyncio.to_thread(
            subprocess.run,
            ["node", str(script), phase, str(executable), str(workspace),
             str(credentials), str(preparation), str(output)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=240, check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"S01 安装版 N1 {phase} 失败：{completed.stderr[-1800:]}"
            )
        observed = json.loads(output.read_text(encoding="utf-8"))
        if observed["status"] != "PASS" or not all(observed["checks"].values()):
            raise AssertionError(f"S01 安装版 N1 {phase} UI 断言未通过")
        return observed

    first_workspace = attempt / "s01-ui-fresh-repo"
    _copy_database(chain / "device-a.sqlite", first_workspace / ".ahadiff" / "growth.sqlite")
    created_note = await note_ui("create", first_workspace)
    note_id = str(created_note["note_id"])
    _copy_database(first_workspace / ".ahadiff" / "growth.sqlite", chain / "device-a.sqlite")
    note_input["note_id"] = note_id
    preparation.write_text(json.dumps(note_input, ensure_ascii=False), encoding="utf-8")
    second_workspace = chain / "device-b-workspace"
    second_state = second_workspace / ".ahadiff"
    second_state.mkdir(parents=True)
    _copy_database(chain / "device-b.sqlite", second_state / "growth.sqlite")
    read_note = await note_ui("read", second_workspace)
    if read_note["note_id"] != note_id:
        raise AssertionError("S01 两端安装版 UI 阅读的不是同一 N1")
    _copy_database(second_state / "growth.sqlite", chain / "device-b.sqlite")
    memory = await _verify_s01_memory(
        source_run, chain, dsn, origin, reme_url, token, account,
        {"first_sync": first_sync}, executable, card_id_override=card_id,
        existing_note_id=note_id,
    )
    with psycopg.connect(dsn) as cloud:
        bundle = cloud.execute(
            "SELECT bundle FROM growth_knowledge_bundles "
            "WHERE account_id=%s AND card_id=%s",
            (account, uuid.UUID(card_id)),
        ).fetchone()
        note = cloud.execute(
            "SELECT task_id,content_text FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s",
            (account, uuid.UUID(memory["note_id"])),
        ).fetchone()
    if (bundle is None or note is None or note[0] != uuid.UUID(card_id)
            or memory["card_id"] != card_id):
        raise AssertionError("S01 知识卡、N1 笔记与记忆问答并非同一云端 card ID")
    return {
        "status": "PASS", "card_id": card_id,
        "source_snapshot_id": first_learning["snapshot_id"],
        "first_sync": first_sync,
        "second_sync": second_sync,
        "note_id": memory["note_id"],
        "installed_note_create": "s01-installed-note-create.json",
        "installed_note_read": "s01-installed-note-read.json",
        "memory_status": memory["memory_status"],
        "installed_chat": memory["installed_chat"],
        "same_cloud_card_id": True,
    }


async def _verify_installed_s04_topic_maintenance(
    source_run: Path, attempt: Path, dsn: str, origin: str,
    token: str, account: uuid.UUID, executable: Path,
    second_device_id: str, auth_card_id: str,
) -> dict[str, object]:
    with psycopg.connect(dsn) as cloud:
        task = cloud.execute(
            "SELECT topic_id, question FROM growth_tasks "
            "WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(auth_card_id)),
        ).fetchone()
    if task is None or not any(term in task[1] for term in (
        "ownerId", "canEdit", "所有者", "所有权", "有权", "权限", "授权",
        "Alice", "Bob",
    )):
        raise AssertionError("S04 授权卡未发布到隔离云，或真实题目缺少资源权限判断")
    topic_id = str(task[0])
    output = attempt / "s04-installed-topic-maintenance.json"
    completed = await asyncio.to_thread(
        subprocess.run,
        ["node", str(ROOT / "acceptance" / "scenarios" /
                      "m7_s04_topic_maintenance_ui.mjs"),
         str(executable), str(attempt / "s01-local-app"),
         str(attempt / "ui-cloud-credentials.json"), topic_id,
         auth_card_id, str(output)],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=240, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "S04 当前安装版主题维护失败：" + completed.stderr[-1800:]
        )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    if evidence["status"] != "PASS" or not all(evidence["checks"].values()):
        raise AssertionError("S04 安装版主题维护断言未全部通过")
    old_topic = uuid.UUID(evidence["old_topic_id"])
    old_note = uuid.UUID(evidence["old_note_id"])
    new_note = uuid.UUID(evidence["new_note_id"])
    auth_topic = uuid.UUID(evidence["auth_topic_id"])
    permission_topic = uuid.UUID(evidence["permission_topic_id"])
    answer_id = uuid.UUID(evidence["answer_attempt_id"])
    with psycopg.connect(dsn) as cloud:
        alias = cloud.execute(
            "SELECT canonical_topic_id FROM growth_topic_aliases "
            "WHERE account_id=%s AND alias_topic_id=%s",
            (account, old_topic),
        ).fetchone()
        notes = cloud.execute(
            "SELECT note_id, content_text, task_id, topic_id FROM growth_notes "
            "WHERE account_id=%s AND note_id=ANY(%s) ORDER BY note_id",
            (account, [old_note, new_note]),
        ).fetchall()
        links = cloud.execute(
            "SELECT topic_id, entity_type, entity_id FROM growth_topic_links "
            "WHERE account_id=%s AND topic_id=ANY(%s)",
            (account, [auth_topic, permission_topic]),
        ).fetchall()
        statuses = cloud.execute(
            "SELECT topic_id, status FROM growth_topics WHERE account_id=%s "
            "AND topic_id=ANY(%s)",
            (account, [auth_topic, permission_topic]),
        ).fetchall()
        answer = cloud.execute(
            "SELECT task_id, answer_text FROM growth_learning_attempts "
            "WHERE account_id=%s AND attempt_id=%s",
            (account, answer_id),
        ).fetchone()
    by_note = {row[0]: row for row in notes}
    old_text = "登录成功说明应用识别了用户身份。"
    new_text = (
        "我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，"
        "还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了"
        "所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。"
    )
    if (alias is None or alias[0] != uuid.UUID(topic_id)
            or by_note.get(old_note, [None, None])[1] != old_text
            or by_note.get(new_note, [None, None])[1] != new_text
            or by_note[new_note][2] != uuid.UUID(auth_card_id)
            or answer is None or answer[0] != uuid.UUID(auth_card_id)
            or "403" not in answer[1] or "ownerId" not in answer[1]
            or {row[0]: row[1] for row in statuses} != {
                auth_topic: "active", permission_topic: "active"}
            or (auth_topic, "note", old_note) not in links
            or (permission_topic, "note", new_note) not in links
            or (permission_topic, "task", uuid.UUID(auth_card_id)) not in links):
        raise AssertionError("S04 PostgreSQL 原文、别名、显式关联或归档恢复不一致")
    async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as client:
        headers = {
            "Authorization": f"Bearer {token}", "X-Device-Id": second_device_id,
        }
        observed = await client.get(f"/v1/topics/{permission_topic}", headers=headers)
        observed.raise_for_status()
        detail = observed.json()
        if (detail["topic"]["status"] != "active"
                or not any(n["note_id"] == str(new_note) for n in detail["notes"])
                or not any(t["task_id"] == auth_card_id for t in detail["tasks"])):
            raise AssertionError("S04 第二设备未取回资源授权子主题的笔记和卡片")
    return {
        "status": "PASS", "auth_card_id": auth_card_id,
        "old_topic_id": str(old_topic), "new_topic_id": topic_id,
        "old_note_id": str(old_note), "new_note_id": str(new_note),
        "answer_attempt_id": str(answer_id),
        "auth_topic_id": str(auth_topic),
        "permission_topic_id": str(permission_topic),
        "second_device_topic_read": "PASS", "postgres_links": "PASS",
        "ui_evidence": output.name,
    }


async def _verify_installed_s02_note_conflict(
    attempt: Path, dsn: str, account: uuid.UUID,
    installer: Path, executable: Path,
    process: subprocess.Popen[bytes], issuer: str, jwks_url: str,
    cloud_port: int, reme_url: str, first_device_id: str,
) -> tuple[subprocess.Popen[bytes], dict[str, object]]:
    lost = json.loads((attempt / "s02-installed-response-loss.json").read_text(
        encoding="utf-8"
    ))
    note_id = lost["first_request"]["note_id"]
    source = json.loads((attempt / "s02-ui-prior.json").read_text(
        encoding="utf-8"
    ))
    initial = attempt / "s02-conflict-prior.json"
    initial.write_text(json.dumps({
        "status": "PASS", "topic_id": lost["topic_id"],
        "task_id": source["task_id"], "note_id": note_id,
        "device_b_id": first_device_id, "operation_ids": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    script = ROOT / "acceptance" / "scenarios" / "m7_s02_conflict_ui.mjs"
    credentials = attempt / "ui-cloud-credentials.json"
    candidate_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    first_workspace = attempt / "s01-local-app"
    second_workspace = attempt / "s02-conflict-device-b"
    evidence: dict[str, dict[str, object]] = {}

    async def phase(name: str, workspace: Path, previous: Path) -> Path:
        output = attempt / f"s02-conflict-{name}.json"
        finished = await asyncio.to_thread(
            subprocess.run,
            ["node", str(script), name, str(executable), str(installer),
             str(workspace), str(credentials), str(previous), str(output),
             candidate_hash],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=240, check=False,
        )
        if finished.returncode != 0:
            raise AssertionError(
                f"S02 安装版冲突 {name} 失败：" + finished.stderr[-1800:]
            )
        item = json.loads(output.read_text(encoding="utf-8"))
        if item["status"] != "PASS" or not all(item["checks"].values()):
            raise AssertionError(f"S02 安装版冲突 {name} 断言未通过")
        evidence[name] = item
        return output

    bootstrapped = await phase("bootstrap", second_workspace, initial)
    stop(process)
    offline = await phase("offline-edit", second_workspace, bootstrapped)
    resumed: subprocess.Popen[bytes] | None = None
    try:
        resumed = _launch_cloud(
            attempt, dsn, issuer, jwks_url, cloud_port, reme_url,
        )
        online = await phase("online-edit", first_workspace, offline)
        merged = await phase("conflict-merge", second_workspace, online)
        await phase("verify", first_workspace, merged)
        with psycopg.connect(dsn) as cloud:
            note = cloud.execute(
                "SELECT revision,content_text,deleted_at FROM growth_notes "
                "WHERE account_id=%s AND note_id=%s",
                (account, uuid.UUID(note_id)),
            ).fetchone()
            revisions = cloud.execute(
                "SELECT revision,content_text FROM growth_note_revisions "
                "WHERE account_id=%s AND note_id=%s ORDER BY revision",
                (account, uuid.UUID(note_id)),
            ).fetchall()
            conflicts = cloud.execute(
                "SELECT count(*),count(resolved_revision) FROM growth_note_conflicts "
                "WHERE account_id=%s AND note_id=%s",
                (account, uuid.UUID(note_id)),
            ).fetchone()
        if (note is None or note[0] != 3 or note[1] != evidence["verify"]["merged_text"]
                or note[2] is not None or len(revisions) != 3
                or [row[0] for row in revisions] != [1, 2, 3]
                or revisions[0][1] != lost["text"]
                or revisions[1][1] != evidence["online-edit"]["online_text"]
                or revisions[2][1] != evidence["verify"]["merged_text"]
                or conflicts != (1, 1)):
            raise AssertionError("S02 云端笔记三版本、显式冲突或正文不一致")
        assert resumed is not None
        return resumed, {
            "status": "PASS", "note_id": note_id, "revision": 3,
            "first_device_id": first_device_id,
            "second_device_id": evidence["bootstrap"]["device_a_id"],
            "cloud_unreachable_during_offline_edit": True,
            "resolved_conflict_count": conflicts[1],
            "ui_evidence": [f"s02-conflict-{name}.json" for name in evidence],
        }
    except BaseException:
        stop(resumed)
        raise


async def _verify_installed_s03_java_cache(
    attempt: Path, dsn: str, origin: str, reme_url: str,
    token: str, account: uuid.UUID, executable: Path,
) -> dict[str, object]:
    """Run one real Java cache story from worktree capture through recovered memory."""
    repo = attempt / "s03-java-repo"
    repo.mkdir()
    fixture = FIXTURES["S03"]
    fixture_audit: dict[str, object] = {"status": "running", "checks": {}}
    audit_path = attempt / "s03-java-chain.json"

    async def command(label: str, args: list[str], timeout: int = 300) -> str:
        completed = await asyncio.to_thread(
            subprocess.run, args, cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"S03 {label} 失败：{(completed.stderr or completed.stdout)[-1800:]}"
            )
        return completed.stdout.strip()

    async def git(*args: str) -> str:
        return await command("Git", ["git", "-C", str(repo), *args], 60)

    await git("init", "-b", "main")
    await git("config", "user.name", "情境验证")
    await git("config", "user.email", "scenario@example.invalid")
    await git("config", "core.autocrlf", "false")
    (repo / ".gitignore").write_text(".ahadiff/\n.codegraph/\n", encoding="utf-8")
    for name, body in fixture["baseline"].items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
    await git("add", ".")
    await git("commit", "-m", fixture["commit"])
    base_sha = await git("rev-parse", "HEAD")
    await git("switch", "-c", fixture["branch"])
    probe = attempt / "Probe.java"
    probe.write_text("""package demo.orders;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
public final class Probe {
    public static void main(String[] args) {
        AtomicInteger calls = new AtomicInteger();
        AtomicLong time = new AtomicLong();
        PricingGateway gateway = sku -> { calls.incrementAndGet(); return 1299; };
        OrderController controller = new OrderController(new OrderService(gateway, time::get));
        int first = controller.quote("SKU-1", 2);
        time.set(500);
        int second = controller.quote("SKU-1", 2);
        time.set(1000);
        int third = controller.quote("SKU-1", 2);
        System.out.println(first + "," + second + "," + third + "|" + calls.get());
    }
}
""", encoding="utf-8")

    async def java_probe(folder: str) -> str:
        classes = attempt / folder
        classes.mkdir()
        sources = [str(item) for item in (repo / "src/demo/orders").glob("*.java")]
        await command("Java 编译", ["javac", "-encoding", "UTF-8", "-d",
                                     str(classes), *sources, str(probe)])
        return await command("Java Probe", ["java", "-cp", str(classes),
                                           "demo.orders.Probe"])

    baseline = await java_probe("s03-baseline-classes")
    for name, body in fixture["next"].items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
    changed = await java_probe("s03-changed-classes")
    status = await git("status", "--porcelain")
    fixture_audit["checks"]["real_java_behavior"] = (
        baseline == "2598,2598,2598|3" and changed == "2598,2598,2598|2"
        and "PriceCache.java" in status and "OrderService.java" in status
    )
    if not fixture_audit["checks"]["real_java_behavior"]:
        raise AssertionError("S03 Java 基线、新增文件或惰性缓存 Probe 不符")
    fixture_audit.update({"base_sha": base_sha, "baseline_probe": baseline,
                          "changed_probe": changed, "uncommitted_status": status})
    audit_path.write_text(json.dumps(fixture_audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    setup_path = attempt / "s03-installed-setup.json"
    capture_path = attempt / "s03-installed-capture.json"
    driver = str(ROOT / "acceptance/scenarios/m7_windows_electron.mjs")
    await command("安装版接入 Java 项目", ["node", driver, "setup", str(executable),
                                str(repo), base_sha, str(setup_path),
                                "order-demo", "价格缓存"], 180)
    await command("安装版捕获新增 Java 源码", ["node", driver, "capture", str(executable),
                                   str(repo), base_sha, str(capture_path),
                                   "PriceCache.java",
                                   "OrderController.quote -> OrderService.quote -> PriceCache.get"], 300)
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    if (capture["feature_id"] != setup["feature_id"]
            or capture["selected_untracked"] != "PriceCache.java"):
        raise AssertionError("S03 安装版捕获未纳入同一 Feature 和新增缓存文件")
    old_snapshot = capture["snapshot_id"]
    code_path = attempt / "s03-artifacts" / "code-index.json"
    code_path.parent.mkdir()
    await command("同快照 CodeGraph 索引", [sys.executable,
        str(ROOT / "acceptance/scenarios/m7_s03_index_evidence.py"),
        str(repo), base_sha, old_snapshot, str(code_path)])
    code = json.loads(code_path.read_text(encoding="utf-8"))
    if (code["snapshot_id"] != old_snapshot or not code["status"].get("initialized")
            or "src/demo/orders/PriceCache.java" not in code["indexed_files"]):
        raise AssertionError("S03 CodeGraph 没有索引真实新文件")
    credentials = attempt / "s03-cloud-credentials.json"
    credentials.write_text(json.dumps({"port": int(origin.rsplit(":", 1)[1]),
        "cloud_url": origin, "account_id": str(account), "access_token": token},
        ensure_ascii=False), encoding="utf-8")
    module_path = attempt / "s03-installed-module.json"
    await command("安装版模块锁定", ["node",
        str(ROOT / "acceptance/scenarios/m7_s03_local_cloud_ui.mjs"),
        str(executable), str(repo), str(credentials), str(module_path)], 360)
    module = json.loads(module_path.read_text(encoding="utf-8"))
    if module["snapshot_id"] != old_snapshot or not module["module_id"]:
        raise AssertionError("S03 安装版没有锁定旧快照的订单计价模块")
    first_path = attempt / "s03-installed-first-learning.json"
    vault = attempt / "s03-vault"
    shutil.copytree(attempt.parent / "obsidian-vault", vault)
    await command("安装版 Java 缓存首次成卡学习", ["node",
        str(ROOT / "acceptance/scenarios/m7_s03_knowledge_first_ui.mjs"),
        str(executable), str(repo), old_snapshot, module["module_id"],
        str(vault), str(first_path)], 660)
    first = json.loads(first_path.read_text(encoding="utf-8"))
    if first["status"] != "PASS" or not all(first["checks"].values()):
        raise AssertionError("S03 首次知识学习 UI 断言未通过")
    card_id = first["card_id"]
    with GrowthLocalRepository(repo / ".ahadiff/growth.sqlite") as ledger:
        before = card_detail(ledger, card_id)
        old_ref = ledger.connection.execute(
            "SELECT source_ref_id,blob_hash,content FROM source_refs "
            "WHERE snapshot_id=? AND relative_path='src/demo/orders/PriceCache.java'",
            (old_snapshot,),
        ).fetchone()
        if (not old_ref or hashlib.sha256(old_ref["content"]).hexdigest() != old_ref["blob_hash"]
                or before["learning"]["stage"] != "completed"
                or len(before["questions"]) != 2):
            raise AssertionError("S03 旧卡来源、客观题或学习状态不完整")
        project_id = setup["project_id"]
        plan = publication_plan(ledger, project_id)
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            published = await connect_and_sync(
                ledger, cloud, token, publish_project_id=project_id,
                approved_digest=plan["digest"],
            )
        if published["knowledge_uploaded"] < 1:
            raise AssertionError("S03 首次卡未发布到成长云")
        topic = ledger.connection.execute(
            "SELECT topic_id FROM growth_tasks WHERE task_id=?", (card_id,),
        ).fetchone()
        if not topic or not topic[0]:
            raise AssertionError("S03 缓存知识卡未关联正式主题")
        topic_id = topic[0]
        old_hash = old_ref["blob_hash"]
        old_ref_id = old_ref["source_ref_id"]

    await git("add", "--", "src/demo/orders/OrderService.java",
              "src/demo/orders/PriceCache.java")
    await git("commit", "-m", "增加惰性价格缓存")
    feature_before = await git("rev-parse", "HEAD")
    await git("switch", "main")
    controller = repo / "src/demo/orders/OrderController.java"
    text = controller.read_text(encoding="utf-8")
    controller.write_text(text.replace("    public int quote",
        "    // 订单接口保持原有报价语义。\n    public int quote", 1), encoding="utf-8")
    await git("add", "--", "src/demo/orders/OrderController.java")
    await git("commit", "-m", "补充订单控制器说明")
    main_head = await git("rev-parse", "HEAD")
    await git("switch", "feature/price-cache")
    await git("rebase", "main")
    feature_after = await git("rev-parse", "HEAD")
    if (feature_after in (feature_before, main_head)
            or await git("status", "--porcelain")):
        raise AssertionError("S03 Git rebase 后 HEAD 或工作树状态不正确")
    new_capture_path = attempt / "s03-installed-rebase-capture.json"
    await command("安装版 rebase 后重新捕获", ["node", driver, "capture", str(executable),
        str(repo), base_sha, str(new_capture_path), "",
        "OrderController.quote -> OrderService.quote -> PriceCache.get"], 300)
    new_capture = json.loads(new_capture_path.read_text(encoding="utf-8"))
    new_snapshot = new_capture["snapshot_id"]
    if new_snapshot == old_snapshot:
        raise AssertionError("S03 rebase 后没有生成新快照")
    revision_path = attempt / "s03-installed-revision.json"
    await command("安装版同卡重索引修订", ["node",
        str(ROOT / "acceptance/scenarios/m7_s03_reindex_revision_ui.mjs"),
        str(executable), str(repo), old_snapshot, new_snapshot,
        module["module_id"], card_id, first["wiki_relative_path"],
        str(credentials), str(revision_path)], 660)
    revised = json.loads(revision_path.read_text(encoding="utf-8"))
    if revised["status"] != "PASS" or not all(revised["checks"].values()):
        raise AssertionError("S03 同概念证据归并与旧 Wiki 修订 UI 断言未通过")
    with GrowthLocalRepository(repo / ".ahadiff/growth.sqlite") as ledger:
        original = ledger.connection.execute(
            "SELECT blob_hash,content FROM source_refs WHERE source_ref_id=?", (old_ref_id,),
        ).fetchone()
        first_card = card_detail(ledger, card_id)
        latest = card_detail(ledger, revised["revision_card_id"])
        if (not original or original["blob_hash"] != old_hash
                or hashlib.sha256(original["content"]).hexdigest() != old_hash
                or first_card["learning"]["stage"] != "completed"
                or latest["card"]["card_version"] != revised["revision_before_version"] + 1
                or latest["learning"]["stage"] != "completed"):
            raise AssertionError("S03 换基破坏旧来源或修订未完成")

    note_input = attempt / "s03-note-preparation.json"
    note_input.write_text(json.dumps({"card_id": card_id, "topic_id": topic_id,
        "note_text": S03_NOTE_TEXT}, ensure_ascii=False), encoding="utf-8")
    note_path = attempt / "s03-installed-note-create.json"
    await command("安装版 ReMe 故障时保存 N1", ["node",
        str(ROOT / "acceptance/scenarios/m7_s01_same_card_note_ui.mjs"),
        "create", str(executable), str(repo), str(credentials),
        str(note_input), str(note_path)], 360)
    note = json.loads(note_path.read_text(encoding="utf-8"))
    if note["status"] != "PASS" or not note["note_id"]:
        raise AssertionError("S03 安装版 N1 未保存")
    note_id = uuid.UUID(note["note_id"])
    with psycopg.connect(dsn) as cloud:
        persisted = cloud.execute(
            "SELECT revision,content_text FROM growth_notes WHERE account_id=%s AND note_id=%s",
            (account, note_id),
        ).fetchone()
    if persisted != (1, S03_NOTE_TEXT):
        raise AssertionError("S03 ReMe 故障影响云端用户原文")
    unavailable_url = reme_url
    async with httpx.AsyncClient(base_url=unavailable_url, timeout=2, trust_env=False) as memory:
        outcome = await run_once(dsn, ReMeAdapter(memory))
    if outcome != "failed":
        raise AssertionError(f"S03 停机时记忆投影未记录失败：{outcome}")
    second_workspace = attempt / "s03-device-b"
    second_state = second_workspace / ".ahadiff"
    second_state.mkdir(parents=True)
    _copy_database(attempt / "device-b.sqlite", second_state / "growth.sqlite")
    note_input.write_text(json.dumps({"card_id": card_id, "topic_id": topic_id,
        "note_text": S03_NOTE_TEXT, "note_id": str(note_id)},
        ensure_ascii=False), encoding="utf-8")
    read_path = attempt / "s03-installed-note-read.json"
    await command("第二独立工作区安装版读取 N1", ["node",
        str(ROOT / "acceptance/scenarios/m7_s01_same_card_note_ui.mjs"),
        "read", str(executable), str(second_workspace), str(credentials),
        str(note_input), str(read_path)], 360)
    observed_read = json.loads(read_path.read_text(encoding="utf-8"))
    if observed_read["status"] != "PASS":
        raise AssertionError("S03 第二端未读取原文 N1")
    memory_workspace = attempt / "s03-reme-workspace"
    memory_workspace.mkdir()
    reme = start_process([
        str(REME), "start", f"config={CONFIG}", f"workspace_dir={memory_workspace}",
        f"service.port={reme_url.rsplit(':', 1)[1]}",
    ], attempt, "s03-reme", os.environ.copy())
    try:
        version = wait_reme(reme, reme_url)
        with psycopg.connect(dsn) as cloud:
            cloud.execute(
                "UPDATE growth_memory_jobs SET next_retry_at=now() WHERE account_id=%s "
                "AND source_type='note' AND source_id=%s", (account, note_id),
            )
        path = reme_note_path(account, note_id)
        async with httpx.AsyncClient(base_url=reme_url, timeout=25, trust_env=False) as memory:
            for _ in range(10):
                projected = await run_once(dsn, ReMeAdapter(memory))
                if (memory_workspace / Path(path)).is_file():
                    break
                if projected is None:
                    await asyncio.sleep(1)
            file = memory_workspace / Path(path)
            if not file.is_file() or S03_NOTE_TEXT not in file.read_text(encoding="utf-8"):
                raise AssertionError("S03 ReMe 恢复后没有投影原文 N1")
            (await memory.post("/reindex", json={})).raise_for_status()
            for _ in range(30):
                if path in await ReMeAdapter(memory).search_paths("PriceCache", limit=20):
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("S03 ReMe 恢复后检索未命中 N1")
        headers = {"Authorization": f"Bearer {token}", "X-Device-Id": published["device_id"]}
        async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
            context = await cloud.get("/v1/growth/context", params={"query": "PriceCache"},
                                      headers=headers)
            context.raise_for_status()
            memory_context = context.json()
        if (memory_context["memory_status"] != "current" or not any(
            item["note_id"] == str(note_id) and item["content_text"] == S03_NOTE_TEXT
            for item in memory_context["notes"]
        )):
            raise AssertionError("S03 云端记忆未按账号与版本回源")
        with psycopg.connect(dsn) as cloud:
            bundle = cloud.execute(
                "SELECT bundle FROM growth_knowledge_bundles WHERE account_id=%s AND card_id=%s",
                (account, uuid.UUID(card_id)),
            ).fetchone()
        if not bundle:
            raise AssertionError("S03 云端缺少原卡两题历史")
        questions = {
            item["question_id"]: item
            for item in bundle[0]["tables"]["card_objective_questions"]
        }
        current_version = max(
            (int(item["card_version"]) for item in questions.values()), default=0,
        )
        answer_ids = [
            item["answer_id"] for item in bundle[0]["tables"]["card_objective_answers"]
            if questions.get(item["question_id"], {}).get("card_version") == current_version
        ]
        if len(answer_ids) != 2:
            raise AssertionError("S03 云端当前修订缺少两题客观作答")
        chat_input = attempt / "s03-chat-preparation.json"
        chat_input.write_text(json.dumps({
            "mode": "live_observation", "account_id": str(account),
            "note_id": str(note_id), "attempt_ids": answer_ids[-2:],
            "provider_name": "deepseek",
            "question": "我对订单缓存到期有什么误解？现在怎么理解？",
        }, ensure_ascii=False), encoding="utf-8")
        chat_path = attempt / "s03-installed-memory-chat.json"
        await command("安装版重启后真实模型记忆问答", ["node",
            str(ROOT / "acceptance/scenarios/m7_s01_chat_ui.mjs"),
            str(executable), str(repo), str(credentials), str(chat_input),
            str(chat_path)], 420)
        chat = json.loads(chat_path.read_text(encoding="utf-8"))
        answer = chat.get("assistant", {}).get("content_text", "")
        if (chat["status"] != "observed_pending_audit"
                or str(note_id) not in chat["preview"]["note_ids"]
                or "TTL" not in answer
                or not any(term in answer for term in ("访问", "下一次 get", "下一次读取"))
                or not any(term in answer for term in ("后台", "自动刷新", "定时"))):
            raise AssertionError("S03 安装版模型没有解释旧误解和访问时重载")
    finally:
        stop_reme(reme)
    fixture_audit.update({
        "status": "PASS", "card_id": card_id, "module_id": module["module_id"],
        "old_snapshot_id": old_snapshot, "new_snapshot_id": new_snapshot,
        "feature_before_rebase": feature_before, "main_head": main_head,
        "feature_after_rebase": feature_after, "note_id": str(note_id),
        "wiki_relative_path": first["wiki_relative_path"],
        "reme_version": version, "memory_status": memory_context["memory_status"],
        "ui_evidence": [setup_path.name, capture_path.name, module_path.name,
                        first_path.name, new_capture_path.name, revision_path.name,
                        note_path.name, read_path.name, chat_path.name],
    })
    audit_path.write_text(json.dumps(fixture_audit, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return fixture_audit


async def _verify_installed_s04_development_event(
    source_run: Path, attempt: Path, dsn: str, origin: str,
    token: str, account: uuid.UUID, executable: Path,
) -> dict[str, object]:
    event_repo = attempt / "s01-local-app"
    fixture = FIXTURES["S04"]

    async def command(label: str, args: list[str], timeout: int = 180) -> str:
        completed = await asyncio.to_thread(
            subprocess.run, args, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
        if completed.returncode:
            raise AssertionError(f"S04 {label} 失败：{completed.stderr[-1200:]}")
        return completed.stdout.strip()

    async def git(*args: str) -> str:
        return await command("Git", ["git", "-C", str(event_repo), *args])

    await git("init", "-b", "main")
    await git("config", "user.name", "情境验证")
    await git("config", "user.email", "scenario@example.invalid")
    await git("config", "core.autocrlf", "false")
    (event_repo / ".gitignore").write_text(".ahadiff/\n", encoding="utf-8")
    for name, body in fixture["baseline"].items():
        file = event_repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8", newline="\n")
    await git("add", ".gitignore", "src/models.ts", "src/document.ts")
    await git("commit", "-m", fixture["commit"])
    base_sha = await git("rev-parse", "HEAD")

    async def probe(stage: str) -> str:
        output = attempt / f"s04-{stage}-classes"
        output.mkdir()
        compiler = ROOT / "viewer/node_modules/typescript/bin/tsc"
        sources = [str(item) for item in (event_repo / "src").glob("*.ts")]
        await command("TypeScript 编译", ["node", str(compiler), "--module", "commonjs",
            "--target", "es2022", "--outDir", str(output), *sources])
        (output / "Probe.cjs").write_text(
            "const {updateContent}=require('./document.js');\n"
            "const doc={ownerId:'alice',content:'old'};\n"
            "const run=user=>{try{return updateContent(user,doc,'new').content}"
            "catch(error){return error.message}};\n"
            "console.log('Alice='+run({id:'alice'})+'|Bob='+run({id:'bob'})"
            "+'|anonymous='+run(null));\n", encoding="utf-8",
        )
        return await command("文档权限 Probe", ["node", str(output / "Probe.cjs")])

    baseline = await probe("baseline")
    if baseline != "Alice=new|Bob=new|anonymous=UNAUTHENTICATED":
        raise AssertionError(f"S04 基线行为错误：{baseline}")
    setup_ui = attempt / "s04-installed-setup.json"
    await command("安装版建立文档项目", ["node",
        str(ROOT / "acceptance/scenarios/m7_windows_electron.mjs"),
        "setup", str(executable), str(event_repo), base_sha,
        str(setup_ui), "document-demo", "文档所有者权限修复"], 240)
    setup_observed = json.loads(setup_ui.read_text(encoding="utf-8"))
    project_id = setup_observed["project_id"]
    feature_id = setup_observed["feature_id"]
    with GrowthLocalRepository(event_repo / ".ahadiff/growth.sqlite") as ledger:
        plan = publication_plan(ledger, project_id)
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            initial_sync = await connect_and_sync(
                ledger, cloud, token, publish_project_id=project_id,
                approved_digest=plan["digest"],
            )
    if initial_sync["uploaded"] < 2:
        raise AssertionError("S04 文档项目和 Feature 未进入隔离云")
    explore_ui = attempt / "s04-explore-installed-ui.json"
    await command("安装版授权探讨", ["node",
        str(ROOT / "acceptance/scenarios/m7_s04_explore_ui.mjs"),
        str(executable), str(event_repo), project_id, str(explore_ui)], 300)
    explore = json.loads(explore_ui.read_text(encoding="utf-8"))
    if explore["status"] != "PASS" or not all(explore["checks"].values()):
        raise AssertionError("S04 探讨主题未经过两轮真实模型对话和用户确认")
    await git("switch", "-c", fixture["branch"])
    for name, body in fixture["next"].items():
        file = event_repo / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8", newline="\n")
    changed = await probe("changed")
    if changed != "Alice=new|Bob=FORBIDDEN|anonymous=UNAUTHENTICATED":
        raise AssertionError(f"S04 所有者修复行为错误：{changed}")
    await git("add", "src/access.ts", "src/document.ts")
    await git("commit", "-m", "增加文档所有者编辑权限检查")
    commit_sha = await git("rev-parse", "HEAD")
    setup = attempt / "s04-event-setup.json"
    setup.write_text(json.dumps({
        "workspace": str(event_repo), "account_id": str(account),
        "project_id": project_id, "feature_id": feature_id,
        "binding_id": setup_observed["binding_id"],
        "base_sha": base_sha,
        "commit_sha": commit_sha,
        "explore_session_id": explore["session_id"],
        "explore_topic_id": explore["topic_id"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    event_path = attempt / "s04-event-sent.json"
    sent = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, str(ROOT / "acceptance" / "scenarios" /
                             "m7_s04_send_event.py"),
         "--workspace", str(event_repo),
         "--local-db", str(event_repo / ".ahadiff/growth.sqlite"),
         "--setup", str(setup),
         "--credentials", str(attempt / "ui-cloud-credentials.json"),
         "--output", str(event_path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=90, check=False,
    )
    if sent.returncode != 0:
        raise AssertionError("S04 外部开发事件幂等提交失败：" + (sent.stderr or "")[-1500:])
    event = json.loads(event_path.read_text(encoding="utf-8"))
    ui_path = attempt / "s04-event-installed-ui.json"
    ui = await asyncio.to_thread(
        subprocess.run,
        ["node", str(ROOT / "acceptance" / "scenarios" / "m7_s04_event_ui.mjs"),
         str(executable), str(event_repo),
         str(attempt / "ui-cloud-credentials.json"),
         str(event_path), str(ui_path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        timeout=240, check=False,
    )
    if ui.returncode != 0:
        raise AssertionError("S04 安装版开发事件捕获失败：" + ui.stderr[-1800:])
    observed = json.loads(ui_path.read_text(encoding="utf-8"))
    if observed["status"] != "PASS" or not all(observed["checks"].values()):
        raise AssertionError("S04 安装版事件捕获幂等断言失败")
    with GrowthLocalRepository(event_repo / ".ahadiff/growth.sqlite") as ledger:
        captures = ledger.connection.execute(
            "SELECT binding_id,snapshot_id,expected_head_sha "
            "FROM local_development_event_captures WHERE account_id=? AND event_id=?",
            (str(account), event["event_id"]),
        ).fetchall()
    with psycopg.connect(dsn) as cloud:
        remote = cloud.execute(
            "SELECT status,target_device_id,expected_head_sha "
            "FROM growth_development_events WHERE account_id=%s AND event_id=%s",
            (account, uuid.UUID(event["event_id"])),
        ).fetchall()
    if (len(captures) != 1 or captures[0][0] != setup_observed["binding_id"]
            or captures[0][1] != observed["snapshot_id"]
            or captures[0][2] != event["head_sha"]
            or len(remote) != 1 or remote[0][0] != "targeted"
            or str(remote[0][1]) != event["device_id"]
            or remote[0][2].strip() != event["head_sha"]):
        raise AssertionError("S04 开发事件云端和本机快照身份不一致")
    knowledge_ui = attempt / "s04-knowledge-first-installed-ui.json"
    await command("安装版授权知识优先完整学习", ["node",
        str(ROOT / "acceptance/scenarios/m7_s04_knowledge_first_ui.mjs"),
        str(executable), str(event_repo), str(setup), str(ui_path),
        str(attempt.parent / "obsidian-vault"), str(knowledge_ui)], 600)
    knowledge = json.loads(knowledge_ui.read_text(encoding="utf-8"))
    if knowledge["status"] != "PASS" or not all(knowledge["checks"].values()):
        raise AssertionError("S04 探讨、代码、学习和 Wiki 未完整通过")
    with GrowthLocalRepository(event_repo / ".ahadiff/growth.sqlite") as ledger:
        plan = publication_plan(ledger, project_id)
        async with httpx.AsyncClient(base_url=origin, timeout=60, trust_env=False) as cloud:
            published = await connect_and_sync(
                ledger, cloud, token, publish_project_id=project_id,
                approved_digest=plan["digest"],
            )
    with psycopg.connect(dsn) as cloud:
        card = cloud.execute(
            "SELECT topic_id FROM growth_tasks WHERE account_id=%s AND task_id=%s",
            (account, uuid.UUID(knowledge["card_id"])),
        ).fetchone()
    if card is None or str(card[0]) != explore["topic_id"]:
        raise AssertionError("S04 云端正式卡未关联已确认的探讨主题")
    return {
        "status": "PASS", "event_id": event["event_id"],
        "snapshot_id": observed["snapshot_id"],
        "head_sha": event["head_sha"],
        "cloud_retry_deduplicated": event["checks"]["retry_deduplicated"],
        "changed_payload_rejected": event["checks"]["changed_payload_rejected"],
        "installed_capture_reused": observed["checks"]["second_capture_reused"],
        "project_id": project_id, "feature_id": feature_id,
        "binding_id": setup_observed["binding_id"],
        "explore_session_id": explore["session_id"],
        "explore_topic_id": explore["topic_id"],
        "baseline_probe": baseline, "changed_probe": changed,
        "commit_sha": event["head_sha"],
        "ui_evidence": ui_path.name, "cloud_evidence": event_path.name,
        "explore_session_id": explore["session_id"],
        "explore_topic_id": explore["topic_id"],
        "knowledge": {"card_id": knowledge["card_id"],
                      "candidate_id": knowledge["candidate_id"],
                      "wiki_relative_path": knowledge["wiki_relative_path"],
                      "publish": published},
    }


async def _verify_installed_s04_deleted_memory(
    attempt: Path, dsn: str, origin: str, token: str,
    account: uuid.UUID, executable: Path,
    process: subprocess.Popen[bytes], issuer: str, jwks_url: str,
    cloud_port: int, reme_url: str,
) -> tuple[subprocess.Popen[bytes], dict[str, object]]:
    credentials = attempt / "ui-cloud-credentials.json"
    prior = attempt / "s04-installed-topic-maintenance.json"
    script = ROOT / "acceptance" / "scenarios" / "m7_s04_delete_ui.mjs"
    first_workspace = attempt / "s01-local-app"
    second_workspace = attempt / "s04-device-b"
    evidence: dict[str, dict[str, object]] = {}

    async def phase(name: str, workspace: Path, previous: Path) -> Path:
        output = attempt / f"s04-delete-{name}.json"
        completed = await asyncio.to_thread(
            subprocess.run,
            ["node", str(script), name, str(executable), str(workspace),
             str(credentials), str(previous), str(output)],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", timeout=240, check=False,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"S04 安装版离线删除 {name} 失败：" + completed.stderr[-1800:]
            )
        item = json.loads(output.read_text(encoding="utf-8"))
        if item["status"] != "PASS" or not all(item["checks"].values()):
            raise AssertionError(f"S04 安装版离线删除 {name} 断言未通过")
        evidence[name] = item
        return output

    created = await phase("create", first_workspace, prior)
    temp_note = uuid.UUID(evidence["create"]["temp_note_id"])
    bootstrapped = await phase("bootstrap", second_workspace, created)
    reme = start_process([
        str(REME), "start", f"config={CONFIG}",
        f"workspace_dir={attempt / 'reme-workspace'}",
        f"service.port={reme_url.rsplit(':', 1)[1]}",
    ], attempt, "reme-delete-before", os.environ.copy())
    try:
        wait_reme(reme, reme_url)
        async with httpx.AsyncClient(base_url=reme_url, timeout=25, trust_env=False) as memory:
            for _ in range(50):
                if await run_once(dsn, ReMeAdapter(memory)) is None:
                    break
            (await memory.post("/reindex", json={})).raise_for_status()
        with psycopg.connect(dsn) as cloud:
            before = cloud.execute(
                "SELECT state FROM growth_memory_jobs WHERE account_id=%s "
                "AND source_type='note' AND source_id=%s AND source_version=1 "
                "AND action='upsert'",
                (account, temp_note),
            ).fetchone()
        if before != ("succeeded",):
            raise AssertionError("S04 临时笔记删除前未进入真实 ReMe")
        temp_path = attempt / "reme-workspace" / Path(reme_note_path(account, temp_note))
        if not temp_path.is_file():
            raise AssertionError("S04 真实 ReMe 未保存临时笔记文件")
    finally:
        stop_reme(reme)

    stop(process)
    offline = await phase("offline-edit", second_workspace, bootstrapped)
    resumed: subprocess.Popen[bytes] | None = None
    try:
        resumed = _launch_cloud(
            attempt, dsn, issuer, jwks_url, cloud_port, reme_url,
        )
        deleted = await phase("delete", first_workspace, created)
        await phase("confirm-delete", first_workspace, deleted)
        recovered = await phase("recover", second_workspace, offline)
        await phase("confirm-recover", second_workspace, recovered)
        await phase("verify-topic", first_workspace, prior)
        reme = start_process([
            str(REME), "start", f"config={CONFIG}",
            f"workspace_dir={attempt / 'reme-workspace'}",
            f"service.port={reme_url.rsplit(':', 1)[1]}",
        ], attempt, "reme-delete-after", os.environ.copy())
        try:
            wait_reme(reme, reme_url)
            async with httpx.AsyncClient(base_url=reme_url, timeout=25, trust_env=False) as memory:
                outcomes: list[str] = []
                for _ in range(50):
                    outcome = await run_once(dsn, ReMeAdapter(memory))
                    if outcome is None:
                        break
                    outcomes.append(outcome)
                (await memory.post("/reindex", json={})).raise_for_status()
                paths: list[str] = []
                for _ in range(30):
                    paths = await ReMeAdapter(memory).search_paths(
                        "有了 JWT 就不需要资源权限检查", limit=20,
                    )
                    if not any(str(temp_note) in path for path in paths):
                        break
                    await asyncio.sleep(1)
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Device-Id": evidence["bootstrap"]["device_id"],
            }
            async with httpx.AsyncClient(base_url=origin, timeout=30, trust_env=False) as cloud:
                deleted_context = await cloud.get(
                    "/v1/growth/context",
                    params={"query": "有了 JWT 就不需要资源权限检查", "limit": 20},
                    headers=headers,
                )
                deleted_context.raise_for_status()
                valid_context = await cloud.get(
                    "/v1/growth/context",
                    params={"query": "认证回答是谁，授权回答允许做什么", "limit": 20},
                    headers=headers,
                )
                valid_context.raise_for_status()
        finally:
            stop_reme(reme)
        with psycopg.connect(dsn) as cloud:
            row = cloud.execute(
                "SELECT revision, deleted_at FROM growth_notes "
                "WHERE account_id=%s AND note_id=%s",
                (account, temp_note),
            ).fetchone()
            job = cloud.execute(
                "SELECT state FROM growth_memory_jobs WHERE account_id=%s "
                "AND source_type='note' AND source_id=%s AND source_version=2 "
                "AND action='delete'",
                (account, temp_note),
            ).fetchone()
            current = cloud.execute(
                "SELECT source_version, deleted_at FROM growth_memory_current "
                "WHERE account_id=%s AND source_type='note' AND source_id=%s",
                (account, temp_note),
            ).fetchone()
        valid_note = json.loads(prior.read_text(encoding="utf-8"))["new_note_id"]
        deleted_query_ids = {item["note_id"] for item in deleted_context.json()["notes"]}
        valid_query_ids = {item["note_id"] for item in valid_context.json()["notes"]}
        if (row is None or row[0] != 2 or row[1] is None
                or job != ("succeeded",) or current is None
                or current[0] != 2 or current[1] is None
                or temp_path.exists()
                or any(str(temp_note) in path for path in paths)
                or str(temp_note) in deleted_query_ids | valid_query_ids
                or valid_note not in valid_query_ids):
            raise AssertionError(
                "S04 删除墓碑、ReMe 清理或有效 N1 回源不一致："
                f"note={row}, job={job}, current={current}, "
                f"file={temp_path.exists()}, paths={paths}, "
                f"deleted_ids={deleted_query_ids}, valid_ids={valid_query_ids}"
            )
        assert resumed is not None
        return resumed, {
            "status": "PASS", "temp_note_id": str(temp_note),
            "offline_edit_device_id": evidence["bootstrap"]["device_id"],
            "delete_device_id": evidence["create"]["device_id"],
            "cloud_unreachable_during_offline_edit": True,
            "tombstone_revision": row[0],
            "recovery_copy_preserved": True,
            "reme_delete_job": job[0],
            "context_keeps_valid_note_id": valid_note,
            "worker_outcomes": outcomes,
            "ui_evidence": [f"s04-delete-{name}.json" for name in evidence],
        }
    except BaseException:
        stop(resumed)
        raise


async def _verify_installed_s04_reinstall(
    attempt: Path, dsn: str, account: uuid.UUID,
    installer: Path, executable: Path,
) -> dict[str, object]:
    workspace = attempt / "s01-local-app"
    database = workspace / ".ahadiff" / "growth.sqlite"
    before_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    installed_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    install_dir = executable.parent
    installed = await asyncio.to_thread(
        subprocess.run,
        [str(installer), "/S", f"/D={install_dir}"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    new_executable = install_dir / executable.name
    if (installed.returncode != 0 or not new_executable.is_file()
            or hashlib.sha256(new_executable.read_bytes()).hexdigest() != installed_hash
            or hashlib.sha256(database.read_bytes()).hexdigest() != before_hash):
        raise AssertionError(
            "S04 同候选静默重装未保留可执行文件与本机账本："
            f"exit={installed.returncode}, stderr={installed.stderr[-600:]}"
        )
    prior = attempt / "s04-installed-topic-maintenance.json"
    output = attempt / "s04-installed-after-reinstall.json"
    completed = await asyncio.to_thread(
        subprocess.run,
        ["node", str(ROOT / "acceptance" / "scenarios" /
                     "m7_s04_topic_export_ui.mjs"), "after",
         str(new_executable), str(workspace),
         str(attempt / "ui-cloud-credentials.json"), str(prior),
         str(prior), str(output)],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=240, check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "S04 重装后安装版主题与导出失败：" + completed.stderr[-1800:]
        )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    if evidence["status"] != "PASS" or not all(evidence["checks"].values()):
        raise AssertionError("S04 重装后安装版断言未全部通过")
    export_id = uuid.UUID(evidence["download_export_id"])
    downloaded_json = attempt / f"growth-{export_id}.json"
    downloaded_md = attempt / f"growth-{export_id}.md"
    with psycopg.connect(dsn) as cloud:
        exported = cloud.execute(
            "SELECT json_payload, markdown_text FROM growth_exports "
            "WHERE account_id=%s AND export_id=%s",
            (account, export_id),
        ).fetchone()
    if (exported is None
            or json.loads(downloaded_json.read_text(encoding="utf-8")) != exported[0]
            or downloaded_md.read_text(encoding="utf-8") != exported[1]):
        raise AssertionError("S04 重装后下载文件与云端快照不一致")
    return {
        "status": "PASS", "installer_sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        "installed_exe_sha256": installed_hash,
        "workspace_database_unchanged_by_reinstall": True,
        "download_export_id": str(export_id),
        "download_json_sha256": hashlib.sha256(downloaded_json.read_bytes()).hexdigest(),
        "download_markdown_sha256": hashlib.sha256(downloaded_md.read_bytes()).hexdigest(),
        "ui_evidence": output.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-dsn", required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--installed-exe", type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id")
    target.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if bool(args.installer) != bool(args.installed_exe):
        parser.error("--installer 与 --installed-exe 必须同时提供")
    source = args.source_db.resolve(strict=True)
    run = args.run_dir.resolve(strict=True) if args.run_dir else (RUNS / args.run_id).resolve()
    if run.parent != RUNS.resolve() or not run.name.startswith(
        ("knowledge-sync-live-", "knowledge-first-live-")
    ):
        raise ValueError("验收目录必须在 knowledge-sync-live 或 knowledge-first-live 系列中")
    if args.run_dir and not source.is_relative_to(run):
        raise ValueError("联合运行的来源数据库必须属于同一 run 目录")
    run.mkdir(parents=True, exist_ok=True)
    attempt = run / ("attempt-" + uuid.uuid4().hex[:8])
    attempt.mkdir()
    if args.installer:
        installer = args.installer.resolve(strict=True)
        installed_exe = args.installed_exe.resolve(strict=False)
        installation = subprocess.run(
            [str(installer), "/S", f"/D={installed_exe.parent}"],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if installation.returncode or not installed_exe.is_file():
            raise AssertionError(
                "完整验证安装候选失败："
                f"exit={installation.returncode}, stderr={installation.stderr[-600:]}"
            )
    database = "growth_knowledge_" + uuid.uuid4().hex[:12]
    dsn = make_conninfo(args.admin_dsn, dbname=database)
    account = uuid.uuid4()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "growth-knowledge-sync", "alg": "RS256", "use": "sig"})
    JwksHandler.document = json.dumps({"keys": [public_jwk]}).encode("utf-8")
    issuer_server = ThreadingHTTPServer(("127.0.0.1", 0), JwksHandler)
    issuer_thread = threading.Thread(target=issuer_server.serve_forever, daemon=True)
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
    }, private_key, algorithm="RS256", headers={"kid": "growth-knowledge-sync"})
    process: subprocess.Popen[bytes] | None = None
    try:
        with psycopg.connect(args.admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
            )
        process = _launch_cloud(attempt, dsn, issuer, jwks_url, cloud_port, reme_url)
        result = asyncio.run(_exercise(source, attempt, origin, token))
        if args.installer:
            result["s03_installed_java_cache"] = asyncio.run(
                _verify_installed_s03_java_cache(
                    attempt, dsn, origin, reme_url, token, account,
                    args.installed_exe.resolve(strict=True),
                )
            )
        result["s01_memory"] = asyncio.run(_verify_s01_memory(
            run, attempt, dsn, origin, reme_url, token, account, result,
            args.installed_exe.resolve(strict=True) if args.installed_exe else None,
        ))
        if args.installer:
            result["s02_installed_first_learning"] = asyncio.run(
                _verify_installed_s02_first_learning(
                    run, attempt, origin, token,
                    args.installed_exe.resolve(strict=True),
                )
            )
            result["s02_installed_response_loss"] = asyncio.run(
                _verify_installed_s02_response_loss(
                    run, attempt, dsn, origin, token, account,
                    args.installer.resolve(strict=True),
                    args.installed_exe.resolve(strict=True),
                    result["s02_installed_first_learning"],
                )
            )
            result["s02_installed_knowledge_revision"] = asyncio.run(
                _verify_installed_s02_knowledge_revision(
                    run, attempt, dsn, account,
                    args.installed_exe.resolve(strict=True),
                    result["s02_installed_first_learning"],
                )
            )
            process, result["s02_installed_note_conflict"] = asyncio.run(
                _verify_installed_s02_note_conflict(
                    attempt, dsn, account,
                    args.installer.resolve(strict=True),
                    args.installed_exe.resolve(strict=True),
                    process, issuer, jwks_url, cloud_port, reme_url,
                    result["first_sync"]["device_id"],
                )
            )
            result["s04_installed_development_event"] = asyncio.run(
                _verify_installed_s04_development_event(
                    run, attempt, dsn, origin, token, account,
                    args.installed_exe.resolve(strict=True),
                )
            )
            result["s04_installed_topic_maintenance"] = asyncio.run(
                _verify_installed_s04_topic_maintenance(
                    run, attempt, dsn, origin, token, account,
                    args.installed_exe.resolve(strict=True),
                    result["second_sync"]["device_id"],
                    result["s04_installed_development_event"]["knowledge"]["card_id"],
                )
            )
            process, result["s04_installed_deleted_memory"] = asyncio.run(
                _verify_installed_s04_deleted_memory(
                    attempt, dsn, origin, token, account,
                    args.installed_exe.resolve(strict=True), process,
                    issuer, jwks_url, cloud_port, reme_url,
                )
            )
            result["s04_installed_reinstall"] = asyncio.run(
                _verify_installed_s04_reinstall(
                    attempt, dsn, account,
                    args.installer.resolve(strict=True),
                    args.installed_exe.resolve(strict=True),
                )
            )
            result["s01_installed_first_learning"] = asyncio.run(
                _verify_installed_s01_first_learning(
                    run, attempt, args.installed_exe.resolve(strict=True),
                )
            )
            result["s01_installed_same_card_memory"] = asyncio.run(
                _verify_installed_s01_same_card_memory(
                    run, attempt, dsn, origin, reme_url, token, account,
                    args.installed_exe.resolve(strict=True),
                    result["s01_installed_first_learning"],
                )
            )
        result["status"] = "PASS"
        result["database"] = database
        (attempt / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"KNOWLEDGE_SYNC_FULL_CHAIN_PASS {attempt}")
    finally:
        stop(process)
        issuer_server.shutdown()
        issuer_server.server_close()


if __name__ == "__main__":
    main()
