"""Synthetic owner policy over real ASGI; no credential activation or model calls."""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor, TokenAuthenticator
from ahadiff.knowledge_platform.browser_sessions import LocalTestBrowserSessions, local_test_actor
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.owner_browser import OWNER_BROWSER_SCOPES, OwnerBrowserSessions

ORIGIN = "http://127.0.0.1:8766"
TOKEN = "synthetic-owner-browser-token-not-real-001"
OTHER = "synthetic-other-browser-token-not-real-002"
PREFIX = "/api/knowledge/v1/auth/owner"


def principal(scopes=frozenset({"*"})):
    return Actor("synthetic-owner", "synthetic-person", "user", scopes)


def system(tmp_path, scopes=frozenset({"*"})):
    clock = [0.0]
    actor = principal(scopes)
    sessions = OwnerBrowserSessions(
        ORIGIN,
        TokenAuthenticator({OTHER: actor}),
        owner_id=actor.owner_id,
        actor_id=actor.actor_id,
        clock=lambda: clock[0],
    )
    app = create_application(
        tmp_path / "state",
        tokens={TOKEN: actor},
        owner_browser_sessions=sessions,
    )
    return app, sessions, clock


async def pair(browser):
    pending = await browser.post(PREFIX + "/request", headers={"Origin": ORIGIN}, json={})
    assert pending.status_code == 200
    assert pending.json()["fixture"] is False
    assert pending.json()["authentication_mode"] == "owner_loopback"
    browser.headers["X-Wiki-Pairing-Proof"] = pending.json()["pairing_proof"]
    initial = browser.cookies.get("wiki_owner_session")
    approved = await browser.post(
        PREFIX + "/approve",
        headers={"Authorization": "Bearer " + TOKEN},
        json={"pairing_code": pending.json()["pairing_code"]},
    )
    assert approved.status_code == 200
    status = await browser.get(PREFIX + "/session")
    assert status.status_code == 200 and status.json()["status"] == "authenticated"
    assert browser.cookies.get("wiki_owner_session") != initial
    assert TOKEN not in status.text and "owner_id" not in status.text
    return pending, status


def test_owner_has_narrow_scopes_private_data_isolated_and_no_implicit_worker(tmp_path):
    app, sessions, _ = system(tmp_path)
    other = Actor("foreign-owner", "foreign-user", "user", frozenset({"*"}))
    foreign = app.state.domain.create_task("fixture", {}, actor=other, idempotency_key="foreign")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            health = (await browser.get("/health")).json()
            assert health["browser_authentication_mode"] == "owner_loopback"
            assert health["worker_enabled"] is False and health["automatic_commit_enabled"] is False
            assert (await browser.get("/api/knowledge/v1/tasks")).status_code == 401
            _, status = await pair(browser)
            assert frozenset(status.json()["scopes"]) == OWNER_BROWSER_SCOPES
            assert (await browser.get("/api/knowledge/v1/tasks")).json()["data"]["items"] == []
            assert (
                await browser.get("/api/knowledge/v1/tasks/" + foreign["task_id"])
            ).status_code == 404
            assert (
                await browser.get("/api/knowledge/v1/auth/local-test/session")
            ).status_code == 404
            with pytest.raises(ValueError):
                sessions.bind_authenticator(TokenAuthenticator({}))

    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


@pytest.mark.parametrize("authorization", ["", "Bearer wrong", "Basic invalid"])
def test_explicit_invalid_bearer_never_falls_back_on_api_or_auth_endpoints(tmp_path, authorization):
    app, _, _ = system(tmp_path)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            await pair(browser)
            for path in ["/api/knowledge/v1/tasks", PREFIX + "/session"]:
                assert (
                    await browser.get(path, headers={"Authorization": authorization})
                ).status_code == 401
            duplicate = [("Authorization", "Bearer " + TOKEN), ("Authorization", "Bearer " + TOKEN)]
            assert (
                await browser.get("/api/knowledge/v1/tasks", headers=duplicate)
            ).status_code == 401
            assert (await browser.get(PREFIX + "/session", headers=duplicate)).status_code == 403

    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


def test_cookie_alone_cannot_recover_proof_and_csrf_is_required_and_logout_revokes(tmp_path):
    app, _, _ = system(tmp_path)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            _, status = await pair(browser)
            proof = browser.headers.pop("X-Wiki-Pairing-Proof")
            assert (await browser.get(PREFIX + "/session")).status_code == 401
            assert (await browser.get("/api/knowledge/v1/tasks")).status_code == 401
            browser.headers["X-Wiki-Pairing-Proof"] = proof
            assert (
                await browser.post(PREFIX + "/logout", headers={"Origin": ORIGIN})
            ).status_code == 403
            for headers in [
                {"Origin": "http://127.0.0.1:8767"},
                {"Host": "evil.test"},
                {"Sec-Fetch-Site": "cross-site"},
            ]:
                assert (await browser.get(PREFIX + "/session", headers=headers)).status_code == 403
            response = await browser.post(
                PREFIX + "/logout",
                headers={"Origin": ORIGIN, "X-Wiki-CSRF": status.json()["csrf_token"]},
            )
            assert response.status_code == 200 and response.json()["status"] == "waiting_auth"
            assert (await browser.get("/api/knowledge/v1/tasks")).status_code == 401

    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


