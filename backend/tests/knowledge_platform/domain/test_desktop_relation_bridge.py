"""Cloud fixture integration through desktop/HTTP/real Git and SQLite, not live E2E."""

import asyncio

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from ahadiff.knowledge_platform.auth import (
    Actor,
    DomainAuthenticationMiddleware,
    TokenAuthenticator,
)
from ahadiff.knowledge_platform.learning.backend import (
    BackendError,
    DurableOutbox,
    RemoteKnowledgeBackend,
)
from ahadiff.knowledge_platform.learning.desktop import configure_desktop_backend
from ahadiff.knowledge_platform.relations import RelationService
from ahadiff.knowledge_platform.relations.routes import create_relation_routes
from ahadiff.serve import ServeState, create_app
from test_relations import commit, relation


@pytest.fixture
def bridge(domain, owner, reviewer, writer, source, tmp_path, monkeypatch):
    monkeypatch.setenv("AHADIFF_KNOWLEDGE_MODE", "unified")
    commit(domain, owner, reviewer, writer, source, "target")
    commit(domain, owner, reviewer, writer, source, "private", visibility="private")
    commit(domain, owner, reviewer, writer, source, "origin", body="[[target]] [[private]]",
           metadata={"relations": [relation(source)]}, covered=True)
    token = "fixture-relation-token-long-enough"
    outsider = Actor("other-owner", "other", "user", frozenset({"knowledge:read"}))
    app = Starlette(routes=create_relation_routes(RelationService(domain)), middleware=[
        Middleware(DomainAuthenticationMiddleware,
                   authenticator=TokenAuthenticator({token: owner, token + "-other": outsider}))
    ])
    remote = httpx.AsyncClient(transport=httpx.ASGITransport(app))
    backend = RemoteKnowledgeBackend("https://knowledge.test", token_provider=lambda: token,
                                    outbox=DurableOutbox(tmp_path / "outbox"), client=remote)
    configure_desktop_backend(backend, device_id="fixture-device")
    client = TestClient(create_app(ServeState(state_dir=tmp_path / "desktop", token="local-token")),
                        base_url="http://localhost:8765")
    yield client, backend, token
    client.close()
    asyncio.run(remote.aclose())
    configure_desktop_backend(None, device_id="fixture-device")


PREFIX = "/api/growth/local/knowledge/relations/origin"
HEADERS = {"X-AhaDiff-Token": "local-token", "Origin": "http://localhost:8765"}


def test_real_projection_is_forwarded_and_never_written(bridge, domain):
    client, backend, _ = bridge
    before = domain.writer.head()
    response = client.get(PREFIX, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()["data"]
    assert {edge["kind"] for edge in data["edges"]} == {"navigation", "confirmed"}
    confirmed = next(edge for edge in data["edges"] if edge["kind"] == "confirmed")
    assert confirmed["verification"] == "reviewed_evidence"
    assert confirmed["review_provenance"] and confirmed["evidence_refs"]
    assert data["inference"] == "none"
    assert response.headers["cache-control"] == "no-store"
    filtered = client.get(
        PREFIX + "?kind=confirmed&kind=candidate&visibility=public", headers=HEADERS
    )
    assert {edge["kind"] for edge in filtered.json()["data"]["edges"]} == {"confirmed"}
    assert "private" not in {node["entry_id"] for node in filtered.json()["data"]["nodes"]}
    bounded = client.get(PREFIX + "/traverse?max_nodes=1&max_depth=1", headers=HEADERS)
    assert bounded.json()["data"]["truncated"] is True
    assert len(bounded.json()["data"]["nodes"]) == 1
    assert backend.outbox.records() == []
    assert domain.writer.head() == before


def test_authentication_and_owner_scope_fail_closed(bridge):
    client, backend, token = bridge
    assert client.get(PREFIX).status_code == 401
    assert client.get(PREFIX, headers={"X-AhaDiff-Token": "wrong"}).status_code == 401
    backend.token_provider = lambda: token + "-other"
    assert client.get(PREFIX, headers=HEADERS).status_code == 404
    backend.token_provider = lambda: ""
    response = client.get(PREFIX, headers=HEADERS)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "WAITING_AUTH"


@pytest.mark.parametrize("query", [
    "?limit=0", "?limit=1001", "?limit=2&limit=3", "?limit=1.5", "?limit=١",
    "?kind=mastered", "?owner_id=other", "?direction=all", "?visibility=private",
    "?max_depth=2", "/traverse?max_depth=4", "/traverse?max_nodes=201",
    "/traverse?limit=5", "/traverse?max_depth=0",
])
def test_invalid_queries_never_become_unbounded_reads(bridge, query):
    client, _, _ = bridge
    assert client.get(PREFIX + query, headers=HEADERS).status_code == 422


def test_missing_configuration_and_read_only_methods(bridge):
    client, _, _ = bridge
    # The desktop catch-all returns 404 for methods without a registered route.
    assert client.post(PREFIX, headers=HEADERS).status_code == 404
    configure_desktop_backend(None, device_id="fixture-device")
    response = client.get(PREFIX, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WAITING_CONFIGURATION"


@pytest.mark.parametrize("entry_id", ["../x", "x?owner=other", "%2e%2e", "a/b", ""])
def test_path_injection_rejected_before_transport(bridge, entry_id):
    _, backend, _ = bridge
    with pytest.raises(BackendError, match="INVALID_INPUT"):
        asyncio.run(backend.read_relations(entry_id))


def test_target_revision_drift_is_not_presented_as_confirmation(
    bridge, domain, owner, reviewer, writer, source
):
    client, _, _ = bridge
    previous = domain.get_entry("target", actor=owner)
    commit(domain, owner, reviewer, writer, source, "target", previous=previous,
           body="Evidence says addition preserves history. Revised target.")
    response = client.get(PREFIX + "?kind=confirmed&kind=candidate", headers=HEADERS)
    assert response.status_code == 200
    edges = response.json()["data"]["edges"]
    assert len(edges) == 1 and edges[0]["kind"] == "candidate"
    assert edges[0]["is_fact"] is False
    assert edges[0]["target_revision"] == 2
    assert edges[0]["review_provenance"] is None


def test_remote_projection_conflict_stays_retryable_and_details_are_redacted(bridge):
    client, backend, _ = bridge
    async def stale(*args, **kwargs):
        raise BackendError("PROJECTION_STALE", "private upstream path /secret", True, 409)
    backend.read_relations = stale
    response = client.get(PREFIX, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECTION_STALE"
    assert response.json()["error"]["retryable"] is True
    assert "secret" not in response.text
