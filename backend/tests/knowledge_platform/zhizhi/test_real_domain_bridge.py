"""Real HTTP/SQLite/Git adapters; no live model or external-channel acceptance claim."""

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor

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
from ahadiff.knowledge_platform.core_routes import create_core_routes
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.learning.backend import DurableOutbox, RemoteKnowledgeBackend
from ahadiff.knowledge_platform.learning.desktop import (
    configure_desktop_backend,
    entry_id_for_concept,
    remote_draft_status,
    submit_remote_draft,
)
from ahadiff.knowledge_platform.learning.model_bridge import (
    ModelBridgeConfiguration,
    configure_model_bridge,
)
from ahadiff.knowledge_platform.radar.service import RadarService
from ahadiff.knowledge_platform.search.index import SearchService
from ahadiff.knowledge_platform.search.routes import create_read_routes
from ahadiff.serve import ServeState, create_app


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("AHADIFF_KNOWLEDGE_MODE", "unified")
    configure_model_bridge(
        None, ModelBridgeConfiguration(enabled=True, input_classification="personal")
    )
    domain = DomainService(tmp_path / "domain.sqlite")
    owner = Actor("zhizhi-owner", "person", "user", frozenset({"*"}))
    writer = Actor(owner.owner_id, "writer", "service", frozenset({"*"}))
    token = "isolated-bridge-fixture-token-at-least-24"
    app = Starlette(
        routes=create_core_routes(domain) + create_read_routes(domain),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware, authenticator=TokenAuthenticator({token: owner})
            )
        ],
    )

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://knowledge.test"
    )
    backend = RemoteKnowledgeBackend(
        "https://knowledge.test",
        token_provider=lambda: token,
        outbox=DurableOutbox(tmp_path / "delivery"),
        client=client,
    )
    configure_desktop_backend(backend, device_id="stable-device")
    yield domain, owner, writer, backend
    asyncio.run(client.aclose())
    configure_desktop_backend(None, device_id="stable-device")
    configure_model_bridge(None, ModelBridgeConfiguration())
    domain.close()


def facts(**extra):
    return {
        "device_id": "stable-device",
        "session_id": "card:1",
        "version": 1,
        "user_text": "事件溯源保留状态变化以便审计，但需要事件版本迁移。",
        "result": {},
        "metadata": {"classification": "personal"},
        **extra,
    }


def make_entry(domain, owner, writer, *, concept="concept", questions=None):
    outcome = domain.submit_learning_outcome(facts(), actor=owner, idempotency_key="evidence")
    change = domain.propose_change(
        {
            "entry_id": entry_id_for_concept(concept),
            "base_revision": 0,
            "title": "事件溯源架构",
            "body": "事件溯源保留状态变化以便审计。\n",
            "metadata": {"concept_id": concept, "open_questions": questions or []},
            "evidence_refs": [{"learning_outcome_id": outcome["outcome_id"]}],
        },
        actor=owner,
        idempotency_key="entry",
    )
    reviewer = Actor(
        owner.owner_id,
        "independent-reviewer",
        "agent",
        frozenset({"knowledge:read", "knowledge:review"}),
    )
    domain.record_review(
        change["change_id"],
        {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
        actor=reviewer,
        idempotency_key="entry-review",
    )
    return domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply")


def test_real_remote_outcome_replay_queues_once_without_claiming_knowledge(system):
    domain, owner, _, backend = system
    response = asyncio.run(backend.submit_learning_outcome(facts()))
    assert response["data"]["knowledge_state"] == "queued"
    assert asyncio.run(backend.submit_learning_outcome(facts())) == response
    assert domain.list_entries(actor=owner) == []
    task = domain.read_task(response["task_id"], actor=owner)
    assert task["kind"] == "knowledge.generate"
    assert len(domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])) == 1
    assert backend.outbox.records()[0]["state"] == "acknowledged"