def test_owner_pending_and_session_expiry_replay_and_factory_auth_binding(tmp_path):
    app, _, clock = system(tmp_path)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            pending = (
                await browser.post(PREFIX + "/request", headers={"Origin": ORIGIN}, json={})
            ).json()
            assert (
                await browser.post(
                    PREFIX + "/approve",
                    headers={"Authorization": "Bearer " + OTHER},
                    json={"pairing_code": pending["pairing_code"]},
                )
            ).status_code == 401
            clock[0] = 120
            assert (
                await browser.post(
                    PREFIX + "/approve",
                    headers={"Authorization": "Bearer " + TOKEN},
                    json={"pairing_code": pending["pairing_code"]},
                )
            ).status_code == 409
            pending, _ = await pair(browser)
            assert (
                await browser.post(
                    PREFIX + "/approve",
                    headers={"Authorization": "Bearer " + TOKEN},
                    json={"pairing_code": pending.json()["pairing_code"]},
                )
            ).status_code == 409
            clock[0] += 900
            assert (await browser.get("/api/knowledge/v1/tasks")).status_code == 401

    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


@pytest.mark.parametrize(
    "actor",
    [
        Actor("another", "synthetic-person", "user", frozenset({"*"})),
        Actor("synthetic-owner", "another", "user", frozenset({"*"})),
        Actor("synthetic-owner", "synthetic-person", "service", frozenset({"*"})),
        principal(frozenset({"knowledge:write"})),
        local_test_actor(),
    ],
)
def test_owner_policy_rejects_other_principals_without_expanding_permissions(tmp_path, actor):
    sessions = OwnerBrowserSessions(
        ORIGIN, TokenAuthenticator({}), owner_id="synthetic-owner", actor_id="synthetic-person"
    )
    with pytest.raises(DomainError):
        sessions.approval_actor(actor)
    with pytest.raises((DomainError, ValueError)):
        create_application(
            tmp_path / "state", tokens={TOKEN: actor}, owner_browser_sessions=sessions
        )
    assert not (tmp_path / "state").exists()


def test_read_only_owner_remains_read_only_and_test_policy_is_separate(tmp_path):
    app, sessions, _ = system(tmp_path, frozenset({"knowledge:read", "knowledge:apply"}))
    assert sessions.approval_actor(
        principal(frozenset({"knowledge:read", "knowledge:apply"}))
    ).scopes == frozenset({"knowledge:read"})
    with pytest.raises(ValueError):
        create_application(
            tmp_path / "other",
            tokens={TOKEN: principal()},
            owner_browser_sessions=sessions,
            local_test_browser_sessions=LocalTestBrowserSessions(ORIGIN, TokenAuthenticator({})),
        )
    with pytest.raises(ValueError):
        create_application(tmp_path / "empty", tokens={}, owner_browser_sessions=sessions)
    app.state.domain.close()


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--local-test-identity", "must-not-read"],
        ["--identities-file", "must-not-read", "--owner", "owner", "--host", "0.0.0.0"],
    ],
)
def test_owner_cli_rejects_missing_existing_identity_or_unsafe_host_before_read(
    tmp_path, monkeypatch, extra
):
    from ahadiff.knowledge_platform.__main__ import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge",
            "--state-directory",
            str(tmp_path),
            "--owner-browser-actor",
            "person",
            *extra,
        ],
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": ""},
        [("Authorization", "Bearer " + TOKEN), ("Authorization", "Bearer " + TOKEN)],
    ],
)
def test_local_test_policy_also_refuses_empty_or_duplicate_explicit_bearer(tmp_path, headers):
    actor = local_test_actor()
    sessions = LocalTestBrowserSessions(ORIGIN, TokenAuthenticator({TOKEN: actor}))
    app = create_application(tmp_path, tokens={TOKEN: actor}, local_test_browser_sessions=sessions)
    prefix = sessions.auth_prefix

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            pending = (
                await browser.post(prefix + "/request", headers={"Origin": ORIGIN}, json={})
            ).json()
            browser.headers["X-Wiki-Pairing-Proof"] = pending["pairing_proof"]
            assert (
                await browser.post(
                    prefix + "/approve",
                    headers={"Authorization": "Bearer " + TOKEN},
                    json={"pairing_code": pending["pairing_code"]},
                )
            ).status_code == 200
            assert (await browser.get(prefix + "/session")).json()["status"] == "authenticated"
            assert (
                await browser.get("/api/knowledge/v1/tasks", headers=headers)
            ).status_code == 401
            assert (await browser.get(prefix + "/session", headers=headers)).status_code in {
                401,
                403,
            }

    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()
