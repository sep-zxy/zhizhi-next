"""Exercise real ReMe recovery for the formal S03 Windows chain without counting Mac steps."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx

from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import (
    ReMeAdapter, current_attempts_for_paths, current_notes_for_paths,
    reme_attempt_path, reme_note_path, run_once,
)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


async def exercise(args: argparse.Namespace) -> dict:
    source = args.source_run.resolve()
    target = args.run_dir.resolve()
    if target.exists() or not target.name.startswith("m7-s03-recovery-rehearsal-"):
        raise AssertionError("恢复演练必须使用新的独立运行目录")
    formal = read(source / "result.json")
    manifest = read(source / "manifest.json")
    note = read(source / "artifacts/s03-09-note-ui.json")
    outage = read(source / "artifacts/s03-09-outage-audit.json")
    prep = read(source / "artifacts/s03-09-preparation.json")
    feedback = read(source / "artifacts/s03-06-feedback-ui.json")
    if not formal["checks"]["S03-09"] or outage["status"] != "PASS" or note["mode"] != "live_observation":
        raise AssertionError("来源不是已审计的 S03-09 停服笔记")
    account = uuid.UUID(prep["account_id"])
    note_id = uuid.UUID(note["note_id"])
    task_id = uuid.UUID(prep["task_id"])
    target.mkdir(parents=True)
    with connect(args.dsn) as conn:
        conn.execute("UPDATE growth_memory_jobs SET next_retry_at=now() WHERE account_id=%s AND state='failed'", (account,))
        rows = conn.execute("SELECT attempt_id,answer_text FROM growth_learning_attempts WHERE account_id=%s AND task_id=%s ORDER BY created_at", (account, task_id)).fetchall()
    attempt_ids = [row["attempt_id"] for row in rows]
    paths = [reme_note_path(account, note_id), *[reme_attempt_path(account, item) for item in attempt_ids]]
    evidence = {"mode": "windows_rehearsal_not_formal", "status": "running", "stage_status": "BLOCKED",
                "source_run_id": source.name, "candidate_sha256": manifest["candidate_sha256"],
                "account_id": str(account), "task_id": str(task_id), "note_id": str(note_id),
                "expected_paths": paths, "started_at": time.time()}
    write(target / "recovery.json", evidence)
    try:
        async with httpx.AsyncClient(base_url=args.reme_url, timeout=30) as client:
            version = await client.post("/version", json={})
            version.raise_for_status()
            evidence["reme_version"] = version.json().get("answer")
            adapter = ReMeAdapter(client)
            outcomes = []
            for _ in range(12):
                outcome = await run_once(args.dsn, adapter)
                if outcome is None:
                    break
                outcomes.append(outcome)
            evidence["worker_outcomes"] = outcomes
            with connect(args.dsn) as conn:
                jobs = conn.execute("SELECT source_type,source_id,source_version,state,attempt_count FROM growth_memory_jobs WHERE account_id=%s ORDER BY source_type,source_id,source_version", (account,)).fetchall()
                current = conn.execute("SELECT source_type,source_id,source_version,reme_path FROM growth_memory_current WHERE account_id=%s ORDER BY source_type,source_id", (account,)).fetchall()
                cloud_note = conn.execute("SELECT revision,content_text FROM growth_notes WHERE account_id=%s AND note_id=%s", (account, note_id)).fetchone()
            evidence["jobs_after_recovery"] = [dict(row) for row in jobs]
            evidence["current_after_recovery"] = [dict(row) for row in current]
            checks = {
                "same_formal_candidate": source.name == prep["run_id"] and manifest["candidate_sha256"] == prep["candidate_sha256"],
                "real_reme_version": evidence["reme_version"] == "0.4.1.12",
                "durable_jobs_recovered": len(jobs) == 5 and all(row["state"] in {"skipped", "succeeded"} for row in jobs)
                and sum(row["state"] == "skipped" for row in jobs) == 2
                and any(row["attempt_count"] >= 2 and row["state"] == "succeeded" for row in jobs),
                "one_current_per_source": len(current) == 3 and {row["reme_path"] for row in current} == set(paths)
                and all(row["source_version"] == (1 if row["source_type"] == "note" else 2) for row in current),
                "note_business_unchanged": cloud_note is not None and cloud_note["revision"] == 1 and cloud_note["content_text"] == note["note_text"],
            }
            files = [args.workspace / Path(item) for item in paths]
            summaries = [turn["feedback"]["summary"] for turn in feedback["turns"]]
            checks["reme_files_preserve_note_and_feedback"] = all(file.exists() for file in files) and note["note_text"] in files[0].read_text(encoding="utf-8") and all(summaries[index] in files[index + 1].read_text(encoding="utf-8") for index in range(2))
            evidence["file_sha256"] = {paths[index]: hashlib.sha256(file.read_bytes()).hexdigest() for index, file in enumerate(files) if file.exists()}
            reindex = await client.post("/reindex", json={})
            reindex.raise_for_status()
            found = []
            evidence["search_query"] = "缓存到期"
            for _ in range(30):
                found = await adapter.search_paths(evidence["search_query"], limit=50)
                if set(paths) <= set(found):
                    break
                await asyncio.sleep(1)
            evidence["search_paths"] = found
            checks["real_reme_search_finds_sources"] = set(paths) <= set(found)
            visible_notes = current_notes_for_paths(args.dsn, account, found)
            visible_attempts = current_attempts_for_paths(args.dsn, account, found)
            foreign = uuid.uuid4()
            checks["account_and_version_backcheck"] = (
                {item["note_id"] for item in visible_notes} == {str(note_id)}
                and {item["attempt_id"] for item in visible_attempts} == {str(item) for item in attempt_ids}
                and all(item["historical"] for item in visible_attempts)
                and not current_notes_for_paths(args.dsn, foreign, found)
                and not current_attempts_for_paths(args.dsn, foreign, found))
            with connect(args.dsn) as conn:
                conn.execute("UPDATE growth_memory_jobs SET state='pending',next_retry_at=now() WHERE account_id=%s AND source_type='note' AND source_id=%s AND source_version=1 AND state='succeeded'", (account, note_id))
            evidence["note_replay_outcome"] = await run_once(args.dsn, adapter)
            with connect(args.dsn) as conn:
                note_current = conn.execute("SELECT source_version,reme_path FROM growth_memory_current WHERE account_id=%s AND source_type='note' AND source_id=%s", (account, note_id)).fetchall()
            checks["replay_idempotent"] = evidence["note_replay_outcome"] == "succeeded" and len(note_current) == 1 and note_current[0]["source_version"] == 1 and note_current[0]["reme_path"] == paths[0]
            evidence["checks"] = checks
            evidence["status"] = "PASS" if all(checks.values()) else "FAIL"
            evidence["finished_at"] = time.time()
            write(target / "recovery.json", evidence)
            (target / "report.md").write_text(
                "# S03 同包 Windows/ReMe 恢复演练\n\n"
                f"结果：**{evidence['status']}**。真实 ReMe v{evidence['reme_version']} "
                "补偿投影、检索回源与重复任务幂等性按 recovery.json 独立留证。"
                "本机 Windows 和隔离 PostgreSQL 不满足 macOS 与外部云要求；正式 S03 仍为 9/12、BLOCKED。\n",
                encoding="utf-8",
            )
            return evidence
    except Exception as error:
        evidence["status"] = "FAIL"
        evidence["error"] = str(error)
        evidence["finished_at"] = time.time()
        write(target / "recovery.json", evidence)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--reme-url", default="http://127.0.0.1:18775")
    args = parser.parse_args()
    result = asyncio.run(exercise(args))
    print(json.dumps({"status": result["status"], "checks": result["checks"]}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
