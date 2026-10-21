"""Memory-only ASGI pairing contract smoke; no Windows identity or browser E2E.

No pairing code, proof, CSRF, cookie, bearer, or raw response is persisted or logged.
The only identity input is an inert synthetic constant in this isolated test.
"""

from __future__ import annotations

import asyncio
import re
from tempfile import TemporaryDirectory

import httpx

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import TokenAuthenticator
from ahadiff.knowledge_platform.browser_sessions import (
    AUTH_PREFIX,
    LocalTestBrowserSessions,
    local_test_actor,
)


async def main() -> None:
    origin = "http://127.0.0.1:8766"
    inert = "synthetic-only-wiki-contract-no-real-account"
    tokens = {inert: local_test_actor()}
    sessions = LocalTestBrowserSessions(origin, TokenAuthenticator(tokens))
    checks = 0
    with TemporaryDirectory(prefix="wiki-local-auth-contract-") as directory:
        app = create_application(
            directory, tokens=tokens, local_test_browser_sessions=sessions,
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url=origin,
            ) as browser:
                assert (await browser.get("/api/knowledge/v1/entries")).status_code == 401
                checks += 1
                pending = await browser.post(
                    AUTH_PREFIX + "/request", headers={"Origin": origin}, json={},
                )
                assert pending.status_code == 200
                value = pending.json()
                assert value["status"] == "waiting_approval" and value["fixture"] is True
                assert value["expires_in_seconds"] == 120
                assert re.fullmatch(r"[A-F0-9]{20}", value["pairing_code"])
                assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value["pairing_proof"])
                checks += 5
                browser.headers["X-Wiki-Pairing-Proof"] = value["pairing_proof"]
                status = await browser.get(AUTH_PREFIX + "/session")
                assert status.json() == {"status": "waiting_approval", "fixture": True}
                checks += 1
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url=origin,
                ) as package_tool:
                    approval = await package_tool.post(
                        AUTH_PREFIX + "/approve",
                        headers={"Authorization": "Bearer " + inert},
                        json={"pairing_code": value["pairing_code"]},
                    )
                    assert approval.json() == {"status": "pairing_approved", "fixture": True}
                    checks += 1
                status = await browser.get(AUTH_PREFIX + "/session")
                session = status.json()
                assert session["status"] == "authenticated" and session["fixture"] is True
                assert 1 <= session["expires_in_seconds"] <= 900
                assert re.fullmatch(r"[A-Za-z0-9_-]{32,128}", session["csrf_token"])
                checks += 3
                entries = await browser.get("/api/knowledge/v1/entries?limit=100&offset=0")
                assert entries.status_code == 200 and entries.json()["data"] == []
                checks += 1
                for resource in ("tasks", "intakes"):
                    listing = await browser.get(f"/api/knowledge/v1/{resource}?limit=100&offset=0")
                    assert listing.status_code == 200
                    assert listing.json()["data"] == {"items": [], "has_more": False}
                    checks += 1
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url=origin,
                    cookies=browser.cookies,
                ) as refreshed_without_memory:
                    assert (await refreshed_without_memory.get(AUTH_PREFIX + "/session")).status_code == 401
                    checks += 1
                logout = await browser.post(
                    AUTH_PREFIX + "/logout", json={},
                    headers={"Origin": origin, "X-Wiki-CSRF": session["csrf_token"]},
                )
                assert logout.json() == {"status": "waiting_auth", "fixture": True}
                assert (await browser.get("/api/knowledge/v1/entries")).status_code == 401
                checks += 2
        finally:
            app.state.domain.close()
    print(f"Local-test ASGI contract: {checks} checks passed; synthetic memory-only flow, no Windows/browser E2E.")


if __name__ == "__main__":
    asyncio.run(main())