def test_real_wiki_draft_uses_common_change_and_restores_pending_then_committed(system):
    domain, owner, writer, backend = system
    detail = {
        "card": {
            "card_id": "card",
            "card_version": 1,
            "concept_id": "concept",
            "title": "事件溯源",
        },
        "learning": {
            "note_text": "我理解审计与版本迁移的取舍。",
            "answers": {},
            "completed_at": "2026-10-19",
        },
    }
    draft = {
        "draft_id": "draft",
        "concept_id": "concept",
        "card_id": "card",
        "markdown": "事件溯源保留事件以便审计。\n",
        "original_hash": None,
        "status": "draft",
    }
    result = asyncio.run(submit_remote_draft(draft, detail, request_id="request"))
    assert result["published"] is False
    assert domain.list_entries(actor=owner) == []
    restored = asyncio.run(remote_draft_status(draft))
    assert restored["status"] == "submitted"
    repeated = asyncio.run(submit_remote_draft(draft, detail, request_id="browser-restart"))
    assert repeated["change_id"] == result["change_id"]
    change = domain.get_change(result["change_id"], actor=owner)
    assert change["evidence_refs"][0]["learning_outcome_id"]
    assert change["state"] == "needs_ai_review"
    reviewer = Actor(
        owner.owner_id,
        "independent-reviewer",
        "agent",
        frozenset({"knowledge:read", "knowledge:review"}),
    )
    domain.record_review(
        change["change_id"],
        {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
        actor=reviewer,
        idempotency_key="review",
    )
    domain.apply_change(change["change_id"], actor=writer, idempotency_key="write")
    assert asyncio.run(remote_draft_status(draft))["status"] == "published"
    assert len(asyncio.run(backend.list_entries())) == 1
    assert len([r for r in backend.outbox.records() if r["path"].endswith("/changes")]) == 1


def test_stale_desktop_original_keeps_draft_and_does_not_propose(system):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer)
    detail = {
        "card": {"card_id": "other", "card_version": 1, "concept_id": "concept", "title": "架构"},
        "learning": {"note_text": "自己的原话", "answers": {}},
    }
    draft = {
        "draft_id": "stale",
        "concept_id": "concept",
        "card_id": "other",
        "markdown": "保留草稿",
        "original_hash": hashlib.sha256(b"older").hexdigest(),
    }
    with pytest.raises(Exception, match="远端条目已变化"):
        asyncio.run(submit_remote_draft(draft, detail, request_id="stale"))
    assert draft["markdown"] == "保留草稿"
    assert domain.get_entry(entry["entry_id"], actor=owner)["revision"] == 1
    assert not any(r["path"].endswith("/changes") for r in backend.outbox.records())


def test_radar_accept_once_reject_stays_rejected_and_no_fake_mastery(system):
    domain, owner, writer, _ = system
    entry = make_entry(
        domain, owner, writer, questions=["怎样迁移旧事件版本？", "何时不适合事件溯源？"]
    )
    radar = RadarService(domain)
    suggestions = radar.run(actor=owner)
    assert len(suggestions) == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(
            pool.map(
                lambda _: radar.decide(suggestions[0]["suggestion_id"], "accept", actor=owner),
                range(2),
            )
        )
    assert accepted[0]["learning_id"] == accepted[1]["learning_id"]
    radar.decide(suggestions[1]["suggestion_id"], "reject", actor=owner)
    assert radar.run(actor=owner) == []
    with pytest.raises(DomainError):
        radar.decide(suggestions[1]["suggestion_id"], "accept", actor=owner)
    outcome = domain.submit_learning_outcome(
        facts(
            session_id="radar:" + suggestions[0]["suggestion_id"],
            entry_id=entry["entry_id"],
            metadata={
                "classification": "personal",
                "radar_suggestion_id": suggestions[0]["suggestion_id"],
            },
        ),
        actor=owner,
        idempotency_key="practice",
    )
    completed = radar.complete(suggestions[0]["suggestion_id"], outcome["outcome_id"], actor=owner)
    assert completed["relation_confirmed"] is False
    assert "mastery" not in completed
    assert domain.get_entry(entry["entry_id"], actor=owner)["revision"] == 1


def test_real_search_rebuild_scopes_and_owner_isolation(system):
    domain, owner, writer, _ = system
    entry = make_entry(domain, owner, writer)
    service = SearchService(domain)
    assert service.search("事件溯源", actor=owner)[0]["entry_id"] == entry["entry_id"]
    read_only = Actor(owner.owner_id, "reader", "agent", frozenset({"knowledge:read"}))
    with pytest.raises(DomainError):
        service.search("版本迁移", actor=read_only, scope="learning")
    assert service.search("版本迁移", actor=owner, scope="learning")
    other = Actor("other-owner", "other", "user", frozenset({"*"}))
    assert service.search("事件溯源", actor=other) == []


