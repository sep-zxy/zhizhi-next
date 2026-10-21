"""Real ASGI/SQLite/Git contract tests with isolated synthetic actors, not live E2E."""

import asyncio

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

TOKEN = "inert-private-actions-owner-token"


def request(domain, owner, method, path, body=None, key="action", actor=None):
    app = Starlette(
        routes=create_core_routes(domain),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware,
                authenticator=TokenAuthenticator({TOKEN: actor or owner}),
            )
        ],
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            return await client.request(
                method,
                "/api/knowledge/v1" + path,
                json=body,
                headers={"Authorization": "Bearer " + TOKEN, "Idempotency-Key": key},
            )

    return asyncio.run(run())


@pytest.mark.parametrize(
    "action,initial,expected",
    [("resume", "waiting_auth", "queued"), ("cancel", "queued", "cancelled")],
)
def test_control_exact_replay_precedes_changed_state(domain, owner, action, initial, expected):
    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")
    if initial != "queued":
        task = domain.update_task(
            task["task_id"],
            {"state": initial, "checkpoint": {"stage": "preserved"}},
            actor=owner,
            idempotency_key="wait",
        )
    path = f"/tasks/{task['task_id']}/{action}"
    body = {"expected_revision": task["revision"]}
    first = request(domain, owner, "POST", path, body)
    assert first.status_code == 200
    assert first.json()["data"]["state"] == expected
    again = request(domain, owner, "POST", path, body)
    assert again.status_code == 200 and again.json()["data"] == first.json()["data"]
    assert domain.read_task(task["task_id"], actor=owner)["revision"] == task["revision"] + 1
    assert "lease_token" not in first.json()["data"] and "payload" not in first.json()["data"]
    stale = request(domain, owner, "POST", path, body, key="other")
    assert stale.status_code == 409
    conflict = request(domain, owner, "POST", path, {"expected_revision": task["revision"] + 1})
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"expected_revision": True},
        {"expected_revision": 0},
        {"expected_revision": 1, "owner_id": "injected"},
    ],
)
def test_control_requires_exact_positive_revision_payload(domain, owner, body):
    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")
    response = request(domain, owner, "POST", f"/tasks/{task['task_id']}/cancel", body)
    assert response.status_code == 422
    assert domain.read_task(task["task_id"], actor=owner)["state"] == "queued"


@pytest.mark.parametrize("action", ["cancel", "resume"])
def test_control_does_not_steal_running_lease_or_accept_machine_actor(
    domain, owner, generator, action
):
    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")
    denied = request(
        domain,
        owner,
        "POST",
        f"/tasks/{task['task_id']}/{action}",
        {"expected_revision": 1},
        actor=generator,
    )
    assert denied.status_code == 403
    claimed = domain.claim_task("fixture-worker", actor=owner)
    response = request(
        domain,
        owner,
        "POST",
        f"/tasks/{task['task_id']}/{action}",
        {"expected_revision": claimed["revision"]},
    )
    assert response.status_code == 409
    after = domain.read_task(task["task_id"], actor=owner)
    assert after["state"] == "running" and after["lease_token"] == claimed["lease_token"]


def test_control_owner_is_not_request_selected(domain, owner):
    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")
    stranger = Actor("other-owner", "other-user", "user", frozenset({"*"}))
    response = request(
        domain,
        owner,
        "POST",
        f"/tasks/{task['task_id']}/cancel",
        {"expected_revision": 1},
        actor=stranger,
    )
    assert response.status_code == 404


def test_source_and_bound_evidence_have_safe_http_projection(domain, owner, source):
    sid = source["source_id"]
    metadata = request(domain, owner, "GET", f"/sources/{sid}").json()["data"]
    assert (
        metadata["source_id"] == sid
        and "object_path" not in metadata
        and "descriptor" not in metadata
    )
    result = request(
        domain,
        owner,
        "POST",
        "/evidence/read",
        {
            "source_id": sid,
            "source_revision": source["revision"],
            "content_hash": source["content_hash"],
            "locator": "lines 1-2",
        },
    )
    assert result.status_code == 200
    value = result.json()["data"]
    assert value["text"] == "Evidence says addition preserves history."
    assert value["kind"] == "source" and value["locator"] == "lines 1-2"
    for private in ("object_path", "raw_path", "owner_id", "payload", "descriptor"):
        assert private not in value
    assert (
        request(
            domain, owner, "POST", "/evidence/read", {"source_id": sid, "source_revision": 999}
        ).status_code
        == 409
    )
    assert (
        request(
            domain, owner, "POST", "/evidence/read", {"source_id": sid, "content_hash": "wrong"}
        ).status_code
        == 409
    )
    assert (
        request(
            domain, owner, "POST", "/evidence/read", {"source_id": sid, "owner_id": "injected"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("action", ["resume", "cancel"])
def test_control_preserves_terminal_results(domain, owner, state, action):
    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")
    claimed = domain.claim_task("fixture-worker", actor=owner)
    terminal = domain.update_task(
        task["task_id"],
        {
            "state": state,
            "lease_token": claimed["lease_token"],
            "result": {"entry_id": "ent_existing"},
        },
        actor=owner,
        idempotency_key="finish",
    )
    result = request(
        domain,
        owner,
        "POST",
        f"/tasks/{task['task_id']}/{action}",
        {"expected_revision": terminal["revision"]},
    )
    assert result.status_code == 409
    assert domain.read_task(task["task_id"], actor=owner) == terminal


def test_control_does_not_reschedule_uncertain_effects(domain, owner):
    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")
    # Synthetic post-crash persisted state, not a user-selectable HTTP capability.
    with domain.store.transaction() as connection:
        task.update(
            state="blocked", reconciliation_required=True, checkpoint={"stage": "effect-recorded"}
        )
        domain.store.put(connection, "task", task["task_id"], task)
    for action in ("resume", "cancel"):
        result = request(
            domain,
            owner,
            "POST",
            f"/tasks/{task['task_id']}/{action}",
            {"expected_revision": 1},
            key=action,
        )
        assert result.json()["error"]["code"] == "RECONCILIATION_REQUIRED"
    assert domain.read_task(task["task_id"], actor=owner)["checkpoint"] == {
        "stage": "effect-recorded"
    }


def test_concurrent_cancel_exact_replay_mutates_once(domain, owner):
    from concurrent.futures import ThreadPoolExecutor

    task = domain.create_task("fixture.control", {}, actor=owner, idempotency_key="create")

    def cancel(_):
        return domain.control_task(
            task["task_id"], "cancel", 1, actor=owner, idempotency_key="one-cancel"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cancel, range(2)))
    assert results[0] == results[1]
    assert domain.read_task(task["task_id"], actor=owner)["revision"] == 2


def test_learning_evidence_returns_only_actual_user_words(domain, owner):
    outcome = domain.submit_learning_outcome(
        {
            "device_id": "fixture-device",
            "session_id": "fixture-session",
            "version": 1,
            "user_text": "Actual synthetic user words.",
            "result": {"model_output": "must not expose"},
        },
        actor=owner,
        idempotency_key="learning",
    )
    response = request(
        domain,
        owner,
        "POST",
        "/evidence/read",
        {"learning_outcome_id": outcome["outcome_id"], "content_hash": outcome["content_hash"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["kind"] == "learning" and data["text"] == "Actual synthetic user words."
    assert data["text_basis"] == "user_statement"
    assert "payload" not in data and "must not expose" not in response.text
