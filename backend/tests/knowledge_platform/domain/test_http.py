"""Actual ASGI request/identity/envelope contracts without fixture success claims."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from ahadiff.knowledge_platform.auth import DomainAuthenticationMiddleware, TokenAuthenticator
from ahadiff.knowledge_platform.core_routes import create_core_routes

USER_TOKEN = "isolated-http-user-token-at-least-24"
AGENT_TOKEN = "isolated-http-agent-token-at-least-24"


@pytest.fixture
def client(domain, owner, reviewer):
    app = Starlette(
        routes=create_core_routes(domain),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware,
                authenticator=TokenAuthenticator({USER_TOKEN: owner, AGENT_TOKEN: reviewer}),
            )
        ],
    )
    with TestClient(app) as session:
        yield session


def test_authentication_and_owner_spoof_are_not_request_fields(client, domain, owner, source):
    response = client.get("/api/knowledge/v1/entries")
    assert response.status_code == 401
    response = client.post(
        "/api/knowledge/v1/changes",
        headers={"Authorization": "Bearer " + USER_TOKEN, "Idempotency-Key": "spoof"},
        json={
            "owner_id": "fake",
            "title": "Spoof",
            "body": "body",
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_agent_cannot_request_or_submit_human_decision(client, proposal):
    headers = {"Authorization": "Bearer " + AGENT_TOKEN, "Idempotency-Key": "human-spoof"}
    root = "/api/knowledge/v1/changes/" + proposal["change_id"]
    assert client.get(root + "/decision-challenge", headers=headers).status_code == 403
    assert (
        client.post(
            root + "/decisions",
            headers=headers,
            json={"decision": "approve", "expected_patch_hash": proposal["patch_hash"]},
        ).status_code
        == 403
    )


def test_http_envelope_and_read_revision(client, domain, writer, proposal):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    response = client.get(
        "/api/knowledge/v1/entries/" + entry["entry_id"],
        headers={"Authorization": "Bearer " + USER_TOKEN},
    )
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {
        "data",
        "task_id",
        "trace_id",
        "data_classification",
        "classification_labels",
    }
    assert result["data"]["revision"] == 1 and result["task_id"] is None
    assert result["trace_id"]


def test_invalid_json_and_idempotency_are_explicit(client):
    headers = {"Authorization": "Bearer " + USER_TOKEN}
    response = client.post("/api/knowledge/v1/changes", headers=headers, content="{bad")
    assert response.status_code == 422
    assert response.json()["error"]["retryable"] is False
