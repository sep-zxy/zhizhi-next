"""Independently audit the installed S04 topic, export, and memory continuation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from ahadiff.growth.cloud.memory import ReMeAdapter, run_once


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def audit(run: Path, dsn: str, credentials_path: Path, reme_url: str) -> dict:
    artifacts = run / "artifacts"
    setup = read(artifacts / "cloned-s04-setup.json")
    topic = read(artifacts / "topic-maintenance-ui.json")
    answer = read(artifacts / "answer-note-ui.json")
    created = read(artifacts / "temp-create.json")
    deletion = read(artifacts / "temp-final-state.json")
    export_ui = read(artifacts / "final-export-ui.json")
    credentials = read(credentials_path)
    account = uuid.UUID(setup["account_id"])
    if credentials["account_id"] != str(account):
        raise ValueError("S04 云账号不一致")
    export_json_path = artifacts / export_ui["download_filenames"][0]
    export_md_path = artifacts / export_ui["download_filenames"][1]
    exported = read(export_json_path)
    markdown = export_md_path.read_text(encoding="utf-8")
    with sqlite3.connect(run / "fixture" / "repo" / ".ahadiff" / "growth.sqlite") as db:
        device = db.execute(
            "SELECT device_id FROM sync_accounts WHERE account_id=?", (str(account),)
        ).fetchone()
    if device is None:
        raise ValueError("重装后的 Windows 设备无云绑定")

    outcomes: list[str] = []
    async with httpx.AsyncClient(base_url=reme_url, timeout=25) as reme:
        version = await reme.post("/version", json={})
        version.raise_for_status()
        if version.json().get("answer") != "0.4.1.12":
            raise AssertionError("ReMe 版本不符")
        adapter = ReMeAdapter(reme)
        for _ in range(20):
            outcome = await run_once(dsn, adapter)
            if outcome is None:
                break
            outcomes.append(outcome)
        reindex = await reme.post("/reindex", json={})
        reindex.raise_for_status()

    with psycopg.connect(dsn, row_factory=dict_row) as db:
        topics = db.execute(
            "SELECT topic_id, title, status, revision, parent_topic_id "
            "FROM growth_topics WHERE account_id=%s", (account,)
        ).fetchall()
        aliases = db.execute(
            "SELECT alias_topic_id, canonical_topic_id FROM growth_topic_aliases "
            "WHERE account_id=%s", (account,)
        ).fetchall()
        links = db.execute(
            "SELECT topic_id, entity_type, entity_id FROM growth_topic_links "
            "WHERE account_id=%s", (account,)
        ).fetchall()
        moves = db.execute(
            "SELECT entity_type, entity_id, from_topic_id, to_topic_id, action "
            "FROM growth_topic_moves WHERE account_id=%s", (account,)
        ).fetchall()
        memory = db.execute(
            "SELECT source_type, source_id, source_version, deleted_at "
            "FROM growth_memory_current WHERE account_id=%s", (account,)
        ).fetchall()
        jobs = db.execute(
            "SELECT source_type, source_id, source_version, action, state "
            "FROM growth_memory_jobs WHERE account_id=%s", (account,)
        ).fetchall()
        stored_export = db.execute(
            "SELECT json_payload, markdown_text FROM growth_exports "
            "WHERE account_id=%s AND export_id=%s",
            (account, uuid.UUID(export_ui["download_export_id"])),
        ).fetchone()

    cloud_url = credentials.get("cloud_url") or f"http://127.0.0.1:{credentials['port']}"
    headers = {
        "Authorization": f"Bearer {credentials['access_token']}",
        "X-Device-Id": device[0],
    }
    async with httpx.AsyncClient(base_url=cloud_url, timeout=25) as cloud:
        response = await cloud.get(
            "/v1/growth/context",
            params={"query": "我现在怎样区分登录和权限？", "limit": 20},
            headers=headers,
        )
        context = response.json()

    topic_by_id = {str(row["topic_id"]): row for row in topics}
    aliases_set = {(str(row["alias_topic_id"]), str(row["canonical_topic_id"]))
                   for row in aliases}
    links_set = {(str(row["topic_id"]), row["entity_type"], str(row["entity_id"]))
                 for row in links}
    memory_by_key = {(row["source_type"], str(row["source_id"])): row
                     for row in memory}
    notes = {row["note_id"]: row for row in exported["tables"]["notes"]}
    attempts = {row["attempt_id"]: row for row in exported["tables"]["attempts"]}
    tasks = {row["task_id"]: row for row in exported["tables"]["tasks"]}
    temp_id = created["temp_note_id"]
    old_id = setup["note_id"]
    new_id = answer["note_id"]
    task_id = topic["task_id"]
    attempt_id = answer["attempt_id"]
    current_note = memory_by_key.get(("note", new_id))
    deleted_note = memory_by_key.get(("note", temp_id))
    current_attempt = memory_by_key.get(("attempt", attempt_id))
    serialized_context = json.dumps(context, ensure_ascii=False)
    checks = {
        "alias_and_split_children": (
            (setup["topic_id"], topic["new_topic_id"]) in aliases_set
            and topic_by_id[topic["auth_topic_id"]]["parent_topic_id"]
            == uuid.UUID(topic["new_topic_id"])
            and topic_by_id[topic["permission_topic_id"]]["parent_topic_id"]
            == uuid.UUID(topic["new_topic_id"])
            and topic_by_id[topic["permission_topic_id"]]["status"] == "active"
            and topic_by_id[topic["permission_topic_id"]]["revision"] == 3
        ),
        "explicit_note_and_task_links": (
            (topic["auth_topic_id"], "note", old_id) in links_set
            and (topic["permission_topic_id"], "note", new_id) in links_set
            and (topic["permission_topic_id"], "task", task_id) in links_set
            and any(row["action"] == "merge" and str(row["entity_id"]) == old_id
                    for row in moves)
            and any(row["action"] == "split_link" and str(row["entity_id"]) == new_id
                    for row in moves)
        ),
        "download_matches_cloud_snapshot": (
            export_ui["status"] == "PASS" and stored_export is not None
            and stored_export["json_payload"] == exported
            and stored_export["markdown_text"] == markdown
        ),
        "export_originals_and_sources": (
            notes[old_id]["content_text"] == setup["note_text"]
            and notes[new_id]["content_text"] == answer["note_text"]
            and notes[old_id]["deleted_at"] is None
            and notes[new_id]["deleted_at"] is None
            and attempts[attempt_id]["answer_text"] == answer["answer_text"]
            and attempts[attempt_id]["status"] == "feedback_ready"
            and tasks[task_id]["source_refs"] == answer["source_refs"]
            and answer["note_text"] in markdown and setup["note_text"] in markdown
        ),
        "deleted_note_record_not_active": (
            notes[temp_id]["deleted_at"] is not None
            and notes[temp_id]["content_text"] == created["temp_note_text"]
            and deletion["status"] == "PASS"
            and all(deletion["checks"].values())
        ),
        "real_reme_deleted_version": (
            deleted_note is not None
            and deleted_note["source_version"] == 2
            and deleted_note["deleted_at"] is not None
            and any(row["source_type"] == "note" and str(row["source_id"]) == temp_id
                    and row["source_version"] == 2 and row["action"] == "delete"
                    and row["state"] == "succeeded" for row in jobs)
            and current_note is not None and current_note["deleted_at"] is None
            and current_attempt is not None and current_attempt["deleted_at"] is None
        ),
        "exact_question_context_current": (
            response.status_code == 200 and context.get("memory_status") == "current"
            and any(row["note_id"] == new_id and row["content_text"] == answer["note_text"]
                    for row in context.get("notes", []))
            and all(row.get("note_id") != temp_id for row in context.get("notes", []))
            and all(created["temp_note_text"] != row.get("content_text")
                    for row in context.get("notes", []))
            and credentials["access_token"] not in serialized_context
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": "installed_windows_real_reme_local_cloud_rehearsal",
        "account_id": str(account), "worker_outcomes": outcomes,
        "reme_version": version.json().get("answer"),
        "reme_reindex_status": reindex.status_code,
        "context_status": response.status_code,
        "context_note_ids": [row["note_id"] for row in context.get("notes", [])],
        "context_attempt_ids": [row["attempt_id"] for row in context.get("attempts", [])],
        "memory_current": memory,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--reme-url", required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    result = asyncio.run(audit(run, args.dsn, args.credentials, args.reme_url))
    output = run / "artifacts" / "final-audit.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                      encoding="utf-8")
    if result["status"] != "PASS":
        raise AssertionError(f"S04 收尾审计失败：{result['checks']}")
    print("S04_FINAL_AUDIT_PASS")


if __name__ == "__main__":
    main()
