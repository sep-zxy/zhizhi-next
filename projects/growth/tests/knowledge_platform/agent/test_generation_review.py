"""Service unit fixtures, distinct from real DomainService/Git/HTTP integration tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.ai_review import AIReviewService
from ahadiff.knowledge_platform.ai_review.service import REVIEWED_FIELDS
from ahadiff.knowledge_platform.generation import GenerationService
from ahadiff.knowledge_platform.generation.service import (
    evidence_classification,
    merge_open_questions,
    publication_policy,
)
from ahadiff.knowledge_platform.model import FixtureModelRunner
from ahadiff.knowledge_platform.model.contracts import canonical_hash

GENERATOR = SimpleNamespace(kind="agent", actor_id="generator")
REVIEWER = SimpleNamespace(kind="agent", actor_id="reviewer")
SOURCE = {"source_id": "source-1"}
TEXT = "水在标准大气压下的沸点为100摄氏度。"
PROPOSAL = {
    "no_change": False,
    "title": "沸点",
    "body": TEXT,
    "summary": "补充条件",
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


class MemoryDomain:
    """No network/storage effects; tests only generator/reviewer contract behavior."""

    def __init__(self):
        self.text = TEXT
        self.change = {
            "change_id": "change-1",
            "entry_id": "entry-1",
            "base_revision": 0,
            "body": TEXT,
            "patch_hash": "patch-1",
            "evidence_refs": [SOURCE],
            "risk_flags": ["requires_ai_review"],
            "state": "needs_ai_review",
        }
        self.proposals = []
        self.reviews = []
        self.review_contexts = []
        self.entries = []

    def list_entries(self, *, actor, limit=100, offset=0):
        return self.entries[offset : offset + limit]

    def read_evidence(self, ref, *, actor):
        return {"text": self.text, "content_hash": "verified"}

    def get_learning_outcome(self, outcome_id, *, actor):
        return {"user_text": self.text, "metadata": {}}

    def get_entry(self, entry_id, *, actor, revision=None):
        return {
            "entry_id": entry_id,
            "body": "旧内容",
            "revision": revision or 1,
            "visibility": "private",
        }

    def propose_change(self, payload, **kwargs):
        self.proposals.append(payload)
        return {**self.change, **payload}

    def get_change(self, change_id, *, actor):
        return deepcopy(self.change)

    def record_review(self, change_id, payload, **kwargs):
        self.reviews.append(payload)
        self.review_contexts.append(kwargs)
        return {"review_id": "review-1", **payload}


def generate(domain, output=None, *, allow_fixture=True, refs=None):
    runner = FixtureModelRunner({"knowledge_proposal_v1": output or PROPOSAL}, development=True)
    service = GenerationService(domain, runner, allow_fixture=allow_fixture)
    return asyncio.run(
        service.generate(
            refs or [SOURCE], task_id="task-1", actor=GENERATOR, idempotency_key="generate-1"
        )
    )


def review(domain, output=None):
    value = {
        "decision": "approve",
        "reason": "原文支持且未改变核心结论",
        "patch_hash": "patch-1",
        "risk_flags": [],
        "checks": [{"evidence_index": 0, "quote": TEXT, "supports_patch": True}],
    }
    value.update(output or {})
    runner = FixtureModelRunner({"knowledge_review_v1": value}, development=True)
    service = AIReviewService(domain, runner, allow_fixture=True)
    return asyncio.run(
        service.review("change-1", task_id="task-2", actor=REVIEWER, idempotency_key="review-1")
    ), runner


def test_generation_proposes_only_and_requires_independent_review():
    domain = MemoryDomain()
    result = generate(domain)
    assert result["status"] == "proposed" and len(domain.proposals) == 1
    assert domain.proposals[0]["risk_flags"] == ["requires_ai_review"]
    assert not domain.reviews


def test_original_unresolved_question_has_source_not_learner_provenance():
    domain = MemoryDomain()
    question = "在什么边界条件下应改用同步调用？"
    domain.text = TEXT + question
    value = deepcopy(PROPOSAL)
    value["unresolved_questions"] = [{"text": question, "quote": question, "evidence_index": 0}]
    result = generate(domain, value)
    assert result["status"] == "proposed"
    metadata = domain.proposals[0]["metadata"]
    assert metadata["open_questions"] == [question]
    record = metadata["open_question_records"][0]
    assert record["origin_kind"] == "source" and record["evidence_ref"] == SOURCE
    assert record["state"] == "open" and record["question_id"].startswith("question_")


def test_fabricated_question_is_not_saved_as_a_learner_weakness():
    domain = MemoryDomain()
    value = deepcopy(PROPOSAL)
    value["unresolved_questions"] = [
        {"text": "用户为什么不会设计架构？", "quote": TEXT, "evidence_index": 0}
    ]
    assert generate(domain, value)["reason"] == "QUESTION_QUOTE_NOT_IN_EVIDENCE"
    assert not domain.proposals


def test_question_merge_preserves_prior_questions_deduplicates_and_does_not_reopen():
    old = {
        "metadata": {
            "open_questions": ["仍待验证的问题？", "仍待验证的问题？"],
            "open_question_records": [
                {"question_id": "prior", "text": "已经解决的问题？", "state": "resolved"}
            ],
        }
    }
    question = "已经解决的问题？"
    questions, records, changed = merge_open_questions(
        old,
        [{"text": question, "quote": question, "evidence_index": 0}],
        [SOURCE],
        [question],
    )
    assert questions == ["仍待验证的问题？"]
    assert records == old["metadata"]["open_question_records"] and not changed


def test_evidence_bound_question_can_be_a_metadata_only_incremental_change():
    domain = MemoryDomain()
    question = "怎样验证服务边界的隔离效果？"
    domain.text = question
    value = {
        **PROPOSAL,
        "body": "旧内容",
        "claims": [],
        "unresolved_questions": [{"text": question, "quote": question, "evidence_index": 0}],
    }
    model = FixtureModelRunner({"knowledge_proposal_v1": value}, development=True)
    result = asyncio.run(
        GenerationService(domain, model, allow_fixture=True).generate(
            [{"learning_outcome_id": "real-user-question"}],
            entry_id="entry-1",
            task_id="question",
            actor=GENERATOR,
            idempotency_key="question",
        )
    )
    assert result["status"] == "proposed"
    assert domain.proposals[0]["body"] == "旧内容"
    assert (
        domain.proposals[0]["metadata"]["open_question_records"][0]["origin_kind"]
        == "user_statement"
    )


def test_production_service_rejects_fixture_result():
    domain = MemoryDomain()
    assert generate(domain, allow_fixture=False)["status"] == "blocked"
    assert not domain.proposals


def test_hallucinated_quote_cannot_propose():
    domain = MemoryDomain()
    value = deepcopy(PROPOSAL)
    value["claims"][0]["quote"] = "并不存在的证据"
    assert generate(domain, value)["status"] == "needs_evidence"
    assert not domain.proposals


def test_fabricated_personal_view_cannot_be_saved_as_user_opinion():
    domain = MemoryDomain()
    value = deepcopy(PROPOSAL)
    value["body"] = value["claims"][0]["text"] = "我认为这个事实绝对正确。"
    value["claims"][0]["personal_expression"] = True
    result = generate(domain, value, refs=[{"learning_outcome_id": "outcome-1"}])
    assert result["status"] == "needs_evidence" and not domain.proposals


def test_independent_review_gets_old_text_diff_and_original_evidence():
    domain = MemoryDomain()
    domain.change["base_revision"] = 1
    domain.change["metadata"] = {
        "open_question_records": [{"text": "原文明示的待解问题？", "origin_kind": "source"}]
    }
    result, runner = review(domain)
    assert result["status"] == "approved"
    payload = runner.calls[0].payload
    assert payload["old_body"] == "旧内容" and payload["evidence"][0]["text"] == TEXT
    assert "generator" not in payload and "-旧内容" in payload["diff"]
    assert payload["open_question_records"] == domain.change["metadata"]["open_question_records"]
    assert payload["new_semantic_patch"]["metadata"] == domain.change["metadata"]
    assert payload["old_semantic_patch"]["visibility"] == "private"
    assert domain.review_contexts[0]["reviewed_fields"] == REVIEWED_FIELDS
    assert "reviewed_fields" not in domain.reviews[0]
    assert domain.reviews[0]["expected_patch_hash"] == "patch-1"


def relation_fixture():
    domain = MemoryDomain()
    relation = {
        "kind": "confirmed",
        "target_entry_id": "target-1",
        "target_revision": 1,
        "relation_type": "constrains",
        "statement": "沸点受压强条件约束。",
        "evidence_refs": [{**SOURCE, "content_hash": "verified", "locator": {"quote": TEXT}}],
    }
    domain.change["metadata"] = {"relations": [relation]}
    check = {
        "relation_index": 0,
        "relation_hash": canonical_hash(relation),
        "supported": True,
        "reason": "原始证据明确给出了该约束条件。",
        "evidence_checks": [{"evidence_index": 0, "quote": TEXT, "supports_relation": True}],
    }
    return domain, relation, check


def test_generic_approval_cannot_attest_relation_metadata():
    domain, _, _ = relation_fixture()
    result, runner = review(domain)
    assert result["status"] == "approved"
    assert domain.review_contexts[0]["reviewed_relations_hash"] is None
    payload = runner.calls[0].payload
    assert payload["new_semantic_patch"]["metadata"] == domain.change["metadata"]
    assert payload["relation_targets"]["target-1"]["body"] == "旧内容"
    assert payload["relation_targets"]["target-1"]["revision"] == 1


def test_specific_relation_review_gets_code_generated_python_only_proof():
    domain, relation, check = relation_fixture()
    result, _ = review(domain, {"relation_checks": [check]})
    assert result["status"] == "approved"
    assert domain.review_contexts[0]["reviewed_relations_hash"] == canonical_hash([relation])
    assert "reviewed_relations_hash" not in domain.reviews[0]


@pytest.mark.parametrize("failure", ["relation_hash", "quote", "source_hash", "missing_quote"])
def test_relation_proof_rejects_wrong_binding(failure):
    domain, relation, check = relation_fixture()
    if failure == "relation_hash":
        check["relation_hash"] = "stale"
    elif failure == "quote":
        check["evidence_checks"][0]["quote"] = "不在此关系引用里的文字"
    elif failure == "source_hash":
        relation["evidence_refs"][0]["content_hash"] = "wrong-source-hash"
        check["relation_hash"] = canonical_hash(relation)
    else:
        check["evidence_checks"] = []
    result, _ = review(domain, {"relation_checks": [check]})
    assert result["status"] == "needs_evidence" and not domain.reviews


@pytest.mark.parametrize("revision", [None, 0, True, "1", 2])
def test_relation_proof_rejects_missing_invalid_or_stale_target_revision(revision):
    domain, relation, check = relation_fixture()
    if revision is None:
        relation.pop("target_revision")
    else:
        relation["target_revision"] = revision
    check["relation_hash"] = canonical_hash(relation)
    result, _ = review(domain, {"relation_checks": [check]})
    assert result["status"] == "needs_evidence"
    assert result["reason"] == "RELATION_REVIEW_TARGET_REVISION_MISMATCH"
    assert not domain.reviews


def test_partial_or_negative_relation_checks_never_attest_the_group():
    domain, relation, check = relation_fixture()
    domain.change["metadata"]["relations"].append({**relation, "target_entry_id": "target-2"})
    result, _ = review(domain, {"relation_checks": [check]})
    assert result["status"] == "approved"
    assert domain.review_contexts[0]["reviewed_relations_hash"] is None
    domain, _, check = relation_fixture()
    check["supported"] = False
    result, _ = review(domain, {"relation_checks": [check]})
    assert result["status"] == "approved"
    assert domain.review_contexts[0]["reviewed_relations_hash"] is None


def test_relation_target_classification_strengthens_the_model_request():
    domain, _, check = relation_fixture()
    original = domain.get_entry
    domain.get_entry = lambda entry_id, **kwargs: {
        **original(entry_id, **kwargs),
        "metadata": {"evidence_classification": "employer"},
    }
    result, runner = review(domain, {"relation_checks": [check]})
    assert result["status"] == "approved"
    assert runner.calls[0].input_classification == "employer"


@pytest.mark.parametrize(
    "risk", ["core_conclusion", "important_merge_delete", "personal_position", "privacy_exposure"]
)
def test_each_human_gate_prevents_ai_approval(risk):
    domain = MemoryDomain()
    domain.change["risk_flags"].append(risk)
    result, runner = review(domain)
    assert result["status"] == "needs_user" and not runner.calls and not domain.reviews


def test_new_major_reviewer_risk_is_persisted_not_just_returned():
    domain = MemoryDomain()
    result, _ = review(domain, {"risk_flags": ["core_conclusion"]})
    assert result["status"] == "needs_user"
    assert domain.reviews[0]["decision"] == "needs_user"
    assert "core_conclusion" in domain.reviews[0]["risk_flags"]


def test_proposer_cannot_review_self_or_consume_model_quota():
    domain = MemoryDomain()
    domain.change["proposer_id"] = REVIEWER.actor_id
    result, runner = review(domain)
    assert result["reason"] == "INDEPENDENT_REVIEWER_REQUIRED"
    assert not runner.calls and not domain.reviews


def test_review_hash_mismatch_is_rejected():
    domain = MemoryDomain()
    result, _ = review(domain, {"patch_hash": "different"})
    assert result["status"] == "invalid_output" and not domain.reviews


def test_review_needs_evidence_is_persisted():
    domain = MemoryDomain()
    result, _ = review(domain, {"decision": "needs_evidence", "reason": "缺失适用条件"})
    assert result["status"] == "needs_evidence"
    assert domain.reviews[0]["decision"] == "needs_evidence"


def test_exact_title_rebases_once_before_proposing():
    domain = MemoryDomain()
    domain.entries = [{"entry_id": "entry-1", "title": "沸点", "metadata": {}}]
    result = generate(domain)
    assert len(domain.proposals) == 1 and domain.proposals[0]["base_revision"] == 1
    assert domain.proposals[0]["entry_id"] == "entry-1"
    assert len(result["model_attempts"]) == 2


def test_incremental_generation_preserves_and_reads_old_original_evidence():
    domain = MemoryDomain()
    old_ref = {"source_id": "old-source", "content_hash": "old-pinned-hash"}
    old = domain.get_entry("entry-1", actor=GENERATOR)
    old["evidence_refs"] = [old_ref, SOURCE]
    domain.get_entry = lambda entry_id, **kwargs: deepcopy(old)
    reads = []

    def read(ref, *, actor):
        reads.append(dict(ref))
        return {"text": "原始架构证据" if ref == old_ref else TEXT, "content_hash": "verified"}

    domain.read_evidence = read
    model = FixtureModelRunner({"knowledge_proposal_v1": PROPOSAL}, development=True)
    refs = [dict(SOURCE)]
    result = asyncio.run(
        GenerationService(domain, model, allow_fixture=True).generate(
            refs,
            task_id="incremental",
            actor=GENERATOR,
            idempotency_key="incremental",
            entry_id="entry-1",
        )
    )
    assert result["status"] == "proposed"
    assert domain.proposals[0]["evidence_refs"] == [SOURCE, old_ref]
    assert refs == [SOURCE]  # Do not mutate the caller's task payload.
    assert reads == [SOURCE, old_ref]
    assert model.calls[0].payload["evidence"][1]["text"] == "原始架构证据"
    assert domain.proposals[0]["metadata"]["claims"][0]["evidence_index"] == 0


def test_unreadable_old_evidence_stops_incremental_model_call():
    domain = MemoryDomain()
    old = domain.get_entry("entry-1", actor=GENERATOR)
    old["evidence_refs"] = [{"source_id": "missing-original"}]
    domain.get_entry = lambda entry_id, **kwargs: deepcopy(old)

    def read(ref, *, actor):
        if ref != SOURCE:
            raise ValueError("original evidence unavailable")
        return {"text": TEXT}

    domain.read_evidence = read
    model = FixtureModelRunner({"knowledge_proposal_v1": PROPOSAL}, development=True)
    with pytest.raises(ValueError, match="original evidence unavailable"):
        asyncio.run(
            GenerationService(domain, model, allow_fixture=True).generate(
                [SOURCE],
                task_id="incremental",
                actor=GENERATOR,
                idempotency_key="incremental",
                entry_id="entry-1",
            )
        )
    assert not model.calls and not domain.proposals


def test_ambiguous_alias_does_not_merge_or_create_duplicate():
    domain = MemoryDomain()
    domain.entries = [
        {"entry_id": "entry-1", "title": "沸点", "metadata": {}},
        {"entry_id": "entry-2", "title": "沸腾", "metadata": {"aliases": ["沸点"]}},
    ]
    assert generate(domain)["reason"] == "AMBIGUOUS_ENTRY_TARGET"
    assert not domain.proposals


def test_catalogue_private_data_strengthens_public_source_classification():
    domain = MemoryDomain()
    domain.entries = [{"entry_id": "entry-2", "title": "另一个主题", "metadata": {}}]
    model = FixtureModelRunner({"knowledge_proposal_v1": PROPOSAL}, development=True)
    asyncio.run(
        GenerationService(
            domain, model, input_classification="public", allow_fixture=True
        ).generate([SOURCE], task_id="task-1", actor=GENERATOR, idempotency_key="public")
    )
    assert model.calls[0].input_classification == "personal"
    assert domain.proposals[0]["metadata"]["input_classification"] == "personal"


def test_nested_classification_cannot_be_downgraded_by_public_label():
    assert (
        evidence_classification(
            [
                {
                    "classification": "public",
                    "descriptor": {"metadata": {"classification": "employer"}},
                }
            ],
            "public",
        )
        == "employer"
    )
    assert (
        evidence_classification(
            [{"classification": "third_party_private"}, {"classification": "employer"}], "public"
        )
        == "secret"
    )


def test_unknown_and_composite_classification_cannot_downgrade():
    assert evidence_classification([{"classification": "unknown"}], "personal") == "secret"
    assert (
        evidence_classification([{"classification": ["public", "employer"]}], "personal")
        == "employer"
    )


def test_review_audit_is_persisted_without_raw_input_or_output():
    domain = MemoryDomain()
    result, _ = review(domain)
    audit = domain.reviews[0]["reviewer_model"]
    assert audit["result_hash"] == result["model"]["result_hash"] and audit["fixture"]
    assert "output" not in audit and "payload" not in audit


def test_public_derivative_default_does_not_publish_raw_or_private_evidence():
    public_source = {"source_id": "source", "visibility": "private", "rights": "summary_allowed"}
    assert publication_policy([public_source], [SOURCE], None, set()) == ("public", "public", None)
    private_source = {**public_source, "descriptor": {"metadata": {"classification": "employer"}}}
    assert publication_policy([private_source], [SOURCE], None, set()) == (
        "private",
        "public",
        "SOURCE_CLASSIFICATION_PRIVATE",
    )
    assert publication_policy([{}], [{"learning_outcome_id": "outcome"}], None, set()) == (
        "private",
        "public",
        "LEARNING_STATEMENT_PRIVATE",
    )
    assert publication_policy([{}], [SOURCE], None, set()) == (
        "private",
        "public",
        "SOURCE_RIGHTS_UNVERIFIED",
    )


def test_existing_private_intent_never_silently_expands():
    evidence = [{"rights": "public_domain"}]
    assert publication_policy(evidence, [SOURCE], {"visibility": "private"}, set()) == (
        "private",
        "private",
        None,
    )
    risks = set()
    assert publication_policy([{}], [SOURCE], {"visibility": "public"}, risks)[0] == "private"
    assert "privacy_exposure" in risks
