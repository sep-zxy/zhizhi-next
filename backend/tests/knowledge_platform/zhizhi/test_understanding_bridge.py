"""Synthetic user facts over real ASGI/SQLite/Git; not a live model/Windows journey."""

from __future__ import annotations

import asyncio
import copy
import json
import secrets
from uuid import uuid4

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
from ahadiff.knowledge_platform.learning.backend import (
    BackendError,
    DurableOutbox,
    RemoteKnowledgeBackend,
)
from ahadiff.knowledge_platform.learning.desktop import configure_desktop_backend
from ahadiff.knowledge_platform.learning.model_bridge import (
    ModelBridgeConfiguration,
    configure_model_bridge,
)
from ahadiff.knowledge_platform.learning.understanding import (
    note_hash,
    read_submission,
    read_submissions,
    submit_understanding,
)
from ahadiff.knowledge_platform.model import DisabledModelRunner, FixtureModelRunner
from ahadiff.knowledge_platform.search.routes import create_read_routes
from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings
from ahadiff.serve import ServeState, create_app

TEXT = "  异步消息把接收与处理分离，重放时需要按业务标识去重。\n这是本人记录，不是模型答题。\n"


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("AHADIFF_KNOWLEDGE_MODE", "unified")
    configure_model_bridge(
        None, ModelBridgeConfiguration(enabled=True, input_classification="public")
    )
    domain = DomainService(tmp_path / "domain.sqlite")
    owner = Actor("synthetic-understanding-owner", "synthetic-person", "user", frozenset({"*"}))
    token = secrets.token_urlsafe(32)
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
        outbox=DurableOutbox(tmp_path / "outbox"),
        client=client,
    )
    configure_desktop_backend(backend, device_id="synthetic-device")
    detail = {
        "card": {
            "card_id": str(uuid4()),
            "concept_id": str(uuid4()),
            "card_version": 1,
            "title": "异步交付边界",
        },
        "learning": {"stage": "front", "answers": {}, "note_text": TEXT, "completed_at": None},
    }
    yield domain, owner, backend, detail
    asyncio.run(client.aclose())
    configure_desktop_backend(None, device_id="synthetic-device")
    configure_model_bridge(None, ModelBridgeConfiguration())
    domain.close()


def submit(detail):
    return submit_understanding(
        detail,
        card_version=detail["card"]["card_version"],
        note_sha256=note_hash(detail["learning"]["note_text"]),
    )


def test_uncompleted_exact_note_queues_one_private_outcome_without_mutating_learning(system):
    domain, owner, backend, detail = system
    original = copy.deepcopy(detail)
    result = asyncio.run(submit(detail))
    assert result["delivery_state"] == "acknowledged"
    assert result["task"]["state"] == "queued" and result["change"] is None
    assert result["user_text"] == TEXT and detail == original
    outcome = domain.get_learning_outcome(result["outcome_id"], actor=owner)["payload"]
    assert outcome["user_text"] == TEXT
    assert outcome["result"] == {"practice_kind": "open_response", "graded": False}
    assert outcome["metadata"]["evidence_classification"] == "personal"
    assert "radar_suggestion_id" not in outcome["metadata"]
    assert "completed_at" not in outcome["result"]
    assert domain.list_entries(actor=owner) == []
    assert "Authorization" not in json.dumps(backend.outbox.records())


def test_double_submit_and_restart_reuse_snapshot_then_new_text_has_new_version(system):
    domain, owner, backend, detail = system

    async def double():
        return await asyncio.gather(submit(detail), submit(detail))

    first, second = asyncio.run(double())
    assert first["submission_id"] == second["submission_id"]
    restored = RemoteKnowledgeBackend(
        backend.base_url,
        token_provider=backend.token_provider,
        outbox=DurableOutbox(backend.outbox.directory),
        client=backend.client,
    )
    configure_desktop_backend(restored, device_id="synthetic-device")
    assert asyncio.run(submit(detail))["outcome_id"] == first["outcome_id"]
    detail["learning"]["note_text"] += "后续补充：需要保存幂等键。"
    new = asyncio.run(submit(detail))
    assert new["submission_id"] != first["submission_id"]
    records = restored.outbox.records()
    assert sorted(item["payload"]["version"] for item in records) == [1, 2]
    assert len(domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])) == 2
    # Explicit old-snapshot replay remains immutable even after editing the note.
    replay = asyncio.run(submit_understanding(detail, card_version=1, note_sha256=note_hash(TEXT)))
    assert replay["user_text"] == TEXT and replay["outcome_id"] == first["outcome_id"]
    assert (
        asyncio.run(read_submissions(detail["card"]["card_id"]))["items"][0]["submission_id"]
        == new["submission_id"]
    )


