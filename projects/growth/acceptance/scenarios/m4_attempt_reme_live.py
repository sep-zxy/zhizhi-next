"""Replay two answer revisions through real ReMe and source-checked context."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
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
    current_attempts_for_paths,
    reme_attempt_path,
    run_once,
)

ROOT = Path(__file__).resolve().parents[2]


async def exercise(run_dir: Path, dsn: str, reme_url: str) -> None:
    local = json.loads(
        (ROOT / "acceptance" / "runs" / LOCAL_RUN / "chain.json").read_text(encoding="utf-8")
    )
    seed_dir = run_dir / "seed-m2"
    (seed_dir / "artifacts").mkdir(parents=True)
    await seed_m2(seed_dir, dsn, local)
    chain = json.loads((seed_dir / "chain.json").read_text(encoding="utf-8"))
    account_id = uuid.UUID(chain["account_id"])
    attempt_ids = [uuid.UUID(item) for item in chain["attempt_ids"]]
    paths = [reme_attempt_path(account_id, item) for item in attempt_ids]
    workspace = run_dir / "workspace"
    steps: list[dict[str, Any]] = []

    def check(name: str, passed: bool, actual: Any) -> None:
        steps.append({"step": name, "at": now(), "verdict": "PASS" if passed else "FAIL",
                      "actual": actual})
        with (run_dir / "steps.jsonl").open("a", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(steps[-1], ensure_ascii=False, default=str) + "\n")
        if not passed:
            raise AssertionError(f"{name}: {actual}")

    with connect(dsn) as conn:
        jobs = conn.execute(
            "SELECT source_id, source_version, state FROM growth_memory_jobs "
            "WHERE account_id=%s AND source_type='attempt' ORDER BY source_id, source_version",
            (account_id,),
        ).fetchall()
        device = conn.execute(
            "SELECT device_id FROM growth_devices WHERE account_id=%s ORDER BY last_seen LIMIT 1",
            (account_id,),
        ).fetchone()
    check("attempt_and_feedback_enqueue_in_transaction", len(jobs) == 4
          and sorted(int(item["source_version"]) for item in jobs) == [1, 1, 2, 2]
          and all(item["state"] == "pending" for item in jobs)
          and device is not None, jobs)
    assert device is not None

    async with httpx.AsyncClient(base_url=reme_url, timeout=30) as reme:
        adapter = ReMeAdapter(reme)
        outcomes = []
        for _ in range(10):
            outcome = await run_once(dsn, adapter)
            if outcome is None:
                break
            outcomes.append(outcome)
        with connect(dsn) as conn:
            states = conn.execute(
                "SELECT source_id, source_version, state FROM growth_memory_jobs "
                "WHERE account_id=%s AND source_type='attempt' ORDER BY source_id, source_version",
                (account_id,),
            ).fetchall()
        check("old_answer_versions_skipped", outcomes.count("skipped") == 2
              and all(item["state"] == ("skipped" if item["source_version"] == 1
                                         else "succeeded") for item in states),
              {"outcomes": outcomes, "states": states})

        contents = [(workspace / Path(path)).read_text(encoding="utf-8") for path in paths]
        check("real_reme_writes_historical_answer_and_feedback",
              all("历史学习回答" in item and "当时回答" in item and "反馈摘要" in item
                  for item in contents)
              and "join 会等待" in contents[0]
              and "修正自历史回答" in contents[1],
              {"paths": paths, "contents": contents})

        indexed = await reme.post("/reindex", json={})
        indexed.raise_for_status()
        found: list[str] = []
        for _ in range(30):
            found = await adapter.search_paths("join", limit=20)
            if set(paths) <= set(found):
                break
            await asyncio.sleep(1)
        check("real_reme_search_finds_both_answers", set(paths) <= set(found),
              {"paths": found, "reindex": indexed.json()})
        visible = current_attempts_for_paths(dsn, account_id, found)
        foreign = current_attempts_for_paths(dsn, uuid.uuid4(), found)
        check("source_checked_history_and_account", len(visible) == 2
              and all(item["historical"] and item["status"] == "feedback_ready"
                      for item in visible)
              and visible[0]["feedback"] is not None and not foreign,
              {"visible": visible, "foreign": foreign})

        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_current SET source_version=1 "
                "WHERE account_id=%s AND source_type='attempt' AND source_id=%s",
                (account_id, attempt_ids[0]),
            )
        stale = current_attempts_for_paths(dsn, account_id, found)
        check("stale_feedback_projection_filtered", len(stale) == 1
              and stale[0]["attempt_id"] == str(attempt_ids[1]), stale)
        with connect(dsn) as conn:
            conn.execute(
                "UPDATE growth_memory_current SET source_version=2 "
                "WHERE account_id=%s AND source_type='attempt' AND source_id=%s",
                (account_id, attempt_ids[0]),
            )

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        app = create_app(CloudSettings(
            dsn, AuthVerifier(issuer=ISSUER, public_key_pem=public_pem), reme_url=reme_url,
        ))
        headers = {"Authorization": f"Bearer {token(private_key, account_id)}",
                   "X-Device-Id": str(device['device_id'])}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://growth.test",
        ) as cloud:
            response = await cloud.get("/v1/growth/context", headers=headers,
                                       params={"query": "join", "limit": 20})
        body = response.json()
        check("real_cloud_context_returns_attempts", response.status_code == 200
              and {item["attempt_id"] for item in body.get("attempts", [])}
              == {str(item) for item in attempt_ids}
              and body["memory_status"] == "current", body)

    save_json(run_dir / "chain.json", {
        "seed": "seed-m2", "account_id": str(account_id),
        "attempt_ids": [str(item) for item in attempt_ids], "reme_paths": paths,
        "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
    })
    save_json(run_dir / "result.json", {"status": "PASS", "checks": len(steps)})
    save_json(run_dir / "manifest.json", {
        "run_id": run_dir.name, "stage": "M4", "mode": "local_live_reme",
        "check_status": "PASS", "stage_status": "BLOCKED",
        "started_at": steps[0]["at"], "finished_at": now(),
        "database_name": dsn.rsplit("dbname=", 1)[1].split()[0],
        "reme_version": "0.4.1.12",
        "stage_blockers": ["无真实模型 provider", "无外部云 PostgreSQL", "无 macOS 机器"],
    })
    (run_dir / "report.md").write_text(
        "# M4 历史回答真实 ReMe 子链\n\n"
        "M2 回放的 A1 错误回答和 A2 修正回答通过真实业务 API 同事务入队，"
        "真实 ReMe v0.4.1.12 写入与检索；v1 旧投影跳过，v2 反馈入索引。"
        "检索结果经账号、来源和版本回源，旧版反馈被过滤，云 context 返回两条历史回答。\n\n"
        f"检查：**PASS ({len(steps)}/{len(steps)})**。输入为 replay，且无真实模型、"
        "外部云和 macOS；不计入 S01–S04 完整 live 情境。\n",
        encoding="utf-8",
    )


def main() -> None:
    admin_dsn = os.environ["GROWTH_TEST_POSTGRES_DSN"]
    reme_url = os.environ["GROWTH_REME_URL"]
    run_dir = ROOT / "acceptance" / "runs" / "m4-attempt-live-20260926-v1"
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    database_name = "growth_m4_attempt_20260926_v1"
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        dsn = admin_dsn.replace("dbname=postgres", f"dbname={database_name}")
        apply_migrations(dsn)
        asyncio.run(exercise(run_dir, dsn, reme_url))
    except Exception as exc:
        save_json(run_dir / "result.json", {"status": "FAIL", "error": str(exc)})
        save_json(run_dir / "manifest.json", {
            "run_id": run_dir.name, "stage": "M4", "mode": "local_live_reme",
            "check_status": "FAIL", "stage_status": "BLOCKED",
            "database_name": database_name, "error": str(exc),
        })
        (run_dir / "report.md").write_text(
            f"# M4 历史回答真实 ReMe 子链失败\n\n`{type(exc).__name__}: {exc}`。\n",
            encoding="utf-8",
        )
        raise
    print(run_dir)


if __name__ == "__main__":
    main()
