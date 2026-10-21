"""Replay the S01 local learning chain through the real cloud API and PostgreSQL."""

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
from psycopg import sql

from ahadiff.growth.cloud.api import CloudSettings, create_app
from ahadiff.growth.cloud.auth import AuthVerifier
from ahadiff.growth.cloud.db import apply_migrations, connect

ROOT = Path(__file__).resolve().parents[2]
LOCAL_RUN = "m2-replay-20260925T064435Z-fe007923"


async def exercise(run_dir: Path, dsn: str, local: dict[str, Any]) -> None:
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
    account_id, other_account_id = uuid.uuid4(), uuid.uuid4()
    device_id, other_device_id = uuid.uuid4(), uuid.uuid4()
    trace_id = local["local"]["events"][0]["trace_id"]
    core = local["local"]
    learning = local["learning"]
    project = core["project"]
    feature = core["feature"]
    snapshot = core["snapshots"][0]
    analysis = core["analysis_runs"][0]
    opportunity = learning["opportunity"]
    proposal = learning["proposals"][0]
    task = learning["task"]
    attempts = learning["attempts"]
    note = learning["notes"][0]
    steps: list[dict[str, Any]] = []
    checks: dict[str, bool] = {}

    async def post(
        client: httpx.AsyncClient,
        path: str,
        body: dict[str, Any],
        label: str,
        expected: int,
        *,
        account: uuid.UUID = account_id,
        device: uuid.UUID | None = device_id,
        method: str = "POST",
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token(private_key, account)}", "X-Trace-Id": trace_id}
        if device:
            headers["X-Device-Id"] = str(device)
        started = now()
        response = await client.request(method, path, headers=headers, json=body)
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
                f"{label}: {response.status_code}, expected {expected}: {response.text}"
            )
        return response

    app = create_app(settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://growth.test"
    ) as client:
        await post(
            client,
            "/v1/devices",
            {"operation_id": str(uuid.uuid4()), "device_id": str(device_id)},
            "register_device",
            201,
            device=None,
        )
        await post(
            client,
            "/v1/devices",
            {"operation_id": str(uuid.uuid4()), "device_id": str(other_device_id)},
            "register_other_account",
            201,
            account=other_account_id,
            device=None,
        )
        await post(
            client,
            "/v1/projects",
            {
                "operation_id": str(uuid.uuid4()),
                "project_id": project["project_id"],
                "name": project["name"],
                "sync_policy": {"cloud_allowed": True, "model_allowed": False},
            },
            "project",
            201,
        )
        await post(
            client,
            "/v1/features",
            {
                "operation_id": str(uuid.uuid4()),
                "feature_id": feature["feature_id"],
                "project_id": project["project_id"],
                "label": feature["label"],
                "base_ref": feature["base_ref"],
                "start_base_sha": feature["start_base_sha"],
            },
            "feature",
            201,
        )
        source_refs = [
            {key: item[key] for key in ("source_ref_id", "relative_path", "blob_hash")}
            for item in core["source_refs"]
        ]
        opportunity_body = {
            "opportunity_id": opportunity["opportunity_id"],
            "title": opportunity["title"],
            "reason": opportunity["reason"],
            "learning_goal": opportunity["learning_goal"],
            "source_refs": json.loads(opportunity["source_refs_json"]),
            "estimated_minutes": opportunity["estimated_minutes"],
            "uncertainties": json.loads(opportunity["uncertainties_json"]),
            "topic_proposals": [
                {"proposal_id": proposal["proposal_id"], "title": proposal["title"]}
            ],
        }
        analysis_body = {
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
            "mode": "dry_run",
            "status": analysis["status"],
            "upstream_run_id": analysis["upstream_run_id"],
            "source_refs": source_refs,
            "generation_origin": "replay",
            "opportunities": [opportunity_body],
        }
        analysis_response = await post(
            client, "/v1/analysis-results", analysis_body, "analysis_and_opportunity", 201
        )
        repeated_analysis = await post(
            client, "/v1/analysis-results", analysis_body, "retry_analysis", 201
        )
        checks["analysis_transaction_idempotent"] = (
            analysis_response.json() == repeated_analysis.json()
        )
        reanalysis_id = str(uuid.uuid4())
        reanalysis_opportunity_id = str(uuid.uuid4())
        reanalysis_body = {
            **analysis_body,
            "operation_id": str(uuid.uuid4()),
            "analysis_id": reanalysis_id,
            "input_fingerprint": hashlib.sha256(b"explicit-reanalysis").hexdigest(),
            "opportunities": [{
                **opportunity_body,
                "opportunity_id": reanalysis_opportunity_id,
                "topic_proposals": [{
                    "proposal_id": str(uuid.uuid4()),
                    "title": "重新分析后的建议",
                }],
            }],
        }
        await post(
            client, "/v1/analysis-results", reanalysis_body,
            "reanalyse_same_snapshot", 201,
        )
        mismatched_snapshot = await post(
            client, "/v1/analysis-results",
            reanalysis_body | {
                "operation_id": str(uuid.uuid4()),
                "analysis_id": str(uuid.uuid4()),
                "head_sha": "0" * 40,
            },
            "reject_snapshot_rewrite", 409,
        )
        mismatched_ref = await post(
            client, "/v1/analysis-results",
            reanalysis_body | {
                "operation_id": str(uuid.uuid4()),
                "analysis_id": str(uuid.uuid4()),
                "source_refs": [source_refs[0] | {"blob_hash": "0" * 64}],
            },
            "reject_source_ref_rewrite", 409,
        )
        rebound_ref = await post(
            client, "/v1/analysis-results",
            reanalysis_body | {
                "operation_id": str(uuid.uuid4()),
                "analysis_id": str(uuid.uuid4()),
                "source_refs": [source_refs[0] | {
                    "source_ref_id": str(uuid.uuid4()),
                }],
                "opportunities": [],
            },
            "reject_source_path_rebind", 409,
        )
        checks["reanalysis_preserves_snapshot_and_sources"] = (
            mismatched_snapshot.json()["code"] == "SNAPSHOT_MISMATCH"
            and mismatched_ref.json()["code"] == "SOURCE_REF_MISMATCH"
            and rebound_ref.json()["code"] == "SOURCE_REF_MISMATCH"
        )
        invalid_ref = {
            **analysis_body,
            "operation_id": str(uuid.uuid4()),
            "snapshot_id": str(uuid.uuid4()),
            "analysis_id": str(uuid.uuid4()),
            "opportunities": [
                {
                    **opportunity_body,
                    "opportunity_id": str(uuid.uuid4()),
                    "source_refs": [str(uuid.uuid4())],
                }
            ],
        }
        rejected = await post(
            client, "/v1/analysis-results", invalid_ref, "reject_unverified_source", 422
        )
        checks["invalid_source_rolls_back"] = rejected.json()["code"] == "INVALID_SOURCE_REF"
        await post(
            client,
            f"/v1/topic-proposals/{proposal['proposal_id']}/decision",
            {
                "operation_id": str(uuid.uuid4()),
                "decision": "confirm",
                "topic_id": proposal["topic_id"],
            },
            "confirm_topic",
            200,
        )
        await post(
            client,
            "/v1/tasks",
            {
                "operation_id": str(uuid.uuid4()),
                "task_id": task["task_id"],
                "opportunity_id": opportunity["opportunity_id"],
                "topic_id": task["topic_id"],
                "question": task["question"],
                "followups": json.loads(task["followups_json"]),
            },
            "create_task",
            201,
        )
        for index, attempt in enumerate(attempts):
            await post(
                client,
                f"/v1/tasks/{task['task_id']}/attempts",
                {
                    "operation_id": str(uuid.uuid4()),
                    "attempt_id": attempt["attempt_id"],
                    "parent_attempt_id": attempt["parent_attempt_id"],
                    "answer_text": attempt["answer_text"],
                    "hint_level": attempt["hint_level"],
                    "actor_origin": attempt["actor_origin"],
                },
                f"submit_answer_{index + 1}",
                202,
            )
            await post(
                client,
                f"/v1/attempts/{attempt['attempt_id']}/feedback",
                {
                    "operation_id": str(uuid.uuid4()),
                    "feedback_origin": "replay",
                    "feedback": json.loads(attempt["feedback_json"]),
                },
                f"save_feedback_{index + 1}",
                200,
            )
        completed = await post(
            client,
            f"/v1/tasks/{task['task_id']}/progress",
            {"operation_id": str(uuid.uuid4()), "base_revision": 3, "progress": "completed"},
            "complete_task",
            200,
            method="PATCH",
        )
        checks["cloud_task_completed"] = completed.json()["revision"] == 4
        stale = await post(
            client,
            f"/v1/tasks/{task['task_id']}/progress",
            {"operation_id": str(uuid.uuid4()), "base_revision": 3, "progress": "paused"},
            "reject_stale_progress",
            409,
            method="PATCH",
        )
        checks["stale_progress_rejected"] = stale.json()["code"] == "REVISION_CONFLICT"
        note_body = {
            "operation_id": str(uuid.uuid4()),
            "note_id": note["note_id"],
            "task_id": task["task_id"],
            "topic_id": task["topic_id"],
            "content_text": note["content_text"],
        }
        note_response = await post(client, "/v1/notes", note_body, "save_user_note", 201)
        repeated_note = await post(client, "/v1/notes", note_body, "retry_user_note", 201)
        checks["note_idempotent"] = note_response.json() == repeated_note.json()
        foreign_note = await post(
            client,
            "/v1/notes",
            {**note_body, "operation_id": str(uuid.uuid4()), "note_id": str(uuid.uuid4())},
            "reject_other_account_note",
            409,
            account=other_account_id,
            device=other_device_id,
        )
        checks["account_cannot_write_foreign_task"] = (
            foreign_note.json()["code"] == "TASK_TOPIC_MISMATCH"
        )

    with connect(dsn) as conn:
        rows = {
            "project": conn.execute(
                "SELECT project_id FROM growth_projects WHERE account_id=%s", (account_id,)
            ).fetchall(),
            "feature": conn.execute(
                "SELECT feature_id FROM growth_features WHERE account_id=%s", (account_id,)
            ).fetchall(),
            "snapshot": conn.execute(
                "SELECT snapshot_id FROM growth_snapshots WHERE account_id=%s", (account_id,)
            ).fetchall(),
            "analyses": conn.execute(
                "SELECT analysis_id, snapshot_id FROM growth_analysis_runs "
                "WHERE account_id=%s ORDER BY updated_at, analysis_id", (account_id,),
            ).fetchall(),
            "opportunity": conn.execute(
                "SELECT opportunity_id, analysis_id, source_refs FROM growth_opportunities "
                "WHERE account_id=%s",
                (account_id,),
            ).fetchall(),
            "proposal": conn.execute(
                "SELECT proposal_id, topic_id, status FROM growth_topic_proposals "
                "WHERE account_id=%s",
                (account_id,),
            ).fetchall(),
            "task": conn.execute(
                "SELECT task_id, topic_id, opportunity_id, progress, revision "
                "FROM growth_tasks WHERE account_id=%s",
                (account_id,),
            ).fetchall(),
            "attempts": conn.execute(
                "SELECT attempt_id, parent_attempt_id, answer_text, answer_hash, "
                "feedback, feedback_origin FROM growth_learning_attempts "
                "WHERE account_id=%s ORDER BY created_at, attempt_id",
                (account_id,),
            ).fetchall(),
            "notes": conn.execute(
                "SELECT note_id, task_id, topic_id, author, content_text, content_hash "
                "FROM growth_notes WHERE account_id=%s",
                (account_id,),
            ).fetchall(),
            "timeline": conn.execute(
                "SELECT source_type, source_id, evidence_label FROM growth_timeline "
                "WHERE account_id=%s ORDER BY created_at, evidence_id",
                (account_id,),
            ).fetchall(),
            "changes": conn.execute(
                "SELECT change_seq, entity_type, entity_id FROM growth_change_feed "
                "WHERE account_id=%s ORDER BY change_seq",
                (account_id,),
            ).fetchall(),
            "events": conn.execute(
                "SELECT event_id, trace_id, event_type FROM growth_domain_events "
                "WHERE account_id=%s ORDER BY occurred_at, event_id",
                (account_id,),
            ).fetchall(),
        }
        other_note_count_row = conn.execute(
            "SELECT count(*) AS n FROM growth_notes WHERE account_id=%s", (other_account_id,)
        ).fetchone()
        assert other_note_count_row is not None
    checks["ids_match_local_chain"] = (
        str(rows["project"][0]["project_id"]) == project["project_id"]
        and str(rows["feature"][0]["feature_id"]) == feature["feature_id"]
        and str(rows["snapshot"][0]["snapshot_id"]) == snapshot["snapshot_id"]
        and {str(row["opportunity_id"]) for row in rows["opportunity"]}
        == {opportunity["opportunity_id"], reanalysis_opportunity_id}
        and any(
            str(row["proposal_id"]) == proposal["proposal_id"]
            and str(row["topic_id"]) == proposal["topic_id"]
            for row in rows["proposal"]
        )
        and str(rows["task"][0]["task_id"]) == task["task_id"]
        and str(rows["notes"][0]["note_id"]) == note["note_id"]
    )
    cloud_answers = {str(row["attempt_id"]): row["answer_text"] for row in rows["attempts"]}
    checks["a1_a2_exact"] = cloud_answers == {
        item["attempt_id"]: item["answer_text"] for item in attempts
    }
    checks["a2_links_a1"] = any(
        str(row["attempt_id"]) == attempts[1]["attempt_id"]
        and str(row["parent_attempt_id"]) == attempts[0]["attempt_id"]
        for row in rows["attempts"]
    )
    checks["feedback_replay_separate"] = all(
        row["feedback_origin"] == "replay" and row["feedback"] is not None
        for row in rows["attempts"]
    )
    checks["user_note_exact"] = (
        len(rows["notes"]) == 1
        and rows["notes"][0]["author"] == "user"
        and rows["notes"][0]["content_text"] == note["content_text"]
        and rows["notes"][0]["content_hash"].strip()
        == hashlib.sha256(note["content_text"].encode("utf-8")).hexdigest()
    )
    checks["other_account_empty"] = int(other_note_count_row["n"]) == 0
    checks["no_false_mastery"] = {row["evidence_label"] for row in rows["timeline"]} <= {
        "practicing",
        "encountered",
    }
    checks["task_progress_matches_local"] = (
        rows["task"][0]["progress"] == task["progress"] == "completed"
        and rows["task"][0]["revision"] == 4
    )
    checks["ordered_changes_no_gaps"] = [row["change_seq"] for row in rows["changes"]] == list(
        range(1, len(rows["changes"]) + 1)
    )
    checks["events_share_trace"] = all(str(row["trace_id"]) == trace_id for row in rows["events"])
    checks["rollback_kept_one_snapshot"] = len(rows["snapshot"]) == 1
    checks["two_analyses_share_snapshot"] = (
        {str(row["analysis_id"]) for row in rows["analyses"]}
        == {analysis["analysis_id"], reanalysis_id}
        and {str(row["snapshot_id"]) for row in rows["analyses"]}
        == {snapshot["snapshot_id"]}
    )
    checks["retries_kept_one_note_and_two_distinct_opportunities"] = (
        len(rows["notes"]) == 1
        and {str(row["opportunity_id"]) for row in rows["opportunity"]}
        == {opportunity["opportunity_id"], reanalysis_opportunity_id}
    )
    save_json(run_dir / "artifacts" / "postgres-rows.json", rows)
    save_json(
        run_dir / "chain.json",
        {
            "local_run_id": LOCAL_RUN,
            "account_id": str(account_id),
            "project_id": project["project_id"],
            "feature_id": feature["feature_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "analysis_id": analysis["analysis_id"],
            "opportunity_id": opportunity["opportunity_id"],
            "topic_id": proposal["topic_id"],
            "task_id": task["task_id"],
            "attempt_ids": [item["attempt_id"] for item in attempts],
            "note_id": note["note_id"],
            "change_seq": [row["change_seq"] for row in rows["changes"]],
        },
    )
    (run_dir / "steps.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in steps), encoding="utf-8"
    )
    (run_dir / "events.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, default=str) + "\n" for item in rows["events"]
        ),
        encoding="utf-8",
    )
    status = "PASS" if all(checks.values()) else "FAIL"
    save_json(run_dir / "result.json", {"status": status, "checks": checks})
    save_json(
        run_dir / "manifest.json",
        {
            "run_id": run_dir.name,
            "stage": "M2",
            "case_id": "S01-cloud-replay",
            "mode": "replay",
            "local_run_id": LOCAL_RUN,
            "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
            "started_at": steps[0]["started_at"],
            "finished_at": now(),
            "check_status": status,
            "stage_status": "BLOCKED",
            "stage_blockers": ["live 模型未接入", "云端真实 Supabase 未接入", "桌面 UI 未驱动"],
            "artifact_sha256": hashlib.sha256(
                (run_dir / "artifacts" / "postgres-rows.json").read_bytes()
            ).hexdigest(),
        },
    )
    (run_dir / "report.md").write_text(
        "# M2 S01 云端回放链路\n\n"
        f"本地情境 `{LOCAL_RUN}` 的 project→feature→snapshot→analysis→opportunity→topic→task→"
        "两次回答→用户笔记使用相同 ID 写入隔离 PostgreSQL。"
        "机会及来源与分析在同一事务保存；非法来源触发回滚。"
        "A1/A2/N1 的原文和来源在云主库可重读，重复分析与笔记操作未复制对象，"
        "另一账号不能写入本账号卡片。\n\n"
        f"数据库检查：**{status}**。机会和反馈来自显式 replay，未调用 live 模型、"
        "真实 Supabase 或桌面 UI，不能计入 S01 发布验收。\n",
        encoding="utf-8",
    )
    if status != "PASS":
        raise AssertionError(f"M2 cloud checks failed: {checks}")


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    run_id = f"m2-cloud-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir = ROOT / "acceptance" / "runs" / run_id
    run_dir.mkdir(parents=True)
    database_name = "growth_m2_" + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        expected = ["001_initial", "002_learning", "003_sync", "004_memory", "005_review",
                    "006_modules", "007_module_tasks", "008_chat_topics",
                    "009_topic_maintenance", "010_development_events",
                    "011_exports", "012_task_drafts", "013_chat_model_receipts", "014_review_answer_text"]
        if apply_migrations(dsn) != expected:
            raise AssertionError("M2 migrations did not apply")
        asyncio.run(exercise(run_dir, dsn, local))
    except Exception as exc:
        if not (run_dir / "result.json").exists():
            save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
            save_json(
                run_dir / "manifest.json",
                {
                    "run_id": run_id,
                    "stage": "M2",
                    "mode": "replay",
                    "check_status": "FAIL",
                    "stage_status": "BLOCKED",
                    "database_name": database_name,
                    "error": str(exc),
                },
            )
            (run_dir / "report.md").write_text(
                f"# M2 云端回放失败\n\n`{type(exc).__name__}: {exc}`。此运行保持 FAIL。\n",
                encoding="utf-8",
            )
        print(run_dir)
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
