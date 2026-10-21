"""Memory-only synthetic pairing; never creates a Windows test credential."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor, TokenAuthenticator
from ahadiff.knowledge_platform.browser_sessions import (
    AUTH_PREFIX,
    COOKIE_NAME,
    LocalTestBrowserSessions,
    local_test_actor,
)
from ahadiff.knowledge_platform.errors import DomainError

ORIGIN = "http://127.0.0.1:8766"
TOKEN = "synthetic-pairing-test-token-not-real-0001"
WRONG_OWNER_TOKEN = "synthetic-other-owner-token-not-real-0002"
PREFIX = AUTH_PREFIX


def system():
    clock = [0.0]
    auth = TokenAuthenticator(
        {
            TOKEN: local_test_actor(),
            WRONG_OWNER_TOKEN: Actor("other", "other", "user", frozenset({"*"})),
        }
    )
    sessions = LocalTestBrowserSessions(ORIGIN, auth, clock=lambda: clock[0])

    async def protected(request):
        try:
            actor = sessions.authenticate(request.scope)
            assert actor == local_test_actor()
            return JSONResponse({"status": "fixture-authorized"})
        except DomainError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status_code)

    app = Starlette(
        routes=sessions.routes()
        + [Route("/api/knowledge/v1/fixture", protected, methods=["GET", "POST"])]
    )
    return app, sessions, clock


async def pairing(browser, app):
    pending = await browser.post(PREFIX + "/request", headers={"Origin": ORIGIN}, json={})
    assert pending.status_code == 200
    browser.headers["X-Wiki-Pairing-Proof"] = pending.json()["pairing_proof"]
    code = pending.json()["pairing_code"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=ORIGIN
    ) as cli:
        approved = await cli.post(
            PREFIX + "/approve", headers={"Authorization": "Bearer " + TOKEN},
            json={"pairing_code": code},
        )
    assert approved.json() == {"status": "pairing_approved", "fixture": True}
    status = await browser.get(PREFIX + "/session")
    assert status.json()["status"] == "authenticated"
    return pending, status


def test_pairing_proves_bearer_without_sending_it_to_browser_and_rotates_cookie():
    app, sessions, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            initial = await browser.post(PREFIX + "/request", headers={"Origin": ORIGIN}, json={})
            code = initial.json()["pairing_code"]
            browser.headers["X-Wiki-Pairing-Proof"] = initial.json()["pairing_proof"]
            pending_cookie = browser.cookies.get(COOKIE_NAME)
            assert (await browser.get("/api/knowledge/v1/fixture")).status_code == 401
            assert "HttpOnly" in initial.headers["set-cookie"]
            assert "SameSite=strict" in initial.headers["set-cookie"]
            assert "Domain=" not in initial.headers["set-cookie"]
            assert "Path=/api/knowledge/v1" in initial.headers["set-cookie"]
            assert "Max-Age" not in initial.headers["set-cookie"]
            assert TOKEN not in initial.text and pending_cookie not in repr(sessions.__dict__)
            for token, expected in [(None, 401), ("incorrect", 401), (WRONG_OWNER_TOKEN, 403)]:
                denied = await browser.post(
                    PREFIX + "/approve", json={"pairing_code": code},
                    headers={"Authorization": "Bearer " + token} if token else {},
                )
                assert denied.status_code == expected
            approved = await browser.post(
                PREFIX + "/approve", json={"pairing_code": code},
                headers={"Authorization": "Bearer " + TOKEN},
            )
            assert approved.status_code == 200 and "set-cookie" not in approved.headers
            repeated = await browser.post(
                PREFIX + "/approve", json={"pairing_code": code},
                headers={"Authorization": "Bearer " + TOKEN},
            )
            assert repeated.status_code == 409
            status = await browser.get(PREFIX + "/session")
            assert status.json()["status"] == "authenticated"
            assert browser.cookies.get(COOKIE_NAME) != pending_cookie
            assert TOKEN not in status.text and "owner_id" not in status.json()
            assert (await browser.get("/api/knowledge/v1/fixture")).status_code == 200
            old = await browser.get(
                "/api/knowledge/v1/fixture", headers={"Cookie": f"{COOKIE_NAME}={pending_cookie}"}
            )
            assert old.status_code == 401
    asyncio.run(check())


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-Wiki-CSRF": "wrong"},
     {"Origin": "http://evil.test", "X-Wiki-CSRF": "valid"}],
)
def test_cookie_writes_require_both_exact_origin_and_csrf(headers):
    app, _, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            _, status = await pairing(browser, app)
            values = dict(headers)
            if values.get("X-Wiki-CSRF") == "valid":
                values["X-Wiki-CSRF"] = status.json()["csrf_token"]
            denied = await browser.post("/api/knowledge/v1/fixture", headers=values)
            assert denied.status_code == 403
            allowed = await browser.post(
                "/api/knowledge/v1/fixture",
                headers={"Origin": ORIGIN, "X-Wiki-CSRF": status.json()["csrf_token"]},
            )
            assert allowed.status_code == 200
    asyncio.run(check())


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "null"}, {"Origin": "http://evil.test"}, {"Host": "evil.test"},
     {"Sec-Fetch-Site": "cross-site"}, [("Origin", ORIGIN), ("Origin", ORIGIN)]],
)
def test_cross_origin_host_rebinding_and_duplicate_origin_are_rejected(headers):
    app, _, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            _, _ = await pairing(browser, app)
            response = await browser.get("/api/knowledge/v1/fixture", headers=headers)
            assert response.status_code == 403
    asyncio.run(check())


def test_pending_and_authenticated_sessions_expire_and_logout_revokes():
    app, _, clock = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            pending = await browser.post(PREFIX + "/request", headers={"Origin": ORIGIN}, json={})
            clock[0] = 121
            expired = await browser.post(
                PREFIX + "/approve", json={"pairing_code": pending.json()["pairing_code"]},
                headers={"Authorization": "Bearer " + TOKEN},
            )
            assert expired.status_code == 409
            assert (await browser.get(PREFIX + "/session")).json()["status"] == "waiting_auth"
            _, status = await pairing(browser, app)
            missing_csrf = await browser.post(PREFIX + "/logout", headers={"Origin": ORIGIN})
            assert missing_csrf.status_code == 403
            old_cookie = browser.cookies.get(COOKIE_NAME)
            logged_out = await browser.post(
                PREFIX + "/logout",
                headers={"Origin": ORIGIN, "X-Wiki-CSRF": status.json()["csrf_token"]},
            )
            assert logged_out.json()["status"] == "waiting_auth"
            assert (await browser.get(
                "/api/knowledge/v1/fixture", headers={"Cookie": f"{COOKIE_NAME}={old_cookie}"}
            )).status_code == 401
            await pairing(browser, app)
            clock[0] += 901
            assert (await browser.get("/api/knowledge/v1/fixture")).status_code == 401
    asyncio.run(check())


def test_pairing_is_bounded_and_cannot_choose_owner_or_accept_oversized_input():
    app, _, clock = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            for data in ({"owner_id": "other"}, {"text": "x" * 5000}):
                denied = await browser.post(
                    PREFIX + "/request", headers={"Origin": ORIGIN}, json=data
                )
                assert denied.status_code == 422
            for _ in range(20):
                assert (await browser.post(
                    PREFIX + "/request", headers={"Origin": ORIGIN}, json={}
                )).status_code == 200
            assert (await browser.post(
                PREFIX + "/request", headers={"Origin": ORIGIN}, json={}
            )).status_code == 429
            clock[0] = 61
            assert (await browser.post(
                PREFIX + "/request", headers={"Origin": ORIGIN}, json={}
            )).status_code == 200
    asyncio.run(check())


@pytest.mark.parametrize(
    "origin",
    ["http://example.com:8766", "http://localhost:8766", "https://127.0.0.1:8766",
     "http://127.0.0.1:8766/", "http://127.0.0.1:8766?token=x",
     "http://user:secret@127.0.0.1:8766", "http://127.0.0.1:80"],
)
def test_configuration_cannot_expand_to_remote_or_credentialed_origins(origin):
    with pytest.raises(ValueError):
        LocalTestBrowserSessions(origin, TokenAuthenticator({}))


def test_foreign_client_never_uses_pairing_even_with_same_host_origin():
    app, _, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, client=("203.0.113.1", 1234)), base_url=ORIGIN
        ) as remote:
            result = await remote.post(PREFIX + "/request", headers={"Origin": ORIGIN}, json={})
            assert result.status_code == 403
    asyncio.run(check())


def test_application_uses_only_verified_cookie_and_never_falls_back_from_bad_bearer(tmp_path):
    actor = local_test_actor()
    sessions = LocalTestBrowserSessions(ORIGIN, TokenAuthenticator({TOKEN: actor}))
    app = create_application(
        tmp_path / "state", tokens={TOKEN: actor}, local_test_browser_sessions=sessions
    )
    other = Actor("another-fixture-owner", "other", "user", frozenset({"*"}))
    other_task = app.state.domain.create_task(
        "fixture.task", {"data_classification": "public"},
        actor=other, idempotency_key="other-task",
    )

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            assert (await browser.get("/api/knowledge/v1/tasks")).status_code == 401
            assert (await browser.get(PREFIX + "/session")).json()["status"] == "waiting_auth"
            _, status = await pairing(browser, app)
            assert (await browser.get("/api/knowledge/v1/tasks")).json()["data"] == {
                "items": [], "has_more": False,
            }
            foreign = await browser.get(
                "/api/knowledge/v1/tasks/" + other_task["task_id"],
                headers={"X-Owner-Id": other.owner_id},
            )
            assert foreign.status_code == 404
            explicit_bad = await browser.get(
                "/api/knowledge/v1/tasks", headers={"Authorization": "Bearer wrong"}
            )
            assert explicit_bad.status_code == 401
            unauthorized_write = await browser.post(
                "/api/knowledge/v1/tasks/missing/resume", json={},
                headers={"Origin": ORIGIN},
            )
            assert unauthorized_write.status_code == 403
            authorized_write = await browser.post(
                "/api/knowledge/v1/tasks/missing/resume", json={},
                headers={"Origin": ORIGIN, "X-Wiki-CSRF": status.json()["csrf_token"]},
            )
            assert authorized_write.status_code == 404  # Auth passed, no fake task exists.
    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


def test_application_rejects_pairing_with_production_or_unconfigured_identity(tmp_path):
    sessions = LocalTestBrowserSessions(ORIGIN, TokenAuthenticator({TOKEN: local_test_actor()}))
    for tokens in (
        {},
        {TOKEN: Actor("not-test", "not-test", "user", frozenset({"*"}))},
    ):
        with pytest.raises(ValueError):
            create_application(
                tmp_path / "state", tokens=tokens, local_test_browser_sessions=sessions
            )


@pytest.mark.parametrize(
    "configuration",
    [{"start_worker": True}, {"model_runner": object()}, {"workflow_settings": object()},
     {"wechat_adapter": object()}, {"domain": object()}, {"intake_service": object()},
     {"channel_supervisor": object()}, {"media_processor": object()}],
)
def test_test_browser_mode_cannot_enable_background_or_external_services(tmp_path, configuration):
    sessions = LocalTestBrowserSessions(ORIGIN, TokenAuthenticator({TOKEN: local_test_actor()}))
    with pytest.raises(ValueError, match="cannot install background"):
        create_application(
            tmp_path / "state", tokens={TOKEN: local_test_actor()},
            local_test_browser_sessions=sessions, **configuration,
        )


def test_cookie_alone_never_returns_session_csrf_or_authenticated_data():
    app, sessions, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            pending, _ = await pairing(browser, app)
            proof = browser.headers.pop("X-Wiki-Pairing-Proof")
            assert proof == pending.json()["pairing_proof"]
            assert proof not in repr(sessions.__dict__)
            for target in (PREFIX + "/session", "/api/knowledge/v1/fixture"):
                assert (await browser.get(target)).status_code == 401
                assert (await browser.get(
                    target, headers={"X-Wiki-Pairing-Proof": "wrong"}
                )).status_code == 401
                assert (await browser.get(
                    target, headers={"X-Wiki-Pairing-Proof": proof}
                )).status_code == 200
    asyncio.run(check())


def test_application_rebinds_pairing_approval_to_the_same_bearer_source(tmp_path):
    impostor = "synthetic-wrong-controller-source-0003"
    sessions = LocalTestBrowserSessions(
        ORIGIN, TokenAuthenticator({impostor: local_test_actor()})
    )
    app = create_application(
        tmp_path / "state", tokens={TOKEN: local_test_actor()},
        local_test_browser_sessions=sessions,
    )

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as browser:
            pending = await browser.post(PREFIX + "/request", headers={"Origin": ORIGIN})
            result = await browser.post(
                PREFIX + "/approve", json={"pairing_code": pending.json()["pairing_code"]},
                headers={"Authorization": "Bearer " + impostor},
            )
            assert result.status_code == 401
            await pairing(browser, app)
    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


def test_approved_pending_cannot_overflow_the_concurrent_session_cap():
    app, sessions, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as first:
            await pairing(first, app)
        existing = next(iter(sessions._sessions.values()))
        for index in range(14):
            sessions._sessions[f"synthetic-cap-fixture-{index}"] = existing
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as pending:
            request = await pending.post(PREFIX + "/request", headers={"Origin": ORIGIN})
            pending.headers["X-Wiki-Pairing-Proof"] = request.json()["pairing_proof"]
            approved = await pending.post(
                PREFIX + "/approve", json={"pairing_code": request.json()["pairing_code"]},
                headers={"Authorization": "Bearer " + TOKEN},
            )
            assert approved.status_code == 200
            sessions._sessions["synthetic-concurrent-session"] = existing
            response = await pending.get(PREFIX + "/session")
            assert response.status_code == 429 and len(sessions._sessions) == 16
    asyncio.run(check())


def test_factory_rebinding_revokes_sessions_and_pending_codes_from_previous_source(tmp_path):
    original, sessions, _ = system()

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(original), base_url=ORIGIN
        ) as browser:
            await pairing(browser, original)
            old_cookie = browser.cookies.get(COOKIE_NAME)
            old_proof = browser.headers["X-Wiki-Pairing-Proof"]
            assert sessions._sessions
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(original), base_url=ORIGIN
            ) as another:
                pending = await another.post(PREFIX + "/request", headers={"Origin": ORIGIN})
                assert pending.status_code == 200 and sessions._pending and sessions._codes
            configured = create_application(
                tmp_path / "state", tokens={TOKEN: local_test_actor()},
                local_test_browser_sessions=sessions,
            )
            try:
                assert not sessions._sessions and not sessions._pending and not sessions._codes
                with pytest.raises(ValueError, match="one application"):
                    create_application(
                        tmp_path / "another-state", tokens={TOKEN: local_test_actor()},
                        local_test_browser_sessions=sessions,
                    )
                assert not (tmp_path / "another-state").exists()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(configured), base_url=ORIGIN
                ) as restarted:
                    result = await restarted.get(
                        "/api/knowledge/v1/tasks",
                        headers={
                            "Cookie": f"{COOKIE_NAME}={old_cookie}",
                            "X-Wiki-Pairing-Proof": old_proof,
                        },
                    )
                    assert result.status_code == 401
                    await pairing(restarted, configured)
                    assert (await restarted.get("/api/knowledge/v1/tasks")).status_code == 200
            finally:
                configured.state.domain.close()
    asyncio.run(check())
