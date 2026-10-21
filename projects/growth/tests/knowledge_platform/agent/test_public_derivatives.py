"""Explicit synthetic fixtures for isolated derivative generation, not legal/live approval."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.ai_review import AIReviewService
from ahadiff.knowledge_platform.ai_review.service import PUBLICATION_CHECKS
from ahadiff.knowledge_platform.generation import GenerationService
from ahadiff.knowledge_platform.generation.publication import summary_output_issues
from ahadiff.knowledge_platform.model import FixtureModelRunner
from ahadiff.knowledge_platform.model.contracts import canonical_hash

SOURCE = (
    "A queue separates the producer from the consumer. A failed consumer can retry independently."
)
BODY = "异步交付将接收请求和处理任务分为两个阶段，使处理端故障不必阻塞请求接收。"
URL = "https://technical.example/article"
GENERATOR = SimpleNamespace(kind="agent", actor_id="generator")
REVIEWER = SimpleNamespace(kind="agent", actor_id="reviewer")


class DerivativeDomain:
    def __init__(self):
        self.document = {
            "text": SOURCE,
            "document_id": "document-1",
            "content_hash": canonical_hash(SOURCE),
            "acquisition_hash": "acquisition-1",
            "canonical_url": URL,
            "evidence_ref": {
                "external_document_id": "document-1",
                "content_hash": canonical_hash(SOURCE),
            },
            "data_classification": "personal",
            "privacy_assessment": "not_assessed",
        }
        self.source = {
            "text": "PRIVATE ENVELOPE NOTE",
            "descriptor": {"metadata": {"publication_intent": "public"}},
        }
        self.entries = []
        self.proposals = []
        self.reviews = []
        self.change = {
            "change_id": "change-1",
            "entry_id": "entry-1",
            "base_revision": 0,
            "body": BODY,
            "title": "异步边界",
            "entry_type": "knowledge",
            "visibility": "public",
            "publication_intent": "public",
            "publication_eligible": False,
            "patch_hash": "patch-1",
            "risk_flags": ["requires_ai_review"],
            "evidence_refs": [self.document["evidence_ref"]],
            "metadata": {
                "private_note": "PRIVATE INTERNAL NOTE",
                "relations": [{"target_entry_id": "private-target"}],
            },
        }

    def read_evidence(self, ref, *, actor):
        return deepcopy(self.document if ref.get("external_document_id") else self.source)

    def read_external_document(self, source_id, *, actor):
        return deepcopy(self.document)

    def list_entries(self, *, actor, limit=100, offset=0):
        return deepcopy(self.entries[offset : offset + limit])

    def get_entry(self, entry_id, **kwargs):
        return deepcopy(next(entry for entry in self.entries if entry["entry_id"] == entry_id))

    def propose_change(self, payload, **kwargs):
        self.proposals.append((deepcopy(payload), deepcopy(kwargs)))
        return {**self.change, **payload}

    def get_change(self, change_id, *, actor):
        return deepcopy(self.change)

    def publication_review_context(self, change_id, *, actor):
        return {
            "policy_id": "fixture-original-summary",
            "policy_version": "1",
            "candidate_hash": "candidate-hash",
            "source_manifest_hash": "source-hash",
            "input_manifest_hash": "input-hash",
            "artifact": {
                "title": self.change["title"],
                "body": self.change["body"],
                "entry_type": "knowledge",
                "metadata": {},
                "citations": [{"url": URL, "title": "technical.example"}],
            },
        }

    def record_review(self, change_id, payload, **kwargs):
        self.reviews.append((deepcopy(payload), deepcopy(kwargs)))
        return {"review_id": "review-1", **payload}


def generated(domain, *, entry_id=None, body=BODY):
    output = {
        "no_change": False,
        "title": "异步边界",
        "body": body,
        "summary": "边界解释",
        "risk_flags": [],
        "claims": [
            {
                "text": body,
                "quote": SOURCE,
                "evidence_index": 0,
                "relation": "add",
                "personal_expression": False,
            }
        ],
    }
    runner = FixtureModelRunner({"knowledge_proposal_v1": output}, development=True)
    result = asyncio.run(
        GenerationService(domain, runner, allow_fixture=True).generate(
            [{"source_id": "source-1"}],
            task_id="generate-task",
            actor=GENERATOR,
            idempotency_key="generate",
            entry_id=entry_id,
        )
    )
    return result, runner


def reviewed(domain, *, checks=True):
    output = {
        "decision": "approve",
        "reason": "Synthetic content assessment",
        "patch_hash": "patch-1",
        "risk_flags": [],
        "checks": [{"evidence_index": 0, "quote": SOURCE, "supports_patch": True}],
    }
    if checks is not None:
        output["publication_checks"] = {
            key: {
                "passed": checks,
                "reason": "Synthetic fixture assessment; not a legal authorization",
            }
            for key in PUBLICATION_CHECKS
        }
    runner = FixtureModelRunner({"knowledge_review_v1": output}, development=True)
    result = asyncio.run(
        AIReviewService(domain, runner, allow_fixture=True).review(
            "change-1",
            task_id="review-task",
            actor=REVIEWER,
            idempotency_key="review",
        )
    )
    return result, runner


def test_clean_generation_excludes_private_envelope_and_private_catalogue():
    domain = DerivativeDomain()
    domain.entries = [
        {"entry_id": "private-1", "title": "PRIVATE CATALOGUE TITLE", "visibility": "private"}
    ]
    result, runner = generated(domain)
    assert result["status"] == "proposed"
    task = runner.calls[0]
    assert "PRIVATE" not in json.dumps(task.payload)
    assert task.payload["evidence"][0]["text"] == SOURCE
    assert task.input_refs == ("document-1",)
    payload, kwargs = domain.proposals[0]
    assert payload["evidence_refs"] == [domain.document["evidence_ref"]]
    assert payload["visibility"] == "public"
    assert payload["metadata"]["publication_block_reason"] == "DERIVATIVE_REVIEW_PENDING"
    manifest = kwargs["derivative_input_manifest"]
    assert manifest["model_input_hash"] == task.fingerprint
    assert manifest["public_catalogue"] == []
    assert manifest["documents"][0]["acquisition_hash"] == "acquisition-1"


def test_public_increment_preserves_unknown_private_metadata_without_model_exposure():
    domain = DerivativeDomain()
    old = {
        "entry_id": "existing-public",
        "revision": 2,
        "content_hash": "old-hash",
        "title": "旧边界",
        "body": "已公开的旧说明。",
        "visibility": "public",
        "publication_eligible": True,
        "publication_intent": "public",
        "entry_type": "knowledge",
        "evidence_refs": [domain.document["evidence_ref"]],
        "metadata": {
            "aliases": ["旧别名"],
            "tags": ["架构"],
            "custom_schema": {"note": "PRIVATE OLD NOTE"},
            "open_questions": ["旧问题"],
            "concept_ids": ["stable-concept"],
        },
    }
    domain.entries = [old]
    result, runner = generated(domain, entry_id=old["entry_id"], body=old["body"] + "\n\n" + BODY)
    assert result["status"] == "proposed"
    assert "PRIVATE" not in json.dumps(runner.calls[0].payload)
    payload, kwargs = domain.proposals[0]
    for key in ("aliases", "tags", "custom_schema", "open_questions", "concept_ids"):
        assert payload["metadata"][key] == old["metadata"][key]
    assert len(kwargs["derivative_input_manifest"]["documents"]) == 1
    assert "privacy_exposure" not in payload["risk_flags"]


def test_explicit_private_intent_cannot_become_public_derivative():
    domain = DerivativeDomain()
    domain.source["descriptor"]["metadata"]["publication_intent"] = "private"
    domain.source["text"] = SOURCE
    result, runner = generated(domain)
    assert "publication_candidate" not in runner.calls[0].payload
    assert result["status"] == "proposed"
    proposal, context = domain.proposals[0]
    assert proposal["visibility"] == "private"
    assert proposal["metadata"]["publication_intent"] == "private"
    assert "derivative_input_manifest" not in context


def test_public_review_covers_only_actual_artifact_and_issues_python_only_proof():
    domain = DerivativeDomain()
    result, runner = reviewed(domain)
    assert result["status"] == "approved"
    payload = runner.calls[0].payload
    assert "PRIVATE" not in json.dumps(payload)
    assert payload["relation_declarations"] == []
    record, context = domain.reviews[0]
    assert "metadata" not in context["reviewed_fields"]
    assert context["reviewed_relations_hash"] is None
    assert "publication_review" not in record
    proof = context["publication_review"]
    assert proof["candidate_hash"] == "candidate-hash" and proof["review_task_id"] == "review-task"
    assert set(proof["checks"]) == set(PUBLICATION_CHECKS)


@pytest.mark.parametrize("checks", [None, False])
def test_generic_approval_or_uncertainty_never_grants_publication(checks):
    domain = DerivativeDomain()
    result, _ = reviewed(domain, checks=checks)
    assert result["status"] == "needs_evidence"
    assert domain.reviews[0][0]["decision"] == "needs_evidence"
    assert "publication_review" not in domain.reviews[0][1]


@pytest.mark.parametrize(
    "body",
    [
        SOURCE,
        SOURCE + " Additional interpretation.",
        "```python\nprint(1)\n```",
        "![copied](https://technical.example/image.png)",
        "> A source quotation",
        "<script>x</script>",
        "A `code` fragment",
        "Contact private@example.com",
        "[unverified](https://other.example/page)",
    ],
)
def test_publication_scan_blocks_copy_assets_private_patterns_and_unverified_links(body):
    assert summary_output_issues("知识摘要", body, [SOURCE], {URL})


def test_publication_scan_is_not_itself_eligibility():
    assert summary_output_issues("架构边界", BODY, [SOURCE], {URL}) == []
    domain = DerivativeDomain()
    domain.change["body"] = SOURCE
    result, _ = reviewed(domain)
    assert result["status"] == "needs_evidence"
    assert "publication_review" not in domain.reviews[0][1]


def test_public_title_and_metadata_are_not_copy_or_privacy_escape_hatches():
    assert summary_output_issues(SOURCE, BODY, [SOURCE], {URL})
    assert summary_output_issues("安全标题", BODY, [SOURCE], {URL}, {"aliases": [SOURCE]})
    assert summary_output_issues(
        "安全标题", BODY, [SOURCE], {URL}, {"tags": ["private@example.com"]}
    )
