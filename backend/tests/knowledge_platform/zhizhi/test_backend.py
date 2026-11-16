"""Development contract tests: HTTP transport is a fixture, not external E2E."""

import asyncio

import httpx
import pytest

from ahadiff.knowledge_platform.learning.backend import (
    BackendError,
    DurableOutbox,
    RemoteKnowledgeBackend,
)


def outcome(**changes):
    return {
        "device_id": "device-a",
        "session_id": "card-a:1",
        "version": 1,
        "user_text": "我还没有验证这个结论。",
        "metadata": {"concept_id": "concept-a"},
        **changes,
    }


def test_replay_after_timeout_uses_same_request_key(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("fixture interrupted after server accepted")
        return httpx.Response(
            200, json={"data": {"outcome_id": "one"}, "task_id": "task-one", "trace_id": "trace"}
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = RemoteKnowledgeBackend(
                "https://knowledge.example",
                token_provider=lambda: "fixture-not-a-real-credential",
                outbox=DurableOutbox(tmp_path),
                client=client,
            )
            with pytest.raises(BackendError, match="NETWORK_UNAVAILABLE"):
                await backend.submit_learning_outcome(outcome())
            assert backend.outbox.records()[0]["state"] == "pending"
            restored = RemoteKnowledgeBackend(
                "https://knowledge.example",
                token_provider=lambda: "fixture-not-a-real-credential",
                outbox=DurableOutbox(tmp_path),
                client=client,
            )
            results = await restored.flush()
            assert results[0]["data"]["outcome_id"] == "one"
            assert restored.outbox.records()[0]["state"] == "acknowledged"
            await restored.submit_learning_outcome(outcome())

    asyncio.run(scenario())
    assert len(requests) == 2
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
    assert requests[0].content == requests[1].content
    assert "fixture-not-a-real-credential" not in "".join(
        path.read_text() for path in tmp_path.glob("*.json")
    )


def test_same_revision_changed_text_conflicts(tmp_path):
    outbox = DurableOutbox(tmp_path)
    outbox.enqueue("same", "/learning", outcome())
    with pytest.raises(BackendError, match="IDEMPOTENCY_CONFLICT"):
        outbox.enqueue("same", "/learning", outcome(user_text="changed"))
    assert outbox.records()[0]["payload"]["user_text"] == outcome()["user_text"]


def test_facts_revision_survives_restart(tmp_path):
    outbox = DurableOutbox(tmp_path)
    first = outbox.enqueue_facts(outcome())
    second = DurableOutbox(tmp_path).enqueue_facts(outcome())
    third = DurableOutbox(tmp_path).enqueue_facts(outcome(user_text="这是新的个人原话"))
    assert first["idempotency_key"] == second["idempotency_key"]
    assert third["payload"]["version"] == 2


def test_conflict_retains_both_local_request_and_remote_reason(tmp_path):
    async def scenario():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                409,
                json={
                    "error": {
                        "code": "BASE_STALE",
                        "detail": "remote revision changed",
                        "retryable": False,
                    }
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            backend = RemoteKnowledgeBackend(
                "https://knowledge.example",
                token_provider=lambda: "fixture",
                outbox=DurableOutbox(tmp_path),
                client=client,
            )
            with pytest.raises(BackendError, match="BASE_STALE"):
                await backend.propose_wiki_change(
                    {"body": "local work", "base_revision": 2}, idempotency_key="draft-one"
                )
            record = backend.outbox.records()[0]
            assert record["state"] == "rejected"
            assert record["payload"]["body"] == "local work"
            assert record["error"]["code"] == "BASE_STALE"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "base_url",
    ["http://remote.example", "https://user:pass@example.com", "https://example.com?token=x"],
)
def test_unsafe_service_addresses_rejected(tmp_path, base_url):
    with pytest.raises(ValueError):
        RemoteKnowledgeBackend(
            base_url, token_provider=lambda: "fixture", outbox=DurableOutbox(tmp_path)
        )


def test_auth_failure_remains_retryable_even_when_domain_error_is_not(tmp_path):
    async def scenario():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                401,
                json={
                    "error": {"code": "UNAUTHENTICATED", "detail": "expired", "retryable": False}
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            backend = RemoteKnowledgeBackend(
                "https://knowledge.example",
                token_provider=lambda: "fixture",
                outbox=DurableOutbox(tmp_path),
                client=client,
            )
            with pytest.raises(BackendError):
                await backend.submit_learning_outcome(outcome())
            assert backend.outbox.records()[0]["state"] == "pending"

    asyncio.run(scenario())


def test_plan_accept_timeout_restarts_with_one_durable_identity(tmp_path):
    payload = {"entry_id": "fixture-entry", "revision": 1, "expected_content_hash": "b" * 64,
               "focus": "architecture", "plan_hash": "a" * 64}
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("synthetic lost acceptance receipt")
        return httpx.Response(200, json={"data": {"suggestion_state": "accepted", "learning_id": "one"}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            def restored():
                return RemoteKnowledgeBackend("https://knowledge.example", token_provider=lambda: "fixture",
                                              outbox=DurableOutbox(tmp_path), client=client)
            with pytest.raises(BackendError):
                await restored().accept_learning_plan(payload)
            assert DurableOutbox(tmp_path).get("learning-plan:" + "a" * 64)["state"] == "pending"
            response = await restored().accept_learning_plan(payload)
            assert await restored().accept_learning_plan(payload) == response
            assert DurableOutbox(tmp_path).get("learning-plan:" + "a" * 64)["state"] == "acknowledged"
            with pytest.raises(BackendError):
                await restored().accept_learning_plan({**payload, "revision": 2})
    asyncio.run(scenario())
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
