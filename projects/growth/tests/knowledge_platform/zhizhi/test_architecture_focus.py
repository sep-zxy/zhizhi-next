"""The changed product focus applies to existing candidate/material paths."""

from ahadiff.growth import (
    chat_model,
    knowledge_card_chat,
    knowledge_first,
    knowledge_material_model,
    model_feedback,
    model_opportunities,
)
from ahadiff.growth.knowledge_focus import FOCUS_PROMPT, assess_focus, rank_candidates
from ahadiff.growth.local import GrowthLocalRepository


def test_architecture_precedes_syntax_even_with_more_syntax_commits():
    rows = [
        {"candidate_id": "syntax", "title": "可选链语法与括号", "evidence_count": 100},
        {"candidate_id": "design", "title": "Redis 缓存一致性与失效取舍", "evidence_count": 1},
        {"candidate_id": "stack", "title": "PostgreSQL 深度应用", "evidence_count": 2},
    ]
    ranked = rank_candidates(rows)
    assert [item["candidate_id"] for item in ranked] == ["design", "stack", "syntax"]
    assert len(ranked) == 3  # Historical syntax knowledge is not deleted.
    assert "learning_focus" not in rows[0]  # Input identities and history remain untouched.


def test_related_stack_facets_are_suggestions_not_silent_merges():
    rows = [
        {"candidate_id": "one", "title": "Redis 缓存一致性边界"},
        {"candidate_id": "two", "title": "Redis 事务一致性的取舍"},
        {"candidate_id": "three", "title": "Redis 线程池资源隔离"},
    ]
    ranked = {item["candidate_id"]: item for item in rank_candidates(rows)}
    assert ranked["one"]["related_candidate_ids"] == ["two"]
    assert ranked["three"]["related_candidate_ids"] == []
    assert set(ranked) == {"one", "two", "three"}


def test_all_existing_model_prompts_adopt_architecture_focus():
    for module in (
        model_opportunities,
        knowledge_material_model,
        knowledge_card_chat,
        model_feedback,
        chat_model,
    ):
        assert FOCUS_PROMPT in module._PROMPT
    assert FOCUS_PROMPT in knowledge_card_chat._STREAM_PROMPT
    assert "语法输出题" in knowledge_material_model._PROMPT


def test_actual_candidate_aggregation_preserves_ids_and_new_ranking(tmp_path, monkeypatch):
    rows = [
        {
            "opportunity_id": "o-syntax",
            "title": "可选链语法",
            "reason": "局部表达式语法",
            "recommendation_status": "pending",
            "project_id": "p",
            "canonical_local_path": None,
            "uncertainties_json": "[]",
        },
        {
            "opportunity_id": "o-design",
            "title": "Kafka 可靠性与一致性",
            "reason": "在故障下选择投递语义",
            "recommendation_status": "pending",
            "project_id": "p",
            "canonical_local_path": None,
            "uncertainties_json": "[]",
        },
    ]
    monkeypatch.setattr(knowledge_first, "_opportunities", lambda ledger, ids=None: rows)
    with GrowthLocalRepository(tmp_path / "growth.sqlite") as ledger:
        first = knowledge_first.candidates(ledger)
        second = knowledge_first.candidates(ledger)
    assert first[0]["title"] == "Kafka 可靠性与一致性"
    assert [item["candidate_id"] for item in first] == [item["candidate_id"] for item in second]
    assert first[1]["opportunity_ids"] == ["o-syntax"]
    assert first[1]["concept_key"] == knowledge_first._key("可选链语法")


def test_unknown_description_never_invents_architecture_or_mastery():
    result = assess_focus("一个改动", "证据还不完整")
    assert result["category"] == "needs_context"
    assert "mastery" not in result