@pytest.mark.parametrize("version,digest", [(2, note_hash(TEXT)), (1, note_hash(TEXT.strip()))])
def test_stale_version_or_exact_whitespace_hash_never_enqueues(system, version, digest):
    domain, owner, backend, detail = system
    with pytest.raises(BackendError) as failure:
        asyncio.run(submit_understanding(detail, card_version=version, note_sha256=digest))
    assert failure.value.code == "UNDERSTANDING_STALE"
    assert backend.outbox.records() == []
    assert domain.list_events(actor=owner) == []


@pytest.mark.parametrize("text", ["", " \n", "长" * 20001], ids=["empty", "whitespace", "oversized"])
def test_empty_or_unbounded_note_not_submitted(system, text):
    _, _, backend, detail = system
    detail["learning"]["note_text"] = text
    with pytest.raises(BackendError) as failure:
        asyncio.run(submit(detail))
    assert failure.value.code == "INVALID_INPUT" and backend.outbox.records() == []


def test_missing_configuration_never_falls_back_or_enqueues(system):
    _, _, backend, detail = system
    configure_desktop_backend(None, device_id="synthetic-device")
    with pytest.raises(BackendError) as failure:
        asyncio.run(submit(detail))
    assert failure.value.code == "WAITING_CONFIGURATION"
    assert backend.outbox.records() == []


def test_offline_pending_survives_get_without_post_and_replays_once(system):
    domain, owner, backend, detail = system
    real_client = backend.client
    requests = []

    def offline(request):
        requests.append(request.method)
        raise httpx.ReadTimeout("synthetic-untrusted-detail-do-not-render")

    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(offline))
    first = asyncio.run(submit(detail))
    assert first["delivery_state"] == "pending" and first["remote_state"] == "unknown"
    assert first["error"]["code"] == "NETWORK_UNAVAILABLE"
    assert "synthetic-untrusted-detail" not in json.dumps(first)
    assert asyncio.run(read_submission(detail["card"]["card_id"], first["submission_id"])) == first
    assert requests == ["POST"]
    asyncio.run(backend.client.aclose())
    backend.client = real_client
    second = asyncio.run(submit(detail))
    assert second["submission_id"] == first["submission_id"]
    assert len(domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])) == 1


def test_no_remote_auth_leaves_pending_without_network_or_public_leak(system):
    _, _, backend, detail = system
    backend.token_provider = lambda: ""
    requests = []
    real_client = backend.client
    backend.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: requests.append(request))
    )
    result = asyncio.run(submit(detail))
    assert requests == []
    assert result["delivery_state"] == "pending" and result["error"]["code"] == "WAITING_AUTH"
    asyncio.run(backend.client.aclose())
    backend.client = real_client


class EvidenceFixture:
    """Schema-validated fixture runner, never a live-provider assertion."""

    def __init__(self):
        self.calls = []

    async def run(self, task):
        self.calls.append(task)
        text = task.payload["evidence"][0]["text"]
        if task.schema_name == "knowledge_proposal_v1":
            output = {
                "no_change": False,
                "title": "异步交付边界",
                "body": text,
                "summary": "本人观察的技术边界",
                "risk_flags": [],
                "claims": [
                    {
                        "text": text,
                        "quote": text,
                        "evidence_index": 0,
                        "relation": "add",
                        "personal_expression": False,
                    }
                ],
            }
        else:
            output = {
                "decision": "approve",
                "reason": "合成原话支持此变更",
                "patch_hash": task.payload["patch_hash"],
                "risk_flags": [],
                "checks": [
                    {"evidence_index": index, "quote": evidence["text"], "supports_patch": True}
                    for index, evidence in enumerate(task.payload["evidence"])
                ],
            }
        return await FixtureModelRunner({task.schema_name: output}, development=True).run(task)


