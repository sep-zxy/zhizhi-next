"""Real HTTP/SQLite/Git adapters; no live model or external-channel acceptance claim."""

from __future__ import annotations

import asyncio
import hashlib
import json
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


def update_radar_entry(domain, owner, writer, entry):
    revision = entry["revision"]
    change = domain.propose_change(
        {
            "entry_id": entry["entry_id"],
            "base_revision": revision,
            "title": f"更新后的架构资料 {revision + 1}",
            "body": entry["body"] + f"这是第 {revision + 1} 版新增的资料。\n",
            "metadata": entry["metadata"],
            "evidence_refs": entry["evidence_refs"],
        },
        actor=owner,
        idempotency_key=f"update-radar-{revision}",
    )
    if change["state"] != "ready":
        challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
        domain.record_review(
            change["change_id"],
            {"decision": "approve", "expected_patch_hash": change["patch_hash"],
             "decision_nonce": challenge["decision_nonce"], "base_revision": revision},
            actor=owner,
            idempotency_key=f"review-radar-{revision}",
        )
    return domain.apply_change(
        change["change_id"], actor=writer, idempotency_key=f"apply-radar-{revision}"
    )


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
    second = update_radar_entry(domain, owner, writer, entry)
    assert client.post(prefix + "/runs", headers=headers, json={"limit": 3}).json() == listed.json()
    accepted = client.post(
        prefix + f"/{sid}/decision", headers=headers, json={"decision": "accept"}
    )
    assert accepted.status_code == 200
    assert accepted.json()["suggestion_state"] == "accepted"
    reference = {"entry_id": entry["entry_id"], "revision": 1,
                 "expected_content_hash": entry["content_hash"]}
    practice = accepted.json()["practice"]
    assert practice["entry_id"] == entry["entry_id"]
    assert practice["title"] == entry["title"]
    assert practice["body"] == entry["body"]
    assert practice["source_reference"] == reference
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
    assert outcome["metadata"]["source_reference"] == reference
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
    update_radar_entry(domain, owner, writer, second)
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
    assert restored.json()["practice"] == practice
    assert client.post(prefix + "/runs", headers=headers, json={"limit": 3}).json()["suggestions"] == []
    assert domain.read_task(accepted.json()["learning_id"], actor=owner)["state"] == "succeeded"
    assert domain.get_entry(entry["entry_id"], actor=owner)["revision"] == 3