def test_existing_desktop_radar_accept_response_and_restart_are_real_http(system, tmp_path):
    domain, owner, writer, backend = system
    entry = make_entry(
        domain, owner, writer, questions=["何时应选择事件溯源，哪些迁移成本必须考虑？"]
    )
    app = create_app(ServeState(state_dir=tmp_path / "desktop", token="local-token"))
    client = TestClient(app, base_url="http://localhost:8765")
    headers = {"Origin": "http://localhost:8765", "X-AhaDiff-Token": "local-token"}
    prefix = "/api/growth/local/knowledge/radar"
    assert (
        client.post(
            prefix + "/runs", headers={"Origin": "http://localhost:8765"}, json={}
        ).status_code
        == 401
    )
    listed = client.post(prefix + "/runs", headers=headers, json={"limit": 3})
    assert listed.status_code == 200
    suggestion = listed.json()["suggestions"][0]
    sid = suggestion["suggestion_id"]
    accepted = client.post(
        prefix + f"/{sid}/decision", headers=headers, json={"decision": "accept"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["suggestion_state"] == "accepted"
    assert accepted.json()["practice"]["entry_id"] == entry["entry_id"]
    assert accepted.json()["user_text"] == ""
    before = domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])
    assert (
        client.post(
            prefix + f"/{sid}/complete", headers=headers, json={"user_text": " "}
        ).status_code
        == 422
    )
    assert domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"]) == before
    user_text = (
        "我会在确实需要审计与重放时采用事件溯源；必须规划事件版本迁移，不能只因为技术新就采用。"
    )
    submitted = client.post(
        prefix + f"/{sid}/complete", headers=headers, json={"user_text": user_text}
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] == "submitted" and submitted.json()["published"] is False
    outcome = domain.get_learning_outcome(submitted.json()["outcome_id"], actor=owner)["payload"]
    assert outcome["user_text"] == user_text
    assert outcome["metadata"]["radar_suggestion_id"] == sid
    assert outcome["entry_id"] == entry["entry_id"]
    assert outcome["result"] == {"practice_kind": "open_response", "graded": False}
    task = domain.read_task(submitted.json()["task_id"], actor=owner)
    assert task["kind"] == "knowledge.generate" and task["payload"]["entry_id"] == entry["entry_id"]
    assert (
        client.post(
            prefix + f"/{sid}/complete", headers=headers, json={"user_text": user_text}
        ).json()
        == submitted.json()
    )
    assert (
        client.post(
            prefix + f"/{sid}/complete", headers=headers, json={"user_text": "替换之前回答"}
        ).status_code
        == 409
    )
    configure_desktop_backend(
        RemoteKnowledgeBackend(
            backend.base_url,
            token_provider=backend.token_provider,
            outbox=DurableOutbox(backend.outbox.directory),
            client=backend.client,
        ),
        device_id="stable-device",
    )
    restored = client.get(prefix + f"/{sid}", headers=headers)
    assert restored.json()["suggestion_state"] == "completed"
    assert restored.json()["user_text"] == user_text
    assert restored.json()["state"] == "submitted"
    assert domain.read_task(accepted.json()["learning_id"], actor=owner)["state"] == "succeeded"
    assert domain.get_entry(entry["entry_id"], actor=owner)["revision"] == 1


def test_radar_rejects_forged_completion_before_outcome_or_generation(system):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer, questions=["何时需要事件溯源？"])
    suggestion = RadarService(domain).run(actor=owner)[0]
    sid = suggestion["suggestion_id"]
    submitted_before = domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])
    payload = facts(
        session_id="radar:" + sid,
        entry_id=entry["entry_id"],
        metadata={"classification": "public", "radar_suggestion_id": sid},
    )
    with pytest.raises(Exception, match="INVALID_STATE"):
        asyncio.run(backend.submit_learning_outcome(payload))
    assert (
        domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"]) == submitted_before
    )


def test_radar_two_devices_only_one_real_outcome_and_generation(system):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer, questions=["如何核对架构边界与迁移成本？"])
    radar = RadarService(domain)
    sid = radar.run(actor=owner)[0]["suggestion_id"]
    radar.decide(sid, "accept", actor=owner)
    before = len(domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"]))

    async def submit(device):
        return await backend.client.post(
            "/api/knowledge/v1/learning/outcomes",
            headers={
                "Authorization": "Bearer " + backend.token_provider(),
                "Idempotency-Key": "response-" + device,
            },
            json=facts(
                device_id=device,
                session_id="radar:" + sid,
                entry_id=entry["entry_id"],
                user_text="我的实际回答，来自设备" + device,
                metadata={"radar_suggestion_id": sid},
                result={"mastery": 100, "graded": True},
            ),
        )

    async def both():
        return await asyncio.gather(submit("one"), submit("two"))

    responses = asyncio.run(both())
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        len(domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])) == before + 1
    )
    result = next(response.json()["data"] for response in responses if response.status_code == 200)
    assert result["payload"]["result"] == {"practice_kind": "open_response", "graded": False}
    assert "personal" in result["payload"]["metadata"]["classification_labels"]
    assert radar.read(sid, actor=owner)["learning_outcome_id"] == result["outcome_id"]