def test_real_workflow_review_writer_then_exact_base_diff_remain_private(system):
    domain, owner, backend, detail = system
    first = asyncio.run(submit(detail))
    runner = EvidenceFixture()
    workflow = KnowledgeWorkflow(
        domain,
        runner,
        WorkflowSettings(
            owner_id=owner.owner_id,
            allowed_provider_ids=("fixture",),
            allow_fixture=True,
            auto_commit_enabled=True,
        ),
    )
    assert asyncio.run(workflow.run_once())
    done = asyncio.run(read_submission(detail["card"]["card_id"], first["submission_id"]))
    assert done["change"]["state"] == "committed", done
    assert done["change"]["committed_revision"] == 1
    assert done["change"]["original_markdown"] == ""
    assert done["change"]["candidate_markdown"] == TEXT
    entry = domain.get_entry(done["change"]["entry_id"], actor=owner)
    assert entry["visibility"] == "private" and entry["publication_eligible"] is False
    assert [call.purpose for call in runner.calls] == ["generate", "review"]
    detail["learning"]["note_text"] += "消费者还需要限制重复副作用。"
    second = asyncio.run(submit(detail))
    assert asyncio.run(workflow.run_once())
    changed = asyncio.run(read_submission(detail["card"]["card_id"], second["submission_id"]))
    assert changed["change"]["base_revision"] == 1
    assert changed["change"]["original_markdown"] == TEXT
    assert changed["change"]["candidate_markdown"] == detail["learning"]["note_text"]
    assert changed["change"]["committed_revision"] == 2, (
        changed["change"]["state"],
        changed["task"]["state"],
        changed["task"]["result"],
        domain.get_change(changed["change"]["change_id"], actor=owner)["risk_flags"],
    )
    assert not {"payload", "lease_token", "owner_id"} & set(changed["task"])
    assert "published" not in changed and detail["learning"]["completed_at"] is None


def test_model_not_configured_is_blocked_not_fabricated_success(system):
    domain, owner, _, detail = system
    first = asyncio.run(submit(detail))
    workflow = KnowledgeWorkflow(
        domain, DisabledModelRunner(), WorkflowSettings(owner_id=owner.owner_id)
    )
    asyncio.run(workflow.run_once())
    view = asyncio.run(read_submission(detail["card"]["card_id"], first["submission_id"]))
    assert view["task"]["state"] == "blocked" and view["change"] is None
    assert domain.list_entries(actor=owner) == []


def test_other_card_cannot_read_submission(system):
    _, _, _, detail = system
    first = asyncio.run(submit(detail))
    with pytest.raises(BackendError) as failure:
        asyncio.run(read_submission(str(uuid4()), first["submission_id"]))
    assert failure.value.code == "NOT_FOUND"


def test_major_personal_position_waits_for_real_user_decision(system):
    domain, owner, _, detail = system
    detail["learning"]["note_text"] = "我的理解是先验证消息重放边界，再决定是否引入事件溯源。"
    first = asyncio.run(submit(detail))
    workflow = KnowledgeWorkflow(
        domain,
        EvidenceFixture(),
        WorkflowSettings(
            owner_id=owner.owner_id,
            allowed_provider_ids=("fixture",),
            allow_fixture=True,
            auto_commit_enabled=True,
        ),
    )
    asyncio.run(workflow.run_once())
    view = asyncio.run(read_submission(detail["card"]["card_id"], first["submission_id"]))
    assert view["task"]["state"] == "waiting_user"
    assert view["change"]["state"] == "needs_user"
    assert view["change"]["committed_revision"] is None
    assert domain.list_entries(actor=owner) == []


def test_no_change_is_not_a_new_formal_entry(system):
    domain, owner, _, detail = system
    first = asyncio.run(submit(detail))
    runner = FixtureModelRunner(
        {
            "knowledge_proposal_v1": {
                "no_change": True,
                "title": "无新增",
                "body": "",
                "claims": [],
                "risk_flags": [],
                "summary": "无新增证据",
            }
        },
        development=True,
    )
    asyncio.run(
        KnowledgeWorkflow(
            domain,
            runner,
            WorkflowSettings(owner_id=owner.owner_id, allow_fixture=True, auto_commit_enabled=True),
        ).run_once()
    )
    view = asyncio.run(read_submission(detail["card"]["card_id"], first["submission_id"]))
    assert view["task"]["state"] == "succeeded"
    assert view["task"]["result"]["status"] == "no_change" and view["change"] is None
    assert domain.list_entries(actor=owner) == []