def test_desktop_radar_snooze_reloads_and_submits_original_practice(system, tmp_path, monkeypatch):
    domain, owner, writer, backend = system
    questions = [f"待验证问题 {number}" for number in range(5)]
    entry = make_entry(domain, owner, writer, questions=questions)
    app = create_app(ServeState(state_dir=tmp_path / "desktop", token="local-token"))
    client = TestClient(app, base_url="http://localhost:8765")
    headers = {"Origin": "http://localhost:8765", "X-AhaDiff-Token": "local-token"}
    prefix = "/api/growth/local/knowledge/radar"
    assert client.get(prefix + "/snoozed").status_code == 401
    initial = client.get(prefix + "/snoozed", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json()["suggestions"] == []
    proposed = client.post(prefix + "/runs", headers=headers, json={"limit": 100}).json()[
        "suggestions"
    ]
    assert len(proposed) == 3
    sid = proposed[0]["suggestion_id"]
    reference = {
        "entry_id": entry["entry_id"],
        "revision": 1,
        "expected_content_hash": entry["content_hash"],
    }
    assert proposed[0]["source_reference"] == reference
    decision = prefix + f"/{sid}/decision"
    snoozed = client.post(decision, headers=headers, json={"decision": "snooze"})
    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["suggestion_state"] == "snoozed"
    rejected = client.post(
        prefix + f"/{proposed[1]['suggestion_id']}/decision",
        headers=headers, json={"decision": "reject"},
    )
    assert rejected.status_code == 200 and rejected.json()["suggestion_state"] == "rejected"
    second = update_radar_entry(domain, owner, writer, entry)
    assert second["revision"] == 2 and second["body"] != entry["body"]
    remaining = client.post(prefix + "/runs", headers=headers, json={}).json()["suggestions"]
    assert [item["proposed_exercise"] for item in remaining] == questions[2:]
    assert remaining[0]["suggestion_id"] == proposed[2]["suggestion_id"]
    assert remaining[0]["source_reference"] == reference

    configure_desktop_backend(
        RemoteKnowledgeBackend(
            backend.base_url,
            token_provider=backend.token_provider,
            outbox=DurableOutbox(backend.outbox.directory),
            client=backend.client,
        ),
        device_id="stable-device",
    )
    read_practice_source = domain.get_practice_source

    def without_original_revision(entry_id, *, actor, revision):
        if revision == 1:
            raise DomainError("NOT_FOUND", "Original source revision is unavailable", status_code=404)
        return read_practice_source(entry_id, actor=actor, revision=revision)

    with monkeypatch.context() as restricted:
        restricted.setattr(domain, "get_practice_source", without_original_revision)
        assert client.get(prefix + "/snoozed", headers=headers).json()["suggestions"] == []
        with pytest.raises(DomainError) as unavailable:
            RadarService(domain).decide(sid, "accept", actor=owner)
        assert unavailable.value.code == "SUGGESTION_UNAVAILABLE"
        assert not any(task["kind"] == "learning" for task in domain.list_tasks(actor=owner)["items"])

    saved = client.get(prefix + "/snoozed", headers=headers).json()["suggestions"]
    assert [item["suggestion_id"] for item in saved] == [sid]
    assert saved[0]["proposed_exercise"] == questions[0]
    accepted = client.post(decision, headers=headers, json={"decision": "accept"})
    assert accepted.status_code == 200, accepted.text
    practice = accepted.json()["practice"]
    assert practice["entry_id"] == entry["entry_id"]
    assert practice["question"] == questions[0]
    assert practice["source_status"] == "available"
    assert practice["source_reference"] == reference
    assert practice["title"] == entry["title"]
    assert practice["body"] == entry["body"]
    assert client.post(decision, headers=headers, json={"decision": "accept"}).json() == accepted.json()
    assert client.get(prefix + "/snoozed", headers=headers).json()["suggestions"] == []
    user_text = "我会用一条具体的重试记录核对行为。"
    submitted = client.post(prefix + f"/{sid}/complete", headers=headers, json={"user_text": user_text})
    assert submitted.status_code == 200, submitted.text
    outcome = domain.get_learning_outcome(submitted.json()["outcome_id"], actor=owner)
    assert outcome["payload"]["user_text"] == user_text
    assert outcome["payload"]["metadata"]["source_reference"] == reference
    restored = client.get(prefix + f"/{sid}", headers=headers).json()
    assert restored["suggestion_state"] == "completed"
    assert restored["user_text"] == user_text
    assert restored["practice"] == practice
    tasks = domain.list_tasks(actor=owner)["items"]
    assert sorted(
        task["payload"]["proposed_exercise"] for task in tasks if task["kind"] == "radar_suggestion"
    ) == questions
    assert [task["task_id"] for task in tasks if task["kind"] == "learning"] == [
        accepted.json()["learning_id"]
    ]


def test_radar_legacy_snooze_replays_and_resumes_once(system):
    domain, owner, writer, backend = system
    question = "共有的合成问题"
    entry = make_entry(domain, owner, writer, questions=[question])
    radar = RadarService(domain)
    identity = hashlib.sha256(json.dumps(
        {"entry_id": entry["entry_id"], "question": question, "evidence_refs": entry["evidence_refs"]},
        ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()
    sid = domain.create_task(
        "radar_suggestion",
        {"target_entry_ids": [entry["entry_id"]], "proposed_exercise": question,
         "evidence_refs": entry["evidence_refs"], "reason": "旧建议", "classification": "personal"},
        actor=owner, idempotency_key="radar-candidate:" + identity,
    )["task_id"]
    domain.create_task(
        "radar_decision", {"suggestion_id": sid, "decision": "snooze"},
        actor=owner, idempotency_key="radar-decision-claim:" + sid,
    )
    old_state = {
        "suggestion_id": sid, "suggestion_state": "snoozed",
        "classification": "personal", "classification_labels": ["personal"],
    }
    domain.set_channel_state(
        "radar:" + sid, old_state, actor=owner, idempotency_key=f"radar-decision:{sid}:snooze"
    )
    old_key = "radar-decision:" + sid
    backend.outbox.enqueue(old_key, f"/api/knowledge/v1/radar/{sid}/decision", {"decision": "snooze"})
    asyncio.run(backend.decide_radar(sid, "snooze"))
    assert backend.outbox.get(old_key)["state"] == "acknowledged"
    assert radar.run(actor=owner) == []
    assert [item["suggestion_id"] for item in radar.list_snoozed(actor=owner)["suggestions"]] == [sid]
    other = Actor("another-owner", "person", "user", frozenset({"*"}))
    assert radar.list_snoozed(actor=other)["suggestions"] == []
    with pytest.raises(DomainError, match="not found"):
        radar.decide(sid, "accept", actor=other)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(lambda _: radar.decide(sid, "accept", actor=owner), range(2)))
    delivered = asyncio.run(backend.decide_radar(sid, "accept"))["data"]
    assert delivered["learning_id"] == accepted[0]["learning_id"] == accepted[1]["learning_id"]
    practice = radar.read(sid, actor=owner)["practice"]
    assert practice["source_status"] == "unknown"
    assert practice["question"] == question and practice["body"] == ""
    assert "source_reference" not in practice
    assert backend.outbox.get(old_key)["payload"] == {"decision": "snooze"}
    with pytest.raises(DomainError) as late_snooze:
        radar.decide(sid, "snooze", actor=owner)
    assert late_snooze.value.code == "INVALID_STATE"
    tasks = domain.list_tasks(actor=owner)["items"]
    assert [task["task_id"] for task in tasks if task["kind"] == "learning"] == [
        delivered["learning_id"]
    ]


def test_radar_snoozed_pages_keep_older_items_after_an_accept(system, monkeypatch):
    domain, owner, writer, _ = system
    questions = [f"合成分页问题 {number}" for number in range(21)]
    entry = make_entry(domain, owner, writer, questions=questions)
    radar = RadarService(domain)
    ids = []
    for _ in range(7):
        for item in radar.run(actor=owner):
            sid = item["suggestion_id"]
            radar.decide(sid, "snooze", actor=owner)
            ids.append(sid)
    first = radar.list_snoozed(actor=owner)
    assert [item["suggestion_id"] for item in first["suggestions"]] == ids[:0:-1]
    assert first["next_cursor"] == ids[1]
    radar.decide(ids[1], "accept", actor=owner)
    older = radar.list_snoozed(actor=owner, before=first["next_cursor"])
    assert [item["suggestion_id"] for item in older["suggestions"]] == ids[:1]
    assert older["next_cursor"] is None

    closed = {"text": questions[0], "state": "resolved"}
    current = {**entry, "metadata": {**entry["metadata"], "open_question_records": [closed]}}
    monkeypatch.setattr(domain, "get_practice_source", lambda *_args, **_kwargs: current)
    assert radar.list_snoozed(actor=owner, before=first["next_cursor"])["suggestions"] == []
    with pytest.raises(DomainError) as unavailable:
        radar.decide(ids[0], "accept", actor=owner)
    assert unavailable.value.code == "SUGGESTION_UNAVAILABLE"


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
    wrong_source = {"entry_id": entry["entry_id"], "revision": 2,
                    "expected_content_hash": entry["content_hash"]}
    conflict = asyncio.run(submit(payload | {"metadata": {
        **payload["metadata"], "source_reference": wrong_source,
    }}))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONTENT_CONFLICT"
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
