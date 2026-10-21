"""Exercise the cloud and memory worker processes against PostgreSQL and real ReMe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
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

from ahadiff.growth.cloud.db import connect
from ahadiff.growth.cloud.memory import reme_note_path
from m1_production_cloud_smoke import JwksHandler, await_ready, free_port, stop


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "acceptance" / "runs"
REME = ROOT / "repo" / "ReMe" / ".venv" / "Scripts" / "reme.exe"
CONFIG = ROOT / "acceptance" / "scenarios" / "reme_memory_only.yaml"
NOTE_TEXT = "M4WORKER：缓存读取前先核对版本；旧投影仅供历史追溯。"
REVISED_TEXT = "M4WORKER：缓存读取前核对当前版本；过期投影不得回源为现行笔记。"


def start_process(command: list[str], run: Path, name: str,
                  environment: dict[str, str]) -> subprocess.Popen[bytes]:
    log = (run / f"{name}.log").open("wb")
    try:
        return subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()


def stop_reme(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, check=False,
        )
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    else:
        stop(process)


def wait_reme(process: subprocess.Popen[bytes], origin: str) -> str:
    deadline = time.monotonic() + 75
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"ReMe 进程提前退出：{process.returncode}")
            try:
                response = client.post(f"{origin}/version", json={})
                if response.status_code == 200 and response.json().get("success"):
                    return str(response.json().get("answer"))
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.4)
    raise TimeoutError("真实 ReMe Job 服务启动超时")


def memory_state(dsn: str, account: uuid.UUID, note: uuid.UUID) -> dict[str, object]:
    with connect(dsn) as conn:
        note_row = conn.execute(
            "SELECT revision, content_text, deleted_at FROM growth_notes "
            "WHERE account_id=%s AND note_id=%s", (account, note),
        ).fetchone()
        jobs = conn.execute(
            "SELECT source_version, action, state, attempt_count, last_error "
            "FROM growth_memory_jobs WHERE account_id=%s AND source_type='note' "
            "AND source_id=%s ORDER BY source_version", (account, note),
        ).fetchall()
        current = conn.execute(
            "SELECT source_version, reme_path, deleted_at FROM growth_memory_current "
            "WHERE account_id=%s AND source_type='note' AND source_id=%s",
            (account, note),
        ).fetchone()
    return {"note": note_row, "jobs": jobs, "current": current}


def wait_job(dsn: str, account: uuid.UUID, note: uuid.UUID,
             worker: subprocess.Popen[bytes], target: str,
             version: int = 1) -> dict[str, object]:
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            raise RuntimeError(f"memory worker 提前退出：{worker.returncode}")
        state = memory_state(dsn, account, note)
        if any(job["source_version"] == version and job["state"] == target
               for job in state["jobs"]):
            return state
        time.sleep(0.3)
    raise TimeoutError(f"memory worker 版本 {version} 未达到 {target}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-dsn", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run = (RUNS / args.run_id).resolve()
    if run.parent != RUNS.resolve() or not args.run_id.startswith("m4-production-worker-"):
        raise ValueError("run_id 必须是 acceptance/runs 下的 M4 worker 烟测")
    if not REME.is_file():
        raise FileNotFoundError(REME)
    run.mkdir(parents=True, exist_ok=False)
    workspace = run / "workspace"
    workspace.mkdir()
    started_at = datetime.now(UTC).isoformat()
    database = "growth_worker_" + uuid.uuid4().hex[:12]
    dsn = make_conninfo(args.admin_dsn, dbname=database)
    account, device, topic, note = (uuid.uuid4() for _ in range(4))
    path = reme_note_path(account, note)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "m4-worker-smoke", "alg": "RS256", "use": "sig"})
    JwksHandler.document = json.dumps({"keys": [public_jwk]}).encode("utf-8")
    JwksHandler.requests_seen = 0
    jwks = ThreadingHTTPServer(("127.0.0.1", 0), JwksHandler)
    jwks_thread = threading.Thread(target=jwks.serve_forever, daemon=True)
    jwks_thread.start()
    issuer = f"http://127.0.0.1:{jwks.server_port}/auth/v1"
    cloud_url = f"http://127.0.0.1:{free_port()}"
    reme_url = f"http://127.0.0.1:{free_port()}"
    token = jwt.encode({
        "iss": issuer, "aud": "authenticated", "sub": str(account),
        "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=20),
    }, private_key, algorithm="RS256", headers={"kid": "m4-worker-smoke"})
    environment = os.environ.copy()
    environment.update({
        "GROWTH_DATABASE_URL": dsn,
        "GROWTH_AUTH_ISSUER": issuer,
        "GROWTH_AUTH_JWKS_URL": f"http://127.0.0.1:{jwks.server_port}/.well-known/jwks.json",
        "GROWTH_AUTH_AUDIENCE": "authenticated",
        "GROWTH_API_HOST": "127.0.0.1",
        "GROWTH_API_PORT": cloud_url.rsplit(":", 1)[1],
        "GROWTH_REME_URL": reme_url,
    })
    cloud: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    reme: subprocess.Popen[bytes] | None = None
    checks: dict[str, bool] = {}
    observations: dict[str, object] = {}
    error: str | None = None
    try:
        with psycopg.connect(args.admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        cloud = start_process(
            [sys.executable, "-m", "ahadiff.growth.cloud"], run, "cloud", environment,
        )
        await_ready(cloud, cloud_url)
        headers = {"Authorization": f"Bearer {token}", "X-Device-Id": str(device)}
        with httpx.Client(timeout=20, trust_env=False) as client:
            registration = client.post(f"{cloud_url}/v1/devices", headers=headers, json={
                "operation_id": str(uuid.uuid4()), "device_id": str(device),
            })
            topic_response = client.post(f"{cloud_url}/v1/topics", headers=headers, json={
                "operation_id": str(uuid.uuid4()), "topic_id": str(topic),
                "title": "M4 worker 恢复验证",
            })
            created = client.post(f"{cloud_url}/v1/notes", headers=headers, json={
                "operation_id": str(uuid.uuid4()), "note_id": str(note),
                "topic_id": str(topic), "content_text": NOTE_TEXT,
            })
            before = memory_state(dsn, account, note)
            checks["production_api_enqueues_durable_note"] = (
                registration.status_code == topic_response.status_code == created.status_code == 201
                and before["note"] is not None
                and before["note"]["content_text"] == NOTE_TEXT
                and len(before["jobs"]) == 1
                and before["jobs"][0]["state"] == "pending"
                and before["jobs"][0]["attempt_count"] == 0
            )
            if not checks["production_api_enqueues_durable_note"]:
                raise AssertionError(f"业务入队失败：{registration.status_code}, "
                                     f"{topic_response.status_code}, {created.status_code}")
            worker = start_process(
                [sys.executable, "-m", "ahadiff.growth.cloud.memory"],
                run, "worker-1", environment,
            )
            failed = wait_job(dsn, account, note, worker, "failed")
            checks["worker_process_persists_offline_failure"] = (
                failed["jobs"][0]["attempt_count"] == 1
                and failed["jobs"][0]["last_error"] is not None
                and failed["note"]["content_text"] == NOTE_TEXT
                and failed["current"] is None
            )
            reme = start_process([
                str(REME), "start", f"config={CONFIG}", f"workspace_dir={workspace}",
                f"service.port={reme_url.rsplit(':', 1)[1]}",
            ], run, "reme", environment)
            observations["reme_version"] = wait_reme(reme, reme_url)
            with connect(dsn) as conn:
                conn.execute(
                    "UPDATE growth_memory_jobs SET next_retry_at=now() "
                    "WHERE account_id=%s AND source_type='note' AND source_id=%s",
                    (account, note),
                )
            succeeded = wait_job(dsn, account, note, worker, "succeeded")
            projected = workspace / Path(path)
            first_projected_sha256 = (
                hashlib.sha256(projected.read_bytes()).hexdigest()
                if projected.is_file() else None
            )
            checks["same_worker_recovers_real_reme_projection"] = (
                succeeded["jobs"][0]["attempt_count"] >= 2
                and succeeded["current"] is not None
                and succeeded["current"]["source_version"] == 1
                and succeeded["current"]["reme_path"] == path
                and projected.is_file()
                and NOTE_TEXT in projected.read_text(encoding="utf-8")
            )
            index = client.post(f"{reme_url}/reindex", json={})
            index.raise_for_status()
            paths: list[str] = []
            context: dict[str, object] = {}
            for _ in range(30):
                search = client.post(f"{reme_url}/search", json={"query": "M4WORKER", "limit": 10})
                search.raise_for_status()
                paths = [item.get("path", "") for item in
                         search.json().get("metadata", {}).get("results", [])]
                response = client.get(f"{cloud_url}/v1/growth/context", headers=headers,
                                      params={"query": "M4WORKER"})
                if response.status_code == 200:
                    context = response.json()
                if path in paths and any(item.get("note_id") == str(note)
                                         for item in context.get("notes", [])):
                    break
                time.sleep(1)
            checks["real_reme_search_and_cloud_backcheck"] = (
                path in paths and context.get("memory_status") == "current"
                and any(item.get("note_id") == str(note)
                        and item.get("content_text") == NOTE_TEXT
                        for item in context.get("notes", []))
            )
            stop(worker)
            worker = start_process(
                [sys.executable, "-m", "ahadiff.growth.cloud.memory"],
                run, "worker-2", environment,
            )
            time.sleep(3)
            restarted = memory_state(dsn, account, note)
            checks["worker_restart_keeps_single_projection"] = (
                worker.poll() is None and len(restarted["jobs"]) == 1
                and restarted["jobs"][0]["state"] == "succeeded"
                and restarted["jobs"][0]["attempt_count"]
                == succeeded["jobs"][0]["attempt_count"]
                and restarted["current"] is not None
                and restarted["current"]["source_version"] == 1
                and restarted["note"]["content_text"] == NOTE_TEXT
            )
            foreign_account, foreign_device = uuid.uuid4(), uuid.uuid4()
            foreign_token = jwt.encode({
                "iss": issuer, "aud": "authenticated", "sub": str(foreign_account),
                "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=20),
            }, private_key, algorithm="RS256", headers={"kid": "m4-worker-smoke"})
            foreign_headers = {
                "Authorization": f"Bearer {foreign_token}",
                "X-Device-Id": str(foreign_device),
            }
            foreign_registration = client.post(f"{cloud_url}/v1/devices",
                                               headers=foreign_headers, json={
                "operation_id": str(uuid.uuid4()), "device_id": str(foreign_device),
            })
            foreign_context = client.get(f"{cloud_url}/v1/growth/context",
                                         headers=foreign_headers, params={"query": "M4WORKER"})
            checks["shared_reme_does_not_leak_foreign_note"] = (
                foreign_registration.status_code == 201
                and foreign_context.status_code == 200
                and foreign_context.json().get("notes") == []
            )
            stop(worker)
            worker = None
            revised = client.put(f"{cloud_url}/v1/notes/{note}/revisions",
                                 headers=headers, json={
                "operation_id": str(uuid.uuid4()), "base_revision": 1,
                "content_text": REVISED_TEXT,
            })
            hidden_revision = client.get(f"{cloud_url}/v1/growth/context",
                                         headers=headers, params={"query": "M4WORKER"})
            pending_revision = memory_state(dsn, account, note)
            checks["revision_hides_stale_projection_before_worker"] = (
                revised.status_code == 200
                and pending_revision["note"]["revision"] == 2
                and len(pending_revision["jobs"]) == 2
                and pending_revision["jobs"][1]["state"] == "pending"
                and pending_revision["current"]["source_version"] == 1
                and hidden_revision.status_code == 200
                and hidden_revision.json().get("notes") == []
            )
            worker = start_process(
                [sys.executable, "-m", "ahadiff.growth.cloud.memory"],
                run, "worker-3", environment,
            )
            current_revision = wait_job(dsn, account, note, worker, "succeeded", 2)
            revised_file_text = projected.read_text(encoding="utf-8") if projected.exists() else ""
            revised_context = client.get(f"{cloud_url}/v1/growth/context",
                                         headers=headers, params={"query": "M4WORKER"})
            checks["worker_process_projects_current_revision"] = (
                current_revision["current"]["source_version"] == 2
                and REVISED_TEXT in revised_file_text
                and NOTE_TEXT not in revised_file_text
                and revised_context.status_code == 200
                and any(item.get("note_id") == str(note)
                        and item.get("content_text") == REVISED_TEXT
                        for item in revised_context.json().get("notes", []))
            )
            stop(worker)
            worker = None
            deleted = client.request("DELETE", f"{cloud_url}/v1/notes/{note}",
                                     headers=headers, json={
                "operation_id": str(uuid.uuid4()), "base_revision": 2,
            })
            hidden_delete = client.get(f"{cloud_url}/v1/growth/context",
                                       headers=headers, params={"query": "M4WORKER"})
            pending_delete = memory_state(dsn, account, note)
            checks["delete_hides_note_before_reme_cleanup"] = (
                deleted.status_code == 200
                and pending_delete["note"]["revision"] == 3
                and pending_delete["note"]["deleted_at"] is not None
                and len(pending_delete["jobs"]) == 3
                and pending_delete["jobs"][2]["state"] == "pending"
                and projected.exists()
                and hidden_delete.status_code == 200
                and hidden_delete.json().get("notes") == []
            )
            worker = start_process(
                [sys.executable, "-m", "ahadiff.growth.cloud.memory"],
                run, "worker-4", environment,
            )
            cleaned = wait_job(dsn, account, note, worker, "succeeded", 3)
            reindex_delete = client.post(f"{reme_url}/reindex", json={})
            reindex_delete.raise_for_status()
            deleted_paths: list[str] = []
            for _ in range(30):
                search = client.post(f"{reme_url}/search", json={
                    "query": "M4WORKER", "limit": 10,
                })
                search.raise_for_status()
                deleted_paths = [item.get("path", "") for item in
                                 search.json().get("metadata", {}).get("results", [])]
                if path not in deleted_paths:
                    break
                time.sleep(1)
            checks["worker_process_removes_deleted_projection"] = (
                cleaned["current"]["source_version"] == 3
                and cleaned["current"]["deleted_at"] is not None
                and not projected.exists()
                and path not in deleted_paths
            )
            observations.update({
                "device_status": registration.status_code,
                "topic_status": topic_response.status_code,
                "note_status": created.status_code,
                "failed_attempt_count": failed["jobs"][0]["attempt_count"],
                "recovered_attempt_count": succeeded["jobs"][0]["attempt_count"],
                "restart_attempt_count": restarted["jobs"][0]["attempt_count"],
                "first_projected_file_sha256": first_projected_sha256,
                "revised_projected_file_sha256": hashlib.sha256(
                    revised_file_text.encode("utf-8")
                ).hexdigest(),
                "reme_search_paths": paths,
                "cloud_context_note_ids": [item.get("note_id") for item in context.get("notes", [])],
                "foreign_context_note_count": len(foreign_context.json().get("notes", [])),
                "deleted_reme_search_paths": deleted_paths,
                "final_note_revision": cleaned["note"]["revision"],
                "final_job_states": [job["state"] for job in cleaned["jobs"]],
                "account_id": str(account), "note_id": str(note), "reme_path": path,
            })
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        checks["run_error"] = False
    finally:
        stop(worker)
        stop(cloud)
        stop_reme(reme)
        jwks.shutdown()
        jwks.server_close()
        jwks_thread.join(timeout=5)
        result = {
            "run_id": args.run_id,
            "mode": "production_worker_local_postgres_real_reme",
            "status": "PASS" if checks and all(checks.values()) else "FAIL",
            "stage_status": "BLOCKED",
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "environment": {"os": platform.platform(), "python": sys.version.split()[0]},
            "database_name": database,
            "checks": checks,
            "observations": observations,
            "error": error,
        }
        (run / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (run / "report.md").write_text(
            f"# M4 独立 worker 生产进程本机烟测\n\n状态：**{result['status']} "
            "（本机 PostgreSQL 与真实 ReMe）/ M4 BLOCKED**。生产成长云 API "
            "写入笔记和持久任务；独立 worker 进程在 ReMe 断线时记失败，服务恢复后"
            "同进程重试成功，真实文件、索引检索与云端账号回源均验证；随后验证"
            "跨账号隔离、进程重启幂等、修订时遮蔽旧版本、再投影及删除前遮蔽、"
            "删除后 ReMe 文件清理。此运行仅是 Windows 本机分段验证，不计正式 S01/S03。\n\n"
            "[机读结果](result.json)保留检查与 ID；JWKS 私钥和访问令牌未落盘。\n",
            encoding="utf-8",
        )
        print(json.dumps({"run_id": args.run_id, "status": result["status"],
                          "checks": checks, "error": error}, ensure_ascii=False))
        if result["status"] != "PASS":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
