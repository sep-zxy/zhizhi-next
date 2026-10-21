"""Navigation metadata over synthetic ASGI; never starts a listener or activates identity."""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest
import uvicorn

from ahadiff.knowledge_platform.__main__ import main
from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor, TokenAuthenticator
from ahadiff.knowledge_platform.browser_sessions import LocalTestBrowserSessions, local_test_actor
from ahadiff.knowledge_platform.navigation import learning_viewer_url
from ahadiff.knowledge_platform.owner_browser import OwnerBrowserSessions

URL = "http://127.0.0.1:9876/"
TOKEN = "synthetic-navigation-fixture-not-real"


@pytest.mark.parametrize("value", [
    "https://127.0.0.1:9876/", "http://localhost:9876/", "http://[::1]:9876/",
    "http://127.0.0.2:9876/", "http://192.0.2.1:9876/", "http://127.0.0.1:80/",
    "http://127.0.0.1:1023/", "http://127.0.0.1:65536/", "http://127.0.0.1:09876/",
    "http://127.0.0.1:9876", "http://127.0.0.1:9876/learn/", "http://127.0.0.1:9876//",
    "http://127.0.0.1:9876/?mode=other", "http://127.0.0.1:9876/#/growth",
    "http://person@127.0.0.1:9876/", " http://127.0.0.1:9876/",
    "http://127.0.0.1:9876/\n", "HTTP://127.0.0.1:9876/", "//127.0.0.1:9876/", "",
])
def test_rejects_noncanonical_or_credential_bearing_navigation(value, tmp_path):
    with pytest.raises(ValueError, match="canonical"):
        learning_viewer_url(value)
    with pytest.raises(ValueError, match="canonical"):
        create_application(tmp_path / "state", tokens={}, learning_viewer_url=value)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("port", [1024, 9876, 65535])
def test_accepts_only_explicit_canonical_ports(port):
    value = f"http://127.0.0.1:{port}/"
    assert learning_viewer_url(value) == value
    assert learning_viewer_url(None) is None


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("target", [None, URL])
def test_health_navigation_never_grants_api_access_or_starts_worker(tmp_path, configured, target):
    actor = Actor("synthetic-navigation-owner", "person", "user", frozenset({"knowledge:read"}))
    app = create_application(
        tmp_path / "state", tokens={TOKEN: actor} if configured else {}, learning_viewer_url=target
    )

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8766"
        ) as client:
            result = await client.get("/health")
            assert result.status_code == 200
            assert result.json()["learning_viewer_url"] == target
            assert result.json()["browser_authentication_status"] == "waiting_auth"
            assert result.json()["worker_enabled"] is False
            assert TOKEN not in result.text and "set-cookie" not in result.headers
            assert "access-control-allow-origin" not in result.headers
            assert (await client.get("/api/knowledge/v1/entries")).status_code == 401
            supplied = await client.get("/health", params={"learning_viewer_url": "ignored"})
            assert supplied.json()["learning_viewer_url"] == target
            assert (
                await client.post("/health", json={"learning_viewer_url": URL})
            ).status_code == 405

    try:
        asyncio.run(check())
    finally:
        if app.state.domain is not None:
            app.state.domain.close()
    if not configured:
        assert not (tmp_path / "state").exists()


def test_local_test_cannot_enable_viewer_and_owner_cannot_point_to_itself(tmp_path):
    test = local_test_actor()
    local = LocalTestBrowserSessions("http://127.0.0.1:8766", TokenAuthenticator({TOKEN: test}))
    with pytest.raises(ValueError, match="own non-test"):
        create_application(tmp_path / "test", tokens={TOKEN: test},
                           local_test_browser_sessions=local, learning_viewer_url=URL)
    actor = Actor("synthetic-navigation-owner", "person", "user", frozenset({"knowledge:read"}))
    owner = OwnerBrowserSessions("http://127.0.0.1:9876", TokenAuthenticator({TOKEN: actor}),
                                 owner_id=actor.owner_id, actor_id=actor.actor_id)
    with pytest.raises(ValueError, match="own non-test"):
        create_application(tmp_path / "owner", tokens={TOKEN: actor},
                           owner_browser_sessions=owner, learning_viewer_url=URL)
    assert not (tmp_path / "test").exists() and not (tmp_path / "owner").exists()


def test_cli_forwards_only_navigation_without_opening_a_service(tmp_path, monkeypatch):
    applications = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: applications.append(app))
    monkeypatch.setattr(sys, "argv", ["knowledge", "--state-directory", str(tmp_path / "state"),
                                     "--learning-viewer-url", URL])
    main()
    assert len(applications) == 1 and applications[0].state.domain is None
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("extra", [
    ["--learning-viewer-url", "http://127.0.0.1:8766/"],
    ["--learning-viewer-url", URL, "--host", "0.0.0.0"],
    ["--learning-viewer-url", URL, "--local-test-identity", "not-read.json"],
    ["--learning-viewer-url", "http://localhost:9876/"],
])
def test_cli_rejects_bad_navigation_before_credential_read_or_listen(tmp_path, monkeypatch, extra):
    def forbidden(*args, **kwargs):
        raise AssertionError("No service may be started")

    monkeypatch.setattr(uvicorn, "run", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "knowledge", "--state-directory", str(tmp_path / "state"), *extra
    ])
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / "state").exists()


def test_cli_error_does_not_echo_rejected_navigation(tmp_path, monkeypatch, capsys):
    sentinel = "NON_SECRET_REJECTED_URL_SENTINEL"
    monkeypatch.setattr(sys, "argv", [
        "knowledge", "--state-directory", str(tmp_path / "state"),
        "--learning-viewer-url", URL + "?mode=" + sentinel,
    ])
    with pytest.raises(SystemExit):
        main()
    assert sentinel not in capsys.readouterr().err
