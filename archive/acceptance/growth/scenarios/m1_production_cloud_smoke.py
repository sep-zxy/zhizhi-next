"""Exercise the production cloud entrypoint with PostgreSQL and a local JWKS issuer."""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ahadiff.growth.cloud.db import MIGRATIONS, connect


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "acceptance" / "runs"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class JwksHandler(BaseHTTPRequestHandler):
    document: bytes
    requests_seen = 0

    def do_GET(self) -> None:
        if self.path != "/.well-known/jwks.json":
            self.send_error(404)
            return
        type(self).requests_seen += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.document)))
        self.end_headers()
        self.wfile.write(self.document)

    def log_message(self, _format: str, *args: object) -> None:
        pass


def await_ready(process: subprocess.Popen[bytes], origin: str) -> None:
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"成长云进程提前退出：{process.returncode}")
            try:
                response = client.get(f"{origin}/healthz")
                if response.status_code == 200 and response.json() == {"ok": True}:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.2)
    raise TimeoutError("生产入口健康检查超时")


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-dsn", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run = (RUNS / args.run_id).resolve()
    if run.parent != RUNS.resolve() or not args.run_id.startswith("m1-production-cloud-"):
        raise ValueError("run_id 必须是 acceptance/runs 下的 M1 生产入口烟测")
    run.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(UTC).isoformat()
    database = "growth_entry_" + uuid.uuid4().hex[:12]
    dsn = make_conninfo(args.admin_dsn, dbname=database)
    account = uuid.uuid4()
    device = uuid.uuid4()
    operation = uuid.uuid4()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "growth-smoke", "alg": "RS256", "use": "sig"})
    JwksHandler.document = json.dumps({"keys": [public_jwk]}).encode("utf-8")
    JwksHandler.requests_seen = 0
    jwks_server = ThreadingHTTPServer(("127.0.0.1", 0), JwksHandler)
    jwks_thread = threading.Thread(target=jwks_server.serve_forever, daemon=True)
    jwks_thread.start()
    jwks_port = jwks_server.server_port
    issuer = f"http://127.0.0.1:{jwks_port}/auth/v1"
    cloud_port = free_port()
    origin = f"http://127.0.0.1:{cloud_port}"
    token = jwt.encode({
        "iss": issuer, "aud": "authenticated", "sub": str(account),
        "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=20),
    }, private_key, algorithm="RS256", headers={"kid": "growth-smoke"})
    wrong_issuer = jwt.encode({
        "iss": issuer + "/wrong", "aud": "authenticated", "sub": str(account),
        "role": "authenticated", "exp": datetime.now(UTC) + timedelta(minutes=20),
    }, private_key, algorithm="RS256", headers={"kid": "growth-smoke"})
    environment = os.environ.copy()
    environment.update({
        "GROWTH_DATABASE_URL": dsn,
        "GROWTH_AUTH_ISSUER": issuer,
        "GROWTH_AUTH_JWKS_URL": f"http://127.0.0.1:{jwks_port}/.well-known/jwks.json",
        "GROWTH_AUTH_AUDIENCE": "authenticated",
        "GROWTH_API_HOST": "127.0.0.1",
        "GROWTH_API_PORT": str(cloud_port),
    })
    environment.pop("GROWTH_REME_URL", None)
    process: subprocess.Popen[bytes] | None = None
    checks: dict[str, bool] = {}
    observations: dict[str, object] = {}
    error: str | None = None
    try:
        with psycopg.connect(args.admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        with httpx.Client(timeout=10, trust_env=False) as client:
            def start_service(number: int) -> subprocess.Popen[bytes]:
                log = (run / f"cloud-{number}.log").open("wb")
                try:
                    child = subprocess.Popen(
                        [sys.executable, "-m", "ahadiff.growth.cloud"],
                        cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                    )
                finally:
                    log.close()
                try:
                    await_ready(child, origin)
                except Exception:
                    stop(child)
                    raise
                return child

            process = start_service(1)
            headers = {"Authorization": f"Bearer {token}"}
            first = client.get(f"{origin}/v1/account", headers=headers)
            anonymous = client.get(f"{origin}/v1/account")
            invalid = client.get(
                f"{origin}/v1/account", headers={"Authorization": f"Bearer {wrong_issuer}"},
            )
            observations["first_account_status"] = first.status_code
            observations["anonymous_status"] = anonymous.status_code
            observations["wrong_issuer_status"] = invalid.status_code
            checks["jwks_auth_and_account"] = (
                first.status_code == 200
                and first.json().get("account_id") == str(account)
                and JwksHandler.requests_seen >= 1
            )
            checks["auth_rejects_missing_and_wrong_issuer"] = (
                anonymous.status_code == invalid.status_code == 401
            )
            payload = {"operation_id": str(operation), "device_id": str(device)}
            registered = client.post(f"{origin}/v1/devices", headers=headers, json=payload)
            observations["register_status"] = registered.status_code
            receipt = registered.json()
            checks["production_write_persisted"] = (
                registered.status_code == 201 and receipt.get("device_id") == str(device)
            )
            stop(process)
            process = start_service(2)
            second = client.get(f"{origin}/v1/account", headers=headers)
            replay = client.post(f"{origin}/v1/devices", headers=headers, json=payload)
            observations["restart_account_status"] = second.status_code
            observations["replay_status"] = replay.status_code
            checks["restart_preserves_authenticated_account"] = (
                second.status_code == 200 and second.json().get("account_id") == str(account)
            )
            checks["restart_idempotent_receipt"] = (
                replay.status_code == registered.status_code and replay.json() == receipt
            )
        with connect(dsn) as conn:
            versions = {
                row["version"]: row["sha256"].strip()
                for row in conn.execute("SELECT version, sha256 FROM growth_schema_migrations")
            }
            devices = conn.execute(
                "SELECT COUNT(*) AS n FROM growth_devices "
                "WHERE account_id=%s AND device_id=%s", (account, device),
            ).fetchone()["n"]
            changes = conn.execute(
                "SELECT COUNT(*) AS n FROM growth_change_feed WHERE account_id=%s",
                (account,),
            ).fetchone()["n"]
        expected = {file.stem for file in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")}
        checks["all_production_migrations_applied_once"] = set(versions) == expected
        checks["one_device_and_one_change_after_retry"] = devices == changes == 1
        observations.update({
            "migration_count": len(versions), "device_rows": devices,
            "change_rows": changes, "jwks_requests": JwksHandler.requests_seen,
            "device_id": str(device), "account_id": str(account),
        })
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        checks["run_error"] = False
    finally:
        stop(process)
        jwks_server.shutdown()
        jwks_server.server_close()
        jwks_thread.join(timeout=5)
        result = {
            "run_id": args.run_id,
            "mode": "production_entrypoint_local_postgres",
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
            f"# M1 生产入口本机烟测\n\n状态：**{result['status']}（本机 PostgreSQL）"
            f"/ M1 BLOCKED**。以生产命令启动成长云两次，使用独立数据库 "
            f"`{database}` 与临时 JWKS 签名身份；验证迁移、认证拒绝、设备写入、"
            "进程重启与原 operation_id 幂等回执。此运行不使用外部 Supabase、"
            "macOS 或真实模型，不计正式情境步骤。\n\n"
            "[机读结果](result.json)保留检查与 ID；私钥和访问令牌未落盘。\n",
            encoding="utf-8",
        )
        print(json.dumps({"run_id": args.run_id, "status": result["status"],
                          "checks": checks}, ensure_ascii=False))
        if result["status"] != "PASS":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
