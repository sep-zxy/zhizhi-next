"""Real temporary ASGI owner protocol checks; only synthetic memory-only identity.

No secrets/cookies/raw responses are persisted or printed. Not live owner, browser,
Windows, model or full E2E acceptance. Run with the integrated backend source.
"""

from __future__ import annotations

import asyncio
import re
from tempfile import TemporaryDirectory

import httpx

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor, TokenAuthenticator
from ahadiff.knowledge_platform.owner_browser import OwnerBrowserSessions


async def main() -> None:
    origin = "http://127.0.0.1:8766"
    prefix = "/api/knowledge/v1/auth/owner"
    inert = "synthetic-owner-ui-contract-not-a-real-account"
    actor = Actor(
        "synthetic-owner-ui",
        "synthetic-person-ui",
        "user",
        frozenset({"knowledge:read"}),
    )
    tokens = {inert: actor}
    sessions = OwnerBrowserSessions(
        origin,
        TokenAuthenticator(tokens),
        owner_id=actor.owner_id,
        actor_id=actor.actor_id,
    )
    checks = 0
    with TemporaryDirectory(prefix="wiki-owner-contract-") as directory:
        app = create_application(
            directory,
            tokens=tokens,
            owner_browser_sessions=sessions,
            learning_viewer_url="http://127.0.0.1:49152/",
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=origin
            ) as browser:
                health = (await browser.get("/health")).json()
                assert health["browser_authentication_mode"] == "owner_loopback"
                assert health["learning_viewer_url"] == "http://127.0.0.1:49152/"
                assert (
                    await browser.get("/api/knowledge/v1/entries")
                ).status_code == 401
                checks += 3
                response = await browser.post(
                    prefix + "/request", headers={"Origin": origin}, json={}
                )
                assert response.status_code == 200
                pending = response.json()
                assert (
                    pending["status"] == "waiting_approval"
                    and pending["fixture"] is False
                )
                assert (
                    pending["authentication_mode"] == "owner_loopback"
                    and "scopes" not in pending
                )
                assert pending["expires_in_seconds"] == 120
                assert re.fullmatch(r"[A-F0-9]{20}", pending["pairing_code"])
                assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", pending["pairing_proof"])
                checks += 6
                browser.headers["X-Wiki-Pairing-Proof"] = pending["pairing_proof"]
                assert (await browser.get(prefix + "/session")).json() == {
                    "status": "waiting_approval",
                    "fixture": False,
                    "authentication_mode": "owner_loopback",
                }
                checks += 1
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url=origin
                ) as package_tool:
                    approved = await package_tool.post(
                        prefix + "/approve",
                        headers={"Authorization": "Bearer " + inert},
                        json={"pairing_code": pending["pairing_code"]},
                    )
                    assert (
                        approved.status_code == 200
                        and approved.json()["status"] == "pairing_approved"
                    )
                    checks += 1
                session = (await browser.get(prefix + "/session")).json()
                assert (
                    session["status"] == "authenticated" and session["fixture"] is False
                )
                assert session["authentication_mode"] == "owner_loopback"
                assert session["scopes"] == ["knowledge:read"]
                assert 1 <= session["expires_in_seconds"] <= 900
                assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", session["csrf_token"])
                checks += 5
                assert (await browser.get("/api/knowledge/v1/entries")).json()[
                    "data"
                ] == []
                denied = await browser.post(
                    "/api/knowledge/v1/intakes",
                    headers={
                        "Origin": origin,
                        "X-Wiki-CSRF": session["csrf_token"],
                        "Idempotency-Key": "synthetic-blocked",
                    },
                    json={"text": "Synthetic denied material."},
                )
                assert denied.status_code == 403
                checks += 2
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app),
                    base_url=origin,
                    cookies=browser.cookies,
                ) as refreshed:
                    assert (await refreshed.get(prefix + "/session")).status_code == 401
                    checks += 1
                logout = await browser.post(
                    prefix + "/logout",
                    json={},
                    headers={"Origin": origin, "X-Wiki-CSRF": session["csrf_token"]},
                )
                assert logout.json() == {
                    "status": "waiting_auth",
                    "fixture": False,
                    "authentication_mode": "owner_loopback",
                }
                assert (
                    await browser.get("/api/knowledge/v1/entries")
                ).status_code == 401
                checks += 2
        finally:
            app.state.domain.close()
    print(
        f"Owner ASGI contract: {checks} checks passed; synthetic memory-only identity, no live owner/browser E2E."
    )


if __name__ == "__main__":
    asyncio.run(main())
