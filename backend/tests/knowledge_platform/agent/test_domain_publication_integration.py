"""Real ledger/Git derivative pipeline with synthetic source and explicit fixture models."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from ahadiff.knowledge_platform.ai_review import AIReviewService
from ahadiff.knowledge_platform.ai_review.service import PUBLICATION_CHECKS
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.generation import GenerationService
from ahadiff.knowledge_platform.model import FixtureModelRunner

SOURCE_TEXT = (
    "A producer puts work on a queue while a consumer processes it later. "
    "If the consumer fails, retained work can be retried without requiring the producer "
    "to repeat the original request. Consumer processing must handle repeated delivery."
)
SUMMARY_BODY = (
    "异步交付把请求接收和后台处理拆成两个阶段，消费者的短暂故障因此不必阻塞生产者。"
    "队列承担待处理任务的保存责任，而重复投递要求消费端按业务标识实现幂等。"
)


def build_reviewed_derivative(
    domain: DomainService, *, owner: Actor, generator: Actor, reviewer: Actor, writer: Actor
) -> dict[str, Any]:
    """Reusable genuine service pipeline fixture; no direct certificate minting or real account."""
    intake = domain.create_intake(
        {
            "text": "PRIVATE CHAT NOTE: learn later",
            "data_classification": "personal",
            "metadata": {"publication_intent": "public"},
        },
        actor=owner,
        idempotency_key="derivative-intake",
    )
    preview = domain.update_intake(
        intake["intake_id"],
        {"state": "preview_ready", "preview": {"summary": "Synthetic source"}},
        actor=owner,
        idempotency_key="derivative-preview",
    )
    raw = json.dumps(
        {
            "document": {"body_text": SOURCE_TEXT},
            "origin": {"text": "PRIVATE CHAT NOTE: learn later"},
        },
        ensure_ascii=False,
    )
    manifest = {
        "body_text": SOURCE_TEXT,
        "content_hash": hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
        "canonical_url": "https://technical.example/architecture",
        "final_url": "https://technical.example/architecture",
        "captured_at": "2026-10-19T20:00:00+00:00",
        "fetcher_version": "anonymous-safe-fetch-v1",
        "authenticated": False,
        "publicly_accessible": True,
        "context_separated": True,
        "content_kind": "external_article",
        "privacy_assessment": "not_assessed",
        "observed_restrictions": [],
    }
    source = domain.retain_source(
        intake["intake_id"],
        {
            "raw_text": raw,
            "preview_revision": preview["preview_revision"],
            "rights": "unknown",
            "metadata": {
                "visibility": "private",
                "publication_intent": "public",
                "extraction": {"body_text": SOURCE_TEXT},
            },
        },
        actor=owner,
        idempotency_key="derivative-keep",
        external_document_manifest=manifest,
    )
    model = FixtureModelRunner(
        {
            "knowledge_proposal_v1": {
                "no_change": False,
                "title": "异步任务的处理边界",
                "body": SUMMARY_BODY,
                "summary": "交付与处理分离",
                "risk_flags": [],
                "claims": [
                    {
                        "text": SUMMARY_BODY,
                        "quote": SOURCE_TEXT,
                        "evidence_index": 0,
                        "relation": "add",
                        "personal_expression": False,
                    }
                ],
            }
        },
        development=True,
    )
    workflow = domain.claim_task("derivative-worker", actor=owner, kinds=["knowledge.generate"])
    assert workflow is not None
    proposed = asyncio.run(
        GenerationService(domain, model, allow_fixture=True).generate(
            [{"source_id": source["source_id"]}],
            task_id=workflow["task_id"],
            actor=generator,
            idempotency_key="derivative-generate",
            workflow_task_id=workflow["task_id"],
        )
    )
    assert proposed["status"] == "proposed"
    change = domain.get_change(proposed["change_id"], actor=owner)
    assert change["publication_eligible"] is False and change["publication_intent"] == "public"
    assert change["derivative_input_manifest"]["model_input_hash"] == model.calls[0].fingerprint
    patch_hash = change["patch_hash"]
    model.outputs["knowledge_review_v1"] = {
        "decision": "approve",
        "reason": "Explicit synthetic fixture, not a legal approval",
        "patch_hash": patch_hash,
        "risk_flags": [],
        "checks": [{"evidence_index": 0, "quote": SOURCE_TEXT, "supports_patch": True}],
        "publication_checks": {
            key: {"passed": True, "reason": "Synthetic technical fixture reviewed independently"}
            for key in PUBLICATION_CHECKS
        },
    }
    reviewed = asyncio.run(
        AIReviewService(domain, model, allow_fixture=True).review(
            change["change_id"],
            task_id=workflow["task_id"],
            actor=reviewer,
            idempotency_key="derivative-review",
        )
    )
    assert reviewed["status"] == "approved"
    assert domain.get_change(change["change_id"], actor=owner)["patch_hash"] == patch_hash
    assert "PRIVATE CHAT NOTE" not in json.dumps([call.payload for call in model.calls])
    applied = domain.apply_change(
        change["change_id"], actor=writer, idempotency_key="derivative-apply"
    )
    assert applied["commit_sha"]
    entry = domain.get_entry(change["entry_id"], actor=owner)
    assert entry["publication_eligible"] is True
    assert domain.get_source(source["source_id"], actor=owner)["rights"] == "unknown"
    artifact = domain.verify_publication_entry(entry, actor=owner)
    assert artifact["body"] == SUMMARY_BODY
    assert "PRIVATE CHAT NOTE" not in json.dumps(artifact)
    assert SOURCE_TEXT not in json.dumps(artifact)
    return entry


def test_original_derivative_through_real_review_and_git(tmp_path):
    domain = DomainService(tmp_path / "domain.db")
    owner = Actor("derivative-owner", "human", "user", frozenset({"*"}))
    generator = Actor(owner.owner_id, "generator", "agent", frozenset({"*"}))
    reviewer = Actor(owner.owner_id, "reviewer", "agent", frozenset({"*"}))
    writer = Actor(owner.owner_id, "writer", "service", frozenset({"*"}))
    try:
        entry = build_reviewed_derivative(
            domain,
            owner=owner,
            generator=generator,
            reviewer=reviewer,
            writer=writer,
        )
        assert entry["publication_certificate"]["candidate_hash"]
        assert entry["metadata"]["generation"]["fixture"] is True
    finally:
        domain.close()


def test_existing_public_derivative_increment_reissues_exact_version_certificate(tmp_path):
    domain = DomainService(tmp_path / "domain.db")
    owner = Actor("derivative-owner", "human", "user", frozenset({"*"}))
    generator = Actor(owner.owner_id, "generator", "agent", frozenset({"*"}))
    reviewer = Actor(owner.owner_id, "reviewer", "agent", frozenset({"*"}))
    writer = Actor(owner.owner_id, "writer", "service", frozenset({"*"}))
    try:
        old = build_reviewed_derivative(
            domain,
            owner=owner,
            generator=generator,
            reviewer=reviewer,
            writer=writer,
        )
        added = "持久化任务使消费者能够恢复未完成的处理，而不需要生产者重新发起最初的请求。"
        body = old["body"] + "\n\n" + added
        model = FixtureModelRunner(
            {
                "knowledge_proposal_v1": {
                    "no_change": False,
                    "title": old["title"],
                    "body": body,
                    "summary": "补充恢复路径边界",
                    "risk_flags": [],
                    "claims": [
                        {
                            "text": added,
                            "quote": SOURCE_TEXT,
                            "evidence_index": 0,
                            "relation": "add",
                            "personal_expression": False,
                        }
                    ],
                }
            },
            development=True,
        )
        result = asyncio.run(
            GenerationService(domain, model, allow_fixture=True).generate(
                old["evidence_refs"],
                entry_id=old["entry_id"],
                task_id="increment-model-task",
                actor=generator,
                idempotency_key="increment-generate",
            )
        )
        assert result["status"] == "proposed"
        change = domain.get_change(result["change_id"], actor=owner)
        assert change["base_revision"] == 1 and change["state"] == "needs_ai_review"
        assert change["derivative_input_manifest"]["public_catalogue"] == []
        assert model.calls[0].payload["existing_entry"]["body"] == old["body"]
        assert "metadata" not in model.calls[0].payload["existing_entry"]
        task = domain.claim_task("increment-review", actor=owner, kinds=["knowledge.review"])
        assert task is not None and task["task_id"] == change["task_id"]
        model.outputs["knowledge_review_v1"] = {
            "decision": "approve",
            "reason": "Synthetic incremental technical fixture",
            "patch_hash": change["patch_hash"],
            "risk_flags": [],
            "checks": [{"evidence_index": 0, "quote": SOURCE_TEXT, "supports_patch": True}],
            "publication_checks": {
                key: {"passed": True, "reason": "Synthetic independent incremental assessment"}
                for key in PUBLICATION_CHECKS
            },
        }
        reviewed = asyncio.run(
            AIReviewService(domain, model, allow_fixture=True).review(
                change["change_id"],
                task_id=task["task_id"],
                actor=reviewer,
                idempotency_key="increment-review",
            )
        )
        assert reviewed["status"] == "approved"
        domain.apply_change(change["change_id"], actor=writer, idempotency_key="increment-apply")
        current = domain.get_entry(old["entry_id"], actor=owner)
        assert current["revision"] == 2 and current["publication_eligible"]
        assert (
            current["publication_certificate"]["certificate_id"]
            != old["publication_certificate"]["certificate_id"]
        )
        assert domain.verify_publication_entry(current, actor=owner)["body"] == body
    finally:
        domain.close()
