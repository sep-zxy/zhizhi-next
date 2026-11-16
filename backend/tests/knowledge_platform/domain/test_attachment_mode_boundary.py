"""Synthetic ASGI factory boundaries; no file upload or identity activation on Windows."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor, TokenAuthenticator
from ahadiff.knowledge_platform.browser_sessions import LocalTestBrowserSessions, local_test_actor
from ahadiff.knowledge_platform.owner_browser import OwnerBrowserSessions

ORIGIN = "http://127.0.0.1:8766"
TOKEN = "synthetic-attachment-policy-not-a-real-credential"
PREFIX = "/api/knowledge/v1"


@pytest.mark.parametrize(
    "mode", ["bearer", "local_test_bearer", "local_test_cookie", "owner_cookie"]
)
def test_factory_preserves_fixture_upload_denial_and_owner_write_scope(tmp_path, mode):
    local_test = mode.startswith("local_test")
    actor = local_test_actor() if local_test else Actor(
        "synthetic-file-owner", "synthetic-person", "user",
        frozenset({"knowledge:read", "knowledge:write", "knowledge:review"}),
    )
    tokens = {TOKEN: actor}
    configuration = {}
    if local_test:
        configuration["local_test_browser_sessions"] = LocalTestBrowserSessions(
            ORIGIN, TokenAuthenticator(tokens)
        )
    elif mode == "owner_cookie":
        configuration["owner_browser_sessions"] = OwnerBrowserSessions(
            ORIGIN, TokenAuthenticator(tokens), owner_id=actor.owner_id, actor_id=actor.actor_id
        )
    app = create_application(tmp_path / "state", tokens=tokens, **configuration)

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as client:
            target = PREFIX + "/intakes/nonexistent/text-attachment"
            assert (
                await client.post(target, json={}, headers={"Origin": ORIGIN})
            ).status_code == 401
            if mode.endswith("cookie"):
                authentication = PREFIX + "/auth/" + ("local-test" if local_test else "owner")
                pending = await client.post(
                    authentication + "/request", headers={"Origin": ORIGIN}, json={}
                )
                assert pending.status_code == 200
                client.headers["X-Wiki-Pairing-Proof"] = pending.json()["pairing_proof"]
                approved = await client.post(
                    authentication + "/approve",
                    headers={"Authorization": "Bearer " + TOKEN},
                    json={"pairing_code": pending.json()["pairing_code"]},
                )
                assert approved.status_code == 200
                session = await client.get(authentication + "/session")
                assert session.json()["status"] == "authenticated"
                client.headers.update({
                    "Origin": ORIGIN, "X-Wiki-CSRF": session.json()["csrf_token"]
                })
            else:
                client.headers["Authorization"] = "Bearer " + TOKEN
            client.headers["Idempotency-Key"] = "synthetic-receive"
            received = await client.post(PREFIX + "/intakes", json={
                "text": "Synthetic original material only.",
                "data_classification": "public" if local_test else "personal",
            })
            assert received.status_code == 202, received.text
            intake_id = received.json()["data"]["intake_id"]
            target = PREFIX + f"/intakes/{intake_id}/text-attachment"
            client.headers["Idempotency-Key"] = "synthetic-file"
            payload = {
                "expected_preview_revision": 0,
                "filename": "synthetic.md",
                "content_type": "text/markdown",
                "content_base64": base64.b64encode(b"# Synthetic file\r\n").decode(),
                "private_note": "Synthetic annotation, not the source.",
            }
            if local_test:
                # The factory must reject even invalid encoding before the file adapter/cache.
                payload["content_base64"] = "intentionally-invalid-base64"
            result = await client.post(target, json=payload)
            if local_test:
                assert result.status_code == 403
                assert result.json()["error"]["code"] == "LOCAL_TEST_WRITES_DISABLED"
                record = app.state.domain.read_intake(intake_id, actor=actor)
                assert record["preview_revision"] == 0 and record["preview"] is None
                assert not list(app.state.domain.cache_path.rglob("*.json"))
            else:
                assert result.status_code == 200, result.text
                record = result.json()["data"]
                assert record["preview_revision"] == 1 and record["source_id"] is None
                assert record["data_classification"] == "personal"
                assert record["preview"]["document"]["body_text"] == "# Synthetic file\r\n"
                assert "content_base64" not in result.text and "cache_ref" not in result.text
            health = (await client.get("/health")).json()
            assert health["worker_enabled"] is False and health["automatic_commit_enabled"] is False

    try:
        asyncio.run(check())
    finally:
        app.state.domain.close()


def test_no_identity_shell_cannot_upload(tmp_path):
    app = create_application(tmp_path / "state", tokens={})

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url=ORIGIN
        ) as client:
            result = await client.post(PREFIX + "/intakes/missing/text-attachment", json={})
            assert result.status_code == 401
            assert (await client.get("/health")).json()["status"] == "waiting_auth"

    asyncio.run(check())
    assert not (tmp_path / "state").exists()
