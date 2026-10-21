"""Real loopback HTTP and process restart, without external account credentials.

This exercises the shipping entry point, not an in-process ASGI transport. It is
not evidence of successful subscription generation or external channel delivery.
"""

import contextlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time

import httpx


@contextlib.contextmanager
def running_service(tmp_path, port, token):
    identities = tmp_path / "identities.json"
    identities.write_text(json.dumps({"identities": [{"owner_id": "process-owner",
        "actor_id": "process-user", "kind": "user", "scopes": ["*"], "token": token}]}))
    identities.chmod(0o600)
    with (tmp_path / "service.log").open("ab") as log:
        process = subprocess.Popen([
            sys.executable, "-m", "ahadiff.knowledge_platform",
            "--state-directory", str(tmp_path / "state"),
            "--identities-file", str(identities), "--owner", "process-owner",
            "--host", "127.0.0.1", "--port", str(port), "--run-worker",
        ], env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2,
                              trust_env=False) as client:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "Service exited before readiness"
                    try:
                        response = client.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "Service readiness timed out"
                    time.sleep(0.05)
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_shipping_service_auth_persistence_and_no_implicit_model(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    token = secrets.token_urlsafe(32)
    with running_service(tmp_path, port, token) as client:
        assert client.get("/api/knowledge/v1/entries").status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        response = client.post("/api/knowledge/v1/intakes", json={
            "text": "事务边界决定跨服务一致性方案，应权衡补偿与强一致性。"},
            headers={"Idempotency-Key": "process-intake"})
        assert response.status_code == 202, response.text
        intake_id = response.json()["data"]["intake_id"]
        assert client.get("/health").json()["publication_enabled"] is False
    with running_service(tmp_path, port, token) as client:
        client.headers["Authorization"] = "Bearer " + token
        response = client.get("/api/knowledge/v1/intakes/" + intake_id)
        assert response.status_code == 200, response.text
        # Neither restart nor a disabled provider can promote an unkept source.
        entries = client.get("/api/knowledge/v1/entries")
        assert entries.status_code == 200
        assert intake_id == response.json()["data"]["intake_id"]
    assert token not in (tmp_path / "service.log").read_text()
