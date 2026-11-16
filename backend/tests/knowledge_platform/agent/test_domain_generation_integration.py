"""Real SQLite/Git and generation/review integration with explicit fixture models only."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from ahadiff.knowledge_platform.ai_review import AIReviewService
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.generation import GenerationService
from ahadiff.knowledge_platform.model import FixtureModelRunner
from ahadiff.knowledge_platform.model.contracts import canonical_hash

if TYPE_CHECKING:
    from pathlib import Path

TEXT = "服务边界使用异步消息隔离失败。"


def test_retained_source_to_reviewed_git_commit(tmp_path: Path) -> None:
    domain = DomainService(tmp_path / "platform.db")
    owner = Actor("fixture-owner", "human", "user", frozenset({"*"}))
    generator = Actor(
        "fixture-owner", "generator", "agent", frozenset({"knowledge:read", "knowledge:write"})
    )
    reviewer = Actor(
        "fixture-owner", "reviewer", "agent", frozenset({"knowledge:read", "knowledge:review"})
    )
    writer = Actor("fixture-owner", "writer", "service", frozenset({"*"}))
    question = "当消息不能重放时，怎样验证恢复路径？"
    source_text = TEXT + "\n" + question
    try:
        intake = domain.create_intake(
            {"text": source_text, "data_classification": "public"},
            actor=owner,
            idempotency_key="intake",
        )
        preview = domain.update_intake(
            intake["intake_id"],
            {
                "state": "preview_ready",
                "preview": {"summary": TEXT},
            },
            actor=owner,
            idempotency_key="preview",
        )
        source = domain.retain_source(
            intake["intake_id"],
            {
                "raw_text": source_text,
                "preview_revision": preview["preview_revision"],
                "rights": "summary_allowed",
            },
            actor=owner,
            idempotency_key="keep",
        )
        model = FixtureModelRunner(
            {
                "knowledge_proposal_v1": {
                    "no_change": False,
                    "title": "异步服务边界",
                    "body": TEXT,
                    "summary": "失败隔离",
                    "risk_flags": [],
                    "unresolved_questions": [
                        {"text": question, "quote": question, "evidence_index": 0}
                    ],
                    "claims": [
                        {
                            "text": TEXT,
                            "quote": TEXT,
                            "evidence_index": 0,
                            "relation": "add",
                            "personal_expression": False,
                        }
                    ],
                }
            },
            development=True,
        )
        workflow = domain.claim_task("worker", actor=owner, kinds=["knowledge.generate"])
        assert workflow is not None
        proposed = asyncio.run(
            GenerationService(domain, model, allow_fixture=True).generate(
                [{"source_id": source["source_id"]}],
                task_id="fixture-generate",
                actor=generator,
                idempotency_key="propose",
                workflow_task_id=workflow["task_id"],
            )
        )
        assert proposed["status"] == "proposed"
        change = domain.get_change(proposed["change_id"], actor=owner)
        assert change["state"] == "needs_ai_review"
        assert change["task_id"] == workflow["task_id"]
        assert domain.claim_task("review-worker", actor=owner, kinds=["knowledge.review"]) is None
        assert change["visibility"] == "public" and change["publication_eligible"]
        model.outputs["knowledge_review_v1"] = {
            "decision": "approve",
            "reason": "原始证据直接支持失败隔离机制",
            "patch_hash": change["patch_hash"],
            "risk_flags": [],
            "checks": [{"evidence_index": 0, "quote": TEXT, "supports_patch": True}],
        }
        reviewed = asyncio.run(
            AIReviewService(domain, model, allow_fixture=True).review(
                change["change_id"],
                task_id="fixture-review",
                actor=reviewer,
                idempotency_key="review",
            )
        )
        assert reviewed["status"] == "approved"
        applied = domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply")
        entry = domain.get_entry(change["entry_id"], actor=owner)
        assert entry["body"] == TEXT and entry["revision"] == 1
        assert applied["commit_sha"]
        assert entry["metadata"]["generation"]["fixture"] is True
        from ahadiff.knowledge_platform.radar.service import RadarService

        suggestions = RadarService(domain).run(actor=owner)
        assert len(suggestions) == 1 and suggestions[0]["proposed_exercise"] == question
        assert suggestions[0]["target_entry_ids"] == [entry["entry_id"]]
        assert entry["metadata"]["open_question_records"][0]["origin_kind"] == "source"
        assert not domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])
        # Relation confirmation needs the real typed review path, not a generic approval.
        relation = {
            "kind": "confirmed",
            "target_entry_id": entry["entry_id"],
            "target_revision": entry["revision"],
            "relation_type": "explains",
            "statement": TEXT,
            "evidence_refs": [{**entry["evidence_refs"][0], "locator": {"quote": TEXT}}],
        }
        related = domain.propose_change(
            {
                "title": "消息边界与失败隔离",
                "body": TEXT,
                "visibility": "public",
                "evidence_refs": entry["evidence_refs"],
                "risk_flags": ["requires_ai_review"],
                "metadata": {"relations": [relation]},
            },
            actor=generator,
            idempotency_key="relation-proposal",
        )
        model.outputs["knowledge_review_v1"] = {
            "decision": "approve",
            "reason": "原文明确解释这两个条目的机制关联。",
            "patch_hash": related["patch_hash"],
            "risk_flags": [],
            "checks": [{"evidence_index": 0, "quote": TEXT, "supports_patch": True}],
            "relation_checks": [
                {
                    "relation_index": 0,
                    "relation_hash": canonical_hash(relation),
                    "supported": True,
                    "reason": "实际目标与原文逐字证据相符。",
                    "evidence_checks": [
                        {"evidence_index": 0, "quote": TEXT, "supports_relation": True}
                    ],
                }
            ],
        }
        relation_review = asyncio.run(
            AIReviewService(domain, model, allow_fixture=True).review(
                related["change_id"],
                task_id="relation-review",
                actor=reviewer,
                idempotency_key="relation-review",
            )
        )
        assert relation_review["status"] == "approved"
        assert relation_review["review"]["reviewed_relations_hash"] == canonical_hash([relation])
        assert "metadata" in relation_review["review"]["reviewed_fields"]
        assert model.calls[-1].payload["relation_targets"][entry["entry_id"]]["body"] == TEXT
        domain.apply_change(related["change_id"], actor=writer, idempotency_key="relation-apply")
        from ahadiff.knowledge_platform.relations import RelationService

        graph = RelationService(domain).neighbors(related["entry_id"], actor=owner)
        assert len(graph["edges"]) == 1
        assert graph["edges"][0]["kind"] == "confirmed" and graph["edges"][0]["is_fact"] is True
        assert graph["edges"][0]["target_entry_id"] == entry["entry_id"]
        # A genuine follow-up targets this entry without dropping its original source.
        learned = "消费端必须按业务标识去重。"
        outcome = domain.submit_learning_outcome(
            {
                "device_id": "fixture-device",
                "session_id": "fixture-followup",
                "version": 1,
                "entry_id": entry["entry_id"],
                "user_text": learned,
                "result": {},
            },
            actor=owner,
            idempotency_key="followup-outcome",
        )
        model.outputs["knowledge_proposal_v1"] = {
            "no_change": False,
            "title": entry["title"],
            "body": TEXT + "\n\n" + learned,
            "summary": "补充去重约束",
            "risk_flags": [],
            "claims": [
                {
                    "text": learned,
                    "quote": learned,
                    "evidence_index": 0,
                    "relation": "add",
                    "personal_expression": False,
                }
            ],
        }
        update = asyncio.run(
            GenerationService(domain, model, allow_fixture=True).generate(
                [{"learning_outcome_id": outcome["outcome_id"]}],
                entry_id=entry["entry_id"],
                task_id="followup-generate",
                actor=generator,
                idempotency_key="followup-propose",
            )
        )
        assert update["status"] == "proposed"
        incremental = domain.get_change(update["change_id"], actor=owner)
        assert incremental["entry_id"] == entry["entry_id"] and incremental["base_revision"] == 1
        assert incremental["evidence_refs"][0]["learning_outcome_id"] == outcome["outcome_id"]
        assert incremental["evidence_refs"][1] == entry["evidence_refs"][0]
        assert model.calls[-1].payload["evidence"][1]["text"] == source_text
        assert incremental["metadata"]["open_questions"] == [question]
        assert domain.get_entry(entry["entry_id"], actor=owner)["body"] == TEXT
    finally:
        domain.close()


def test_learning_payload_preserves_stable_concept_identity(tmp_path: Path) -> None:
    domain = DomainService(tmp_path / "platform.db")
    owner = Actor("fixture-owner", "human", "user", frozenset({"*"}))
    generator = Actor(
        "fixture-owner",
        "generator",
        "agent",
        frozenset({"*"}),
        allowed_classifications=frozenset({"public", "personal", "employer"}),
    )
    suggested_id = str(uuid5(NAMESPACE_URL, "fixture-owner/async-boundaries"))
    try:
        outcome = domain.submit_learning_outcome(
            {
                "device_id": "device-1",
                "session_id": "session-1",
                "version": 1,
                "user_text": TEXT,
                "result": {},
                "metadata": {
                    "concept_id": "async-boundaries",
                    "suggested_entry_id": suggested_id,
                    "input_classification": "employer",
                },
            },
            actor=owner,
            idempotency_key="outcome",
        )
        model = FixtureModelRunner(
            {
                "knowledge_proposal_v1": {
                    "no_change": False,
                    "title": "异步服务边界",
                    "body": TEXT,
                    "summary": "失败隔离",
                    "risk_flags": [],
                    "claims": [
                        {
                            "text": TEXT,
                            "quote": TEXT,
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
                [{"learning_outcome_id": outcome["outcome_id"]}],
                task_id="fixture-generate",
                actor=generator,
                idempotency_key="proposal",
            )
        )
        change = domain.get_change(result["change_id"], actor=owner)
        assert change["entry_id"] == suggested_id and change["base_revision"] == 0
        assert change["metadata"]["concept_id"] == "async-boundaries"
        assert change["metadata"]["concept_ids"] == ["async-boundaries"]
        assert change["visibility"] == "private"
        assert change["metadata"]["publication_intent"] == "public"
        assert domain.read_task(change["task_id"], actor=owner)["kind"] == "knowledge.review"
        assert model.calls[0].input_classification == "employer"
    finally:
        domain.close()
