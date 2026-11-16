"""Synthetic mocked transport, real isolated Domain/Git: NOT a live model result."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ahadiff.knowledge_platform.acceptance_model import (
    MODEL,
    SOURCE_TEXT,
    USER_TEXT,
    VERSION,
    WORKER,
    AcceptanceDomain,
    AcceptanceModelRunner,
    ScopeReceipts,
)
from ahadiff.knowledge_platform.ai_review.service import AIReviewService
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.generation.service import GenerationService
from ahadiff.knowledge_platform.intake.service import IntakeService
from ahadiff.knowledge_platform.model import CodexModelRunner, CodexRunnerConfig, SubscriptionRoute
from ahadiff.knowledge_platform.model.contracts import ModelResult, canonical_hash
from ahadiff.knowledge_platform.owner_identity import ACTOR, OWNER

USER = Actor(
    OWNER, ACTOR, "user", frozenset({"knowledge:read", "knowledge:write", "knowledge:review"})
)
GEN = Actor(OWNER, WORKER + ":generator", "agent", frozenset({"knowledge:read", "knowledge:write"}))
REVIEW = Actor(
    OWNER, WORKER + ":reviewer", "agent", frozenset({"knowledge:read", "knowledge:review"})
)
WRITER = Actor(
    OWNER, WORKER + ":writer", "service", frozenset({"knowledge:read", "knowledge:apply"})
)


@pytest.fixture
def scoped(tmp_path):
    receipts = ScopeReceipts(
        tmp_path, tmp_path / "receipts", {"synthetic": True}, "INERT-SYNTHETIC-TEST"
    )
    domain = AcceptanceDomain(tmp_path / "state", receipts, lambda facts: True)
    config = CodexRunnerConfig(
        enabled=True,
        reuse_existing_login=True,
        expected_version=VERSION,
        routes=(
            SubscriptionRoute(
                provider_id="openai",
                model_id=MODEL,
                account_qualified=True,
                allowed_classifications=("personal",),
            ),
        ),
    )
    transport = CodexModelRunner(config)
    calls = []

    async def synthetic(task):
        calls.append(task)
        if task.purpose == "generate":
            text = task.payload["evidence"][0]["text"]
            output = {
                "no_change": False,
                "title": "合成理解" if text == USER_TEXT else "合成整理",
                "body": text,
                "claims": [
                    {
                        "text": text,
                        "quote": text,
                        "evidence_index": 0,
                        "relation": "add",
                        "personal_expression": text == USER_TEXT,
                    }
                ],
                "risk_flags": [],
                "summary": "合成测试摘要",
            }
        elif task.purpose == "review":
            output = {
                "decision": "approve",
                "reason": "合成测试复核",
                "patch_hash": task.payload["patch_hash"],
                "risk_flags": [],
                "checks": [
                    {
                        "evidence_index": 0,
                        "quote": task.payload["evidence"][0]["text"],
                        "supports_patch": True,
                    }
                ],
            }
        else:
            output = {"summary": "合成测试摘要"}
        # Metadata deliberately simulates a real transport ONLY for offline boundary tests.
        # No fixture is used as operational evidence or written to a real acceptance root.
        return ModelResult(
            task_id=task.task_id,
            status="succeeded",
            output=output,
            provider_id="openai",
            requested_model_id=MODEL,
            runtime_version="codex-cli " + VERSION,
            input_fingerprint=task.fingerprint,
            result_hash=canonical_hash(output),
        )

    transport.run = AsyncMock(side_effect=synthetic)
    runner = AcceptanceModelRunner(domain, transport)
    yield domain, runner, transport, calls
    domain.close()


def retained(scoped):
    domain, _, _, _ = scoped
    intake = domain.create_intake({"text": SOURCE_TEXT}, actor=USER, idempotency_key="source")
    domain.update_intake(
        intake["intake_id"],
        {"state": "extracted", "preview": {"synthetic": True}, "preview_revision": 1},
        actor=USER,
        idempotency_key="preview",
    )
    raw = domain.cache_path / "synthetic.txt"
    raw.write_text(SOURCE_TEXT, encoding="utf-8")
    return domain.retain_source(
        intake["intake_id"],
        {
            "preview_revision": 1,
            "raw_path": str(raw),
            "metadata": {"publication_intent": "private", "extraction": {"body_text": SOURCE_TEXT}},
        },
        actor=USER,
        idempotency_key="keep",
    )


def generated(scoped, source):
    domain, runner, _, _ = scoped
    return asyncio.run(
        GenerationService(domain, runner, allowed_provider_ids=("openai",)).generate(
            [{"source_id": source["source_id"]}],
            task_id="synthetic-source-generate",
            actor=GEN,
            idempotency_key="generate",
        )
    )


def approved(scoped, change_id):
    domain, runner, _, _ = scoped
    return asyncio.run(
        AIReviewService(domain, runner, allowed_provider_ids=("openai",)).review(
            change_id, task_id="synthetic-source-review", actor=REVIEW, idempotency_key="review"
        )
    )


def test_real_domain_generator_reviewer_writer_sequence_with_mocked_transport(scoped):
    domain, _, _, calls = scoped
    source = retained(scoped)
    proposal = generated(scoped, source)
    assert proposal["status"] == "proposed", proposal
    change = proposal["change"]
    assert change["visibility"] == "private" and change["publication_intent"] == "private"
    assert change["publication_eligible"] is False
    with pytest.raises(DomainError):
        domain.apply_change(change["change_id"], actor=WRITER, idempotency_key="too-early")
    assert approved(scoped, change["change_id"])["status"] == "approved"
    result = domain.apply_change(change["change_id"], actor=WRITER, idempotency_key="apply")
    entry = domain.get_entry(result["entry_id"], actor=USER)
    assert entry["body"] == SOURCE_TEXT and entry["commit_sha"]
    assert [task.purpose for task in calls] == ["generate", "review"]
    assert domain.apply_change(change["change_id"], actor=WRITER, idempotency_key="apply") == result
    with pytest.raises(DomainError):
        domain.verify_publication_entry(entry, actor=USER)


def test_normal_user_cannot_skip_real_independent_model_review(scoped):
    domain, _, _, _ = scoped
    change = generated(scoped, retained(scoped))["change"]
    assert change["state"] == "needs_ai_review"
    challenge = domain.create_decision_challenge(change["change_id"], actor=USER)
    with pytest.raises(DomainError) as error:
        domain.record_review(
            change["change_id"],
            {
                "decision": "approve",
                "reason": "synthetic",
                "expected_patch_hash": change["patch_hash"],
                "base_revision": change["base_revision"],
                "decision_nonce": challenge["decision_nonce"],
            },
            actor=USER,
            idempotency_key="skip-ai",
        )
    assert error.value.code == "ACCEPTANCE_SCOPE_BLOCKED"


def test_proposal_cannot_forge_metadata_or_publication(scoped):
    domain, _, _, _ = scoped
    generated(scoped, retained(scoped))
    proofs = [
        json.loads(p.read_text())["payload"] for p in domain.scope_receipts.directory.iterdir()
    ]
    payload = next(p["value"]["payload"] for p in proofs if p["key"].startswith("proposal:"))
    for changed in ({**payload, "body": "injected"}, {**payload, "visibility": "public"}):
        with pytest.raises(DomainError):
            domain.propose_change(changed, actor=GEN, idempotency_key="forged")
    with pytest.raises(DomainError):
        domain.propose_change(payload, actor=USER, idempotency_key="wrong-principal")


def test_input_task_reconstruction_blocks_prompt_schema_and_payload_mutation(scoped):
    _, runner, transport, calls = scoped
    generated(scoped, retained(scoped))
    task = calls[0]
    before = transport.run.await_count
    for changed in (
        replace(task, instructions="secret injected"),
        replace(task, payload={**task.payload, "extra": "private"}),
        replace(task, output_schema={"type": "string"}),
        replace(task, purpose="answer"),
        replace(task, allowed_provider_ids=("other",)),
    ):
        assert asyncio.run(runner.run(changed)).status == "blocked"
    assert transport.run.await_count == before
    replay = asyncio.run(runner.run(task))
    assert replay.status == "succeeded" and transport.run.await_count == before


def test_failed_or_uncertain_stage_cannot_automatically_retry(scoped):
    domain, runner, transport, _ = scoped
    transport.run.side_effect = lambda task: ModelResult(task_id=task.task_id, status="failed")
    source = retained(scoped)
    assert generated(scoped, source)["status"] == "failed"
    assert generated(scoped, source)["status"] == "blocked"
    assert transport.run.await_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("fixture", True),
        ("requested_model_id", "fallback"),
        ("runtime_version", "codex-cli unreviewed"),
        ("input_fingerprint", "forged"),
        ("result_hash", "forged"),
    ],
)
def test_result_must_have_exact_transport_provenance(scoped, field, value):
    _, _, transport, _ = scoped
    original = transport.run.side_effect

    async def invalid(task):
        return replace(await original(task), **{field: value})

    transport.run.side_effect = invalid
    assert generated(scoped, retained(scoped))["status"] == "blocked"


def test_fixed_intake_and_closed_routes_reject_arbitrary_data(scoped):
    domain, _, _, _ = scoped
    for payload in (
        {"text": SOURCE_TEXT + " private"},
        {"text": SOURCE_TEXT, "url": "https://example.invalid"},
        {"text": SOURCE_TEXT, "channel": "wechat"},
        {"text": SOURCE_TEXT, "metadata": {"extra": "secret"}},
    ):
        with pytest.raises(DomainError):
            domain.create_intake(payload, actor=USER, idempotency_key="unapproved")
    with pytest.raises(DomainError):
        domain.supplement_intake("in_any", {"text": SOURCE_TEXT}, actor=USER)
    with pytest.raises(DomainError):
        domain.submit_learning_outcome(
            {"user_text": "other"}, actor=USER, idempotency_key="outcome"
        )
    domain.outcome_verifier = lambda facts: False
    with pytest.raises(DomainError):
        domain.submit_learning_outcome(
            {"user_text": USER_TEXT}, actor=USER, idempotency_key="unbound-fixed-note"
        )
    assert domain.scope_receipts.read("single-explanation") is None


def test_fixed_explanation_is_private_derived_chain_and_major_review_stays_human(scoped):
    domain, runner, _, calls = scoped
    first = generated(scoped, retained(scoped))["change"]
    assert approved(scoped, first["change_id"])["status"] == "approved"
    entry = domain.apply_change(first["change_id"], actor=WRITER, idempotency_key="source-apply")
    entry = domain.get_entry(entry["entry_id"], actor=USER)
    context = str(uuid4())
    target = str(uuid4())
    facts = {
        "device_id": "SYNTHETIC-DEVICE",
        "session_id": f"understanding:wiki:{context}:2",
        "version": 1,
        "entry_id": None,
        "user_text": USER_TEXT,
        "result": {"practice_kind": "open_response", "graded": False},
        "metadata": {
            "submission_kind": "wiki_entry_understanding",
            "context_id": context,
            "context_version": 2,
            "authority_key": "a" * 64,
            "source_kind": "wiki_entry",
            "source_entry_id": entry["entry_id"],
            "source_revision": entry["revision"],
            "source_content_hash": entry["content_hash"],
            "note_sha256": "b" * 64,
            "suggested_entry_id": target,
            "title": "本人理解：" + entry["title"],
            "evidence_classification": "personal",
            "classification_labels": ["personal"],
        },
    }
    outcome = domain.submit_learning_outcome(facts, actor=USER, idempotency_key="explanation")
    assert "publication_intent" not in facts["metadata"]
    assert outcome["payload"]["metadata"]["publication_intent"] == "private"
    result = asyncio.run(
        GenerationService(domain, runner, allowed_provider_ids=("openai",)).generate(
            [{"learning_outcome_id": outcome["outcome_id"]}],
            task_id="synthetic-note-generate",
            actor=GEN,
            idempotency_key="note-generate",
        )
    )
    assert result["status"] == "proposed", result
    change = result["change"]
    assert change["entry_id"] == target and target != entry["entry_id"]
    assert change["publication_intent"] == "private" and change["state"] == "needs_user"
    before = len(calls)
    response = asyncio.run(
        AIReviewService(domain, runner, allowed_provider_ids=("openai",)).review(
            change["change_id"],
            task_id="synthetic-note-review",
            actor=REVIEW,
            idempotency_key="note-review",
        )
    )
    assert response["status"] == "needs_user" and len(calls) == before
    challenge = domain.create_decision_challenge(change["change_id"], actor=USER)
    review = {
        "decision": "approve",
        "reason": "合成离线模拟用户明确确认",
        "expected_patch_hash": change["patch_hash"],
        "base_revision": change["base_revision"],
        "decision_nonce": challenge["decision_nonce"],
    }
    domain.record_review(change["change_id"], review, actor=USER, idempotency_key="human-approve")
    domain.record_review(change["change_id"], review, actor=USER, idempotency_key="human-approve")
    written = domain.apply_change(change["change_id"], actor=WRITER, idempotency_key="note-apply")
    assert written["entry_id"] == target
    assert domain.get_entry(entry["entry_id"], actor=USER)["content_hash"] == entry["content_hash"]
    assert [task.purpose for task in calls] == ["generate", "review", "generate"]
    assert calls[-1].payload["existing_catalogue"] == []


def test_real_intake_preview_contract_uses_only_fixed_text(scoped):
    domain, runner, transport, _ = scoped
    intake = IntakeService(domain, runner, allowed_provider_ids=("openai",))
    receipt = intake.receive({"text": SOURCE_TEXT}, actor=USER, idempotency_key="receive")
    from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings

    workflow = KnowledgeWorkflow(
        domain,
        runner,
        WorkflowSettings(
            owner_id=OWNER,
            worker_id=WORKER,
            allowed_provider_ids=("openai",),
            auto_commit_enabled=True,
        ),
        intake_service=intake,
    )
    assert asyncio.run(workflow.run_once()) is True
    record = domain.read_intake(receipt["intake_id"], actor=USER)
    assert record["preview"]["summary_state"] == "ready", record["preview"]
    assert transport.run.await_count == 1
    assert record["metadata"]["publication_intent"] == "private"
    source = intake.keep(
        record["intake_id"],
        preview_revision=record["preview_revision"],
        actor=USER,
        idempotency_key="keep-real-service",
    )
    assert (
        domain.read_evidence({"source_id": source["source_id"]}, actor=USER)["text"] == SOURCE_TEXT
    )
    assert asyncio.run(workflow.run_once()) is True
    assert asyncio.run(workflow.run_once()) is False
    entries = domain.list_entries(actor=USER)
    assert len(entries) == 1 and entries[0]["commit_sha"]
    assert transport.run.await_count == 3  # Actual services: preview, generate, independent review.


def test_list_entries_checks_exact_returned_seal_without_duplicate_git(scoped, monkeypatch):
    """Real synthetic Domain/Git, no model; one Git read per returned entry."""
    domain, _, _, _ = scoped
    proposal = generated(scoped, retained(scoped))['change']
    assert approved(scoped, proposal['change_id'])['status'] == 'approved'
    domain.apply_change(proposal['change_id'], actor=WRITER, idempotency_key='list-read-once')
    original = domain.writer.read_entry
    calls = []
    def read(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(domain.writer, 'read_entry', read)
    entries = domain.list_entries(actor=USER)
    assert len(entries) == len(calls) == 1
    original_receipt = domain.scope_receipts.read
    def tampered(key):
        value = original_receipt(key)
        return {**value, 'digest': '0' * 64} if key.startswith('entry:') else value
    monkeypatch.setattr(domain.scope_receipts, 'read', tampered)
    with pytest.raises(DomainError):
        domain.list_entries(actor=USER)
