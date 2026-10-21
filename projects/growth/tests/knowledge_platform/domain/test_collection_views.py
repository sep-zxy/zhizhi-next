"""Authenticated ASGI/SQLite collection tests; no real browser identity or model."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware

from ahadiff.knowledge_platform.auth import (
    Actor,
    DomainAuthenticationMiddleware,
    TokenAuthenticator,
)
from ahadiff.knowledge_platform.core_routes import create_core_routes
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.intake import create_intake_routes

USER_TOKEN = "fixture-collection-owner-token-at-least-24"
LIMITED_TOKEN = "fixture-collection-limited-token-at-least-24"


def application(domain, owner):
    restricted = Actor(
        owner.owner_id, "public-reader", "agent",
        frozenset({"knowledge:read"}), frozenset({"public"}),
    )
    return Starlette(
        routes=create_core_routes(domain) + create_intake_routes(domain),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware,
                authenticator=TokenAuthenticator(
                    {USER_TOKEN: owner, LIMITED_TOKEN: restricted}
                ),
            )
        ],
    )


def get(app, path, token=USER_TOKEN):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get(
                path, headers={"Authorization": "Bearer " + token} if token else {}
            )
    return asyncio.run(request())


@pytest.mark.parametrize("collection", ["tasks", "intakes"])
def test_collections_require_identity_and_have_honest_empty_states(domain, owner, collection):
    app = application(domain, owner)
    path = "/api/knowledge/v1/" + collection
    denied = get(app, path, token=None)
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "UNAUTHENTICATED"
    response = get(app, path)
    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "has_more": False}
    assert response.json()["task_id"] is None and response.json()["trace_id"]
    limited = get(app, path, token=LIMITED_TOKEN)
    assert limited.status_code == 200
    assert limited.json()["data"] == {"items": [], "has_more": False}


@pytest.mark.parametrize("collection", ["tasks", "intakes"])
def test_visible_pagination_does_not_count_hidden_or_other_owner_records(
    domain, owner, collection
):
    def create(actor, label, key):
        payload = {"text": key, "data_classification": label}
        if collection == "tasks":
            return domain.create_task(
                "fixture.task", payload, actor=actor, idempotency_key=key
            )
        return domain.create_intake(payload, actor=actor, idempotency_key=key)

    first = create(owner, "public", "first-visible")
    create(owner, "employer", "hidden-employer")
    second = create(owner, "public", "second-visible")
    other = Actor("another-owner", "another-human", "user", frozenset({"*"}))
    create(other, "public", "other-owner-hidden")
    app = application(domain, owner)
    path = "/api/knowledge/v1/" + collection
    id_key = "task_id" if collection == "tasks" else "intake_id"
    page1 = get(app, path + "?limit=1", token=LIMITED_TOKEN).json()["data"]
    page2 = get(app, path + "?limit=1&offset=1", token=LIMITED_TOKEN).json()["data"]
    assert [row[id_key] for row in page1["items"]] == [second[id_key]]
    assert page1["has_more"] is True
    assert [row[id_key] for row in page2["items"]] == [first[id_key]]
    assert page2["has_more"] is False
    assert get(app, path + "?offset=2", token=LIMITED_TOKEN).json()["data"] == {
        "items": [], "has_more": False,
    }
    assert "total" not in page1 and "owner_id" not in page1["items"][0]


@pytest.mark.parametrize(
    "collection,state", [("tasks", "waiting_auth"), ("intakes", "needs_input")]
)
def test_state_filter_reads_ledger_without_side_effects(domain, owner, collection, state):
    if collection == "tasks":
        record = domain.create_task(
            "fixture.task", {}, actor=owner, idempotency_key="task"
        )
        domain.update_task(
            record["task_id"], {"state": state},
            actor=owner, idempotency_key="waiting",
        )
    else:
        record = domain.create_intake({"text": "fixture"}, actor=owner, idempotency_key="intake")
        domain.update_intake(
            record["intake_id"], {"state": state},
            actor=owner, idempotency_key="waiting",
        )
    before = domain.list_events(actor=owner)
    response = get(application(domain, owner), f"/api/knowledge/v1/{collection}?state={state}")
    assert response.status_code == 200
    assert len(response.json()["data"]["items"]) == 1
    assert response.json()["data"]["items"][0]["state"] == state
    assert domain.list_events(actor=owner) == before


@pytest.mark.parametrize("collection", ["tasks", "intakes"])
@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "limit=no", "offset=-1", "offset=100001", "state=unknown",
     "owner_id=another-owner"],
)
def test_collection_query_bounds_are_explicit(domain, owner, collection, query):
    response = get(application(domain, owner), f"/api/knowledge/v1/{collection}?{query}")
    assert response.status_code == 422


def test_task_detail_and_list_hide_worker_leases_inputs_and_raw_failures(domain, owner):
    created = domain.create_task(
        "knowledge.generate", {"text": "never-leak-input"},
        actor=owner, idempotency_key="task",
    )
    claimed = domain.claim_task("worker", actor=owner)
    assert claimed is not None and claimed["lease_token"]
    diagnostic = {
        "stage": "execution", "kind": "transport", "summary": "transport_connection_failed",
        "event_type": "turn.failed", "exit_code": None, "raw_error": "never-leak-provider",
    }
    domain.update_task(
        created["task_id"],
        {
            "lease_token": claimed["lease_token"],
            "error": {"code": "PROVIDER_FAILURE", "detail": "never-leak-error"},
            "result": {
                "stage": "generate", "status": "failed", "entry_id": "entry-fixture",
                "output": {"text": "never-leak-output"},
                "model": {
                    "status": "failed", "error_code": "PROVIDER_FAILURE",
                    "failure": diagnostic, "output": {"text": "never-leak-model"},
                },
            },
        },
        actor=owner, idempotency_key="result",
    )
    app = application(domain, owner)
    detail = get(app, "/api/knowledge/v1/tasks/" + created["task_id"]).json()["data"]
    listed = get(app, "/api/knowledge/v1/tasks").json()["data"]["items"][0]
    assert detail == listed
    assert detail["error"] == {"code": "PROVIDER_FAILURE"}
    assert detail["result"]["model"]["failure"]["summary"] == "transport_connection_failed"
    assert "raw_error" not in detail["result"]["model"]["failure"]
    assert all(key not in detail for key in ("payload", "lease_token", "lease_owner", "owner_id"))
    assert claimed["lease_token"] not in json.dumps(detail)
    assert "never-leak" not in json.dumps(detail)
    # Internal workers retain the exact lease through the domain boundary.
    internal = domain.read_task(created["task_id"], actor=owner)
    assert internal["lease_token"] == claimed["lease_token"]


def test_intake_list_matches_public_single_record_shape_without_cache_paths(domain, owner):
    created = domain.create_intake({"text": "fixture"}, actor=owner, idempotency_key="intake")
    domain.update_intake(
        created["intake_id"],
        {"state": "preview_ready", "preview": {"summary": "summary", "cache_ref": "private-path"}},
        actor=owner, idempotency_key="preview",
    )
    listed = get(application(domain, owner), "/api/knowledge/v1/intakes").json()["data"]["items"][0]
    assert listed["preview"] == {"summary": "summary"}
    assert "payload" not in listed and "metadata" not in listed
    assert "private-path" not in json.dumps(listed)


def test_domain_collection_methods_enforce_read_scope(domain, owner):
    denied = Actor(owner.owner_id, "writer-only", "agent", frozenset({"knowledge:write"}))
    for method in (domain.list_tasks, domain.list_intakes):
        with pytest.raises(DomainError) as caught:
            method(actor=denied)
        assert caught.value.code == "FORBIDDEN"


def test_resume_response_uses_the_same_safe_task_projection(domain, owner):
    task = domain.create_task(
        "knowledge.generate", {"text": "never-leak-input"},
        actor=owner, idempotency_key="task",
    )
    domain.update_task(
        task["task_id"],
        {"state": "waiting_auth", "error": {"code": "SUBSCRIPTION_AUTH", "detail": "raw-secret"}},
        actor=owner, idempotency_key="waiting",
    )
    app = application(domain, owner)

    async def resume():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.post(
                f"/api/knowledge/v1/tasks/{task['task_id']}/resume",
                json={"expected_revision": 2},
                headers={
                    "Authorization": "Bearer " + USER_TOKEN,
                    "Idempotency-Key": "resume-fixture",
                },
            )

    response = asyncio.run(resume())
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["state"] == "queued" and data["revision"] == 3
    assert all(key not in data for key in ("payload", "lease_token", "lease_owner", "owner_id"))
    assert "never-leak" not in json.dumps(data) and "raw-secret" not in json.dumps(data)


def test_task_timeline_projects_payload_without_changing_internal_outbox(domain, owner):
    task = domain.create_task(
        "knowledge.generate", {}, actor=owner, idempotency_key="task"
    )
    with domain.store.transaction() as connection:
        domain.store.event(
            connection, "task.fixture.v1", task, task["task_id"],
            {
                "state": "waiting_auth", "attempt": 2, "reclaimed": True,
                "raw_error": "never-leak", "lease_token": "synthetic-lease",
                "reason": "arbitrary-private-detail",
            },
        )
    response = get(
        application(domain, owner), f"/api/knowledge/v1/tasks/{task['task_id']}/events"
    )
    assert response.status_code == 200
    event = response.json()["data"][-1]
    assert event["payload"] == {"state": "waiting_auth", "attempt": 2, "reclaimed": True}
    assert "owner_id" not in event
    assert "never-leak" not in json.dumps(event) and "synthetic-lease" not in json.dumps(event)
    internal = domain.task_events(task["task_id"], actor=owner)[-1]
    assert internal["payload"]["raw_error"] == "never-leak"


def test_public_only_reader_can_read_an_empty_authorized_task_timeline(domain, owner):
    task = domain.create_task(
        "fixture.task", {"data_classification": "public"},
        actor=owner, idempotency_key="public-task",
    )
    response = get(
        application(domain, owner),
        f"/api/knowledge/v1/tasks/{task['task_id']}/events?after=100000",
        token=LIMITED_TOKEN,
    )
    assert response.status_code == 200 and response.json()["data"] == []