def test_malformed_radar_submission_does_not_reserve_completion(system):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer, questions=["哪些边界不适合事件溯源？"])
    radar = RadarService(domain)
    sid = radar.run(actor=owner)[0]["suggestion_id"]
    radar.decide(sid, "accept", actor=owner)
    payload = facts(
        session_id="radar:" + sid, entry_id=entry["entry_id"], metadata={"radar_suggestion_id": sid}
    )

    async def submit(value, key="valid-request"):
        return await backend.client.post(
            "/api/knowledge/v1/learning/outcomes",
            headers={"Authorization": "Bearer " + backend.token_provider(), "Idempotency-Key": key},
            json=value,
        )

    for bad in (
        payload | {"device_id": ""},
        payload | {"user_text": {"forged": "text"}},
        payload | {"version": True},
        payload | {"version": 1.0},
        payload | {"result": []},
        payload | {"metadata": []},
    ):
        assert asyncio.run(submit(bad)).status_code == 422
    assert asyncio.run(submit(payload, "")).status_code == 422
    assert asyncio.run(submit(payload)).status_code == 200


def test_radar_lost_response_reopens_outbox_and_acknowledges_same_outcome(system, monkeypatch):
    from ahadiff.knowledge_platform.learning.radar_practice import read_practice, submit_practice

    domain, owner, writer, backend = system
    make_entry(domain, owner, writer, questions=["如何评估迁移代价？"])
    radar = RadarService(domain)
    sid = radar.run(actor=owner)[0]["suggestion_id"]
    radar.decide(sid, "accept", actor=owner)
    transport = backend.client._transport
    real_request = transport.handle_async_request
    lost = False

    async def lose_once(request):
        nonlocal lost
        response = await real_request(request)
        if request.url.path.endswith("learning/outcomes") and not lost:
            lost = True
            await response.aread()
            raise httpx.ReadError("response lost after remote commit", request=request)
        return response

    monkeypatch.setattr(transport, "handle_async_request", lose_once)
    text = "我会先验证迁移与回放成本，再决定是否采用。"
    pending = asyncio.run(submit_practice(sid, text))
    assert pending["state"] == "pending"
    outcome_id = radar.read(sid, actor=owner)["learning_outcome_id"]
    reopened = RemoteKnowledgeBackend(
        backend.base_url,
        token_provider=backend.token_provider,
        outbox=DurableOutbox(backend.outbox.directory),
        client=backend.client,
    )
    configure_desktop_backend(reopened, device_id="stable-device")
    restored = asyncio.run(read_practice(sid))
    assert restored["state"] == "pending" and restored["user_text"] == text
    asyncio.run(reopened.flush())
    restored = asyncio.run(read_practice(sid))
    assert restored["state"] == "submitted" and restored["outcome_id"] == outcome_id


def test_plan_preview_explicit_accept_and_status_use_existing_real_routes(system, tmp_path):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer)
    app = create_app(ServeState(state_dir=tmp_path / "desktop", token="local-token"))
    client = TestClient(app, base_url="http://localhost:8765")
    headers = {"Origin": "http://localhost:8765", "X-AhaDiff-Token": "local-token"}
    prefix = "/api/growth/local/knowledge/radar/plans"
    ref = {"entry_id": entry["entry_id"], "revision": entry["revision"],
           "expected_content_hash": entry["content_hash"], "focus": "architecture"}
    assert client.post(prefix + "/preview", headers={"Origin": "http://localhost:8765"}, json=ref).status_code == 401
    assert client.post(prefix + "/preview", headers={"X-AhaDiff-Token": "local-token"}, json=ref).status_code == 403
    assert client.post(prefix + "/preview", headers={**headers, "Origin": "https://untrusted.example"}, json=ref).status_code == 403
    before = domain.list_events(actor=owner)
    preview = client.post(prefix + "/preview", headers=headers, json=ref)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert domain.list_events(actor=owner) == before
    assert not backend.outbox.records()
    status_path = prefix + "/" + plan["plan_hash"]
    assert client.get(status_path, headers=headers).json()["delivery_state"] == "not_submitted"
    wrong = client.post(prefix + "/accept", headers=headers, json={**ref, "plan_hash": "0" * 64})
    assert wrong.status_code == 409
    accepted = client.post(prefix + "/accept", headers=headers, json={**ref, "plan_hash": plan["plan_hash"]})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["suggestion_state"] == "accepted"
    assert accepted.json()["learning_id"].startswith("tsk_")
    assert client.post(prefix + "/accept", headers=headers, json={**ref, "plan_hash": plan["plan_hash"]}).json() == accepted.json()
    status = client.get(status_path, headers=headers).json()
    assert status["delivery_state"] == "acknowledged"
    assert status["receipt"] == accepted.json()
    assert client.get(prefix + "/bad", headers=headers).status_code == 422
