"""Real ASGI assembly with synthetic identities; no listening socket or login."""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest
import uvicorn

from ahadiff.knowledge_platform.__main__ import main
from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor

TOKEN = "synthetic-private-shell-test-token-not-real"


@pytest.fixture
def compiled(tmp_path):
    root = tmp_path / "app" / "wiki-private"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>private Wiki shell fixture</html>", encoding="utf-8")
    return root


def request(app, path, *, method="GET", headers=None):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8766"
        ) as client:
            return await client.request(method, path, headers=headers)
    return asyncio.run(run())


def test_unconfigured_application_does_not_initialize_domain_or_fabricate_ready(tmp_path, compiled):
    state = tmp_path / "state"
    app = create_application(state, tokens={}, private_web_root=compiled)
    health = request(app, "/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "waiting_auth", "service": "zhizhi-knowledge",
        "authentication_status": "waiting_auth",
        "browser_authentication_status": "waiting_auth",
        "private_wiki_enabled": True, "worker_enabled": False,
        "channel_worker_enabled": False, "channel_status": "disabled",
        "automatic_commit_enabled": False, "publication_enabled": False,
    }
    assert request(app, "/wiki/").status_code == 200
    assert app.state.domain is None and app.state.workflow is None
    assert not state.exists()


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "OPTIONS"])
@pytest.mark.parametrize(
    "path",
    ["/api/knowledge/v1/tasks", "/api/knowledge/v1/intakes",
     "/api/knowledge/v1/channels/wechat-kf/callback"],
)
def test_no_identity_domain_routes_fail_closed_even_with_forged_browser_claims(
    tmp_path, compiled, method, path
):
    app = create_application(tmp_path / "state", tokens={}, private_web_root=compiled)
    response = request(
        app, path + "?owner_id=forged", method=method,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Cookie": "wiki_session=synthetic-forged-session",
            "X-Owner-Id": "forged", "Origin": "http://127.0.0.1:8766",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    "option",
    ["model_runner", "workflow_settings", "wechat_adapter", "domain",
     "intake_service", "channel_supervisor", "media_processor"],
)
def test_no_identity_factory_rejects_backend_injection(tmp_path, option):
    with pytest.raises(ValueError, match="Unauthenticated shell"):
        create_application(tmp_path / "state", tokens={}, **{option: object()})
    assert not (tmp_path / "state").exists()


def test_no_identity_factory_rejects_worker(tmp_path):
    with pytest.raises(ValueError, match="Unauthenticated shell"):
        create_application(tmp_path / "state", tokens={}, start_worker=True)


def test_existing_bearer_identity_remains_required_and_compatible(tmp_path, compiled):
    owner = Actor("fixture-owner", "fixture-person", "user", frozenset({"*"}))
    app = create_application(
        tmp_path / "state", tokens={TOKEN: owner}, private_web_root=compiled
    )
    try:
        assert request(app, "/wiki/").status_code == 200
        health = request(app, "/health").json()
        assert health["status"] == "ready" and health["authentication_status"] == "configured"
        assert health["browser_authentication_status"] == "waiting_auth"
        assert request(app, "/api/knowledge/v1/tasks").status_code == 401
        authenticated = request(
            app, "/api/knowledge/v1/tasks", headers={"Authorization": "Bearer " + TOKEN}
        )
        assert authenticated.status_code == 200
        assert authenticated.json()["data"] == {"items": [], "has_more": False}
        class FailedBackground:
            def done(self):
                return True

            def cancelled(self):
                return False

        app.state.background_tasks = [FailedBackground()]
        degraded = request(app, "/health")
        assert degraded.status_code == 503 and degraded.json()["status"] == "degraded"
    finally:
        app.state.domain.close()


def test_cli_no_identity_launches_only_shell_with_frozen_mount_flag(
    tmp_path, compiled, monkeypatch
):
    launched = {}

    def capture(app, **kwargs):
        launched.update(app=app, **kwargs)

    monkeypatch.setattr(uvicorn, "run", capture)
    monkeypatch.setattr(
        sys, "argv",
        ["knowledge", "--state-directory", str(tmp_path / "state"),
         "--private-web-root", str(compiled), "--port", "8766"],
    )
    main()
    assert launched["host"] == "127.0.0.1" and launched["access_log"] is False
    assert request(launched["app"], "/health").json()["status"] == "waiting_auth"
    assert request(launched["app"], "/wiki/").status_code == 200
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "flags",
    [["--run-worker"], ["--auto-commit"], ["--owner", "forged"],
     ["--model-config", "must-not-read.json"], ["--channel-config", "must-not-read.json"],
     ["--asr-model-directory", "must-not-read"], ["--provider", "openai"],
     ["--identities-file", "must-not-read.json"]],
)
def test_cli_rejects_unpaired_identity_or_unconfigured_background_work(
    tmp_path, monkeypatch, flags
):
    monkeypatch.setattr(
        sys, "argv", ["knowledge", "--state-directory", str(tmp_path / "state"), *flags]
    )
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    assert not (tmp_path / "state").exists()