def test_remote_conflict_is_retained_and_never_retried_by_get(system):
    _, _, backend, detail = system
    requests = []
    real_client = backend.client

    def conflict(request):
        requests.append(request.method)
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "IDEMPOTENCY_CONFLICT",
                    "detail": "synthetic-private-provider-detail",
                    "retryable": False,
                }
            },
        )

    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(conflict))
    first = asyncio.run(submit(detail))
    assert first["delivery_state"] == "rejected"
    assert first["error"] == {"code": "IDEMPOTENCY_CONFLICT", "retryable": False}
    assert "synthetic-private-provider-detail" not in json.dumps(first)
    assert asyncio.run(submit(detail)) == first
    assert asyncio.run(read_submissions(detail["card"]["card_id"]))["items"] == [first]
    assert requests == ["POST"]
    asyncio.run(backend.client.aclose())
    backend.client = real_client


def test_unavailable_exact_base_does_not_substitute_current_entry(system, monkeypatch):
    domain, owner, backend, detail = system
    runner = EvidenceFixture()
    workflow = KnowledgeWorkflow(
        domain,
        runner,
        WorkflowSettings(owner_id=owner.owner_id, allow_fixture=True, auto_commit_enabled=True),
    )
    asyncio.run(submit(detail))
    asyncio.run(workflow.run_once())
    detail["learning"]["note_text"] += "新增受控结论。"
    second = asyncio.run(submit(detail))
    asyncio.run(workflow.run_once())

    async def wrong_base(entry_id, revision):
        return {
            "data": {
                "entry_id": entry_id,
                "revision": revision + 1,
                "body": "绝不能冒充旧版的当前正文",
            }
        }

    monkeypatch.setattr(backend, "read_entry_revision", wrong_base)
    view = asyncio.run(read_submission(detail["card"]["card_id"], second["submission_id"]))
    assert view["change"] is None and view["remote_state"] == "unknown"
    assert view["error"]["code"] == "INVALID_RESPONSE"
    assert "绝不能冒充旧版" not in json.dumps(view, ensure_ascii=False)


def test_unknown_classification_cannot_send_user_words(system):
    _, _, backend, detail = system
    configure_model_bridge(None, ModelBridgeConfiguration())
    with pytest.raises(BackendError) as failure:
        asyncio.run(submit(detail))
    assert failure.value.code == "WAITING_CONFIGURATION" and backend.outbox.records() == []


def test_routes_require_auth_exact_payload_and_preserve_learning(system, tmp_path, monkeypatch):
    _, _, backend, detail = system
    from ahadiff.serve import routes_growth_knowledge as routes

    reads = []
    monkeypatch.setattr(
        routes.knowledge_learning,
        "card_detail",
        lambda ledger, card: reads.append(card) or copy.deepcopy(detail),
    )
    local_token = secrets.token_urlsafe(32)
    app = create_app(ServeState(state_dir=tmp_path / "desktop", token=local_token))
    client = TestClient(app, base_url="http://localhost:8765")
    headers = {"Origin": "http://localhost:8765", "X-AhaDiff-Token": local_token}
    path = (
        f"/api/growth/local/knowledge/cards/{detail['card']['card_id']}/understanding/submissions"
    )
    body = {"card_version": 1, "note_sha256": note_hash(TEXT)}
    assert client.post(path, json=body, headers={"Origin": headers["Origin"]}).status_code == 401
    assert reads == [] and backend.outbox.records() == []
    for extra in (
        {"owner_id": "other"},
        {"user_text": "fabricated"},
        {"completed_at": "now"},
        {"data_classification": "public"},
        {"card_version": True},
    ):
        assert client.post(path, json={**body, **extra}, headers=headers).status_code == 422
    assert backend.outbox.records() == []
    assert client.get(path, headers=headers).json() == {"items": [], "has_more": False}
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 202, response.text
    item = response.json()
    assert client.get(path + "/" + item["submission_id"], headers=headers).json() == item
    assert client.get(path + "?limit=0", headers=headers).status_code == 422
    assert client.get(path + "?unknown=1", headers=headers).status_code == 422
    assert detail["learning"]["stage"] == "front"
