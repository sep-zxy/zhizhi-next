"""Rebuildability and Chinese retrieval without external embeddings."""

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.radar.service import RadarService
from ahadiff.knowledge_platform.search.index import SearchIndex, SearchService


def doc(identifier, owner, title, body, **changes):
    return {
        "entry_id": identifier,
        "owner_id": owner,
        "revision": 3,
        "title": title,
        "body": body,
        "visibility": "private",
        "evidence_refs": [{"source_id": "s1"}],
        **changes,
    }


def test_chinese_query_and_identity_scope():
    index = SearchIndex()
    try:
        index.rebuild(
            [
                doc("mine", "a", "认证与授权", "## 权限边界\n身份认证不等于操作授权"),
                doc("other", "b", "认证与授权", "别人的私人原话"),
                doc("raw", "a", "认证与授权", "原始私人素材", scope="sources"),
            ]
        )
        results = index.search("授权", owner_id="a", scope="wiki")
        assert [item["entry_id"] for item in results] == ["mine"]
        assert results[0]["revision"] == 3
        assert results[0]["evidence_refs"] == [{"source_id": "s1"}]
    finally:
        index.close()


def test_rebuild_removes_deleted_and_old_text():
    index = SearchIndex()
    try:
        index.rebuild([doc("one", "a", "历史概念", "oldunique")])
        index.rebuild([doc("one", "a", "新概念", "newunique", revision=4)])
        assert not index.search("oldunique", owner_id="a")
        assert index.search("newunique", owner_id="a")[0]["revision"] == 4
        index.rebuild([doc("one", "a", "新概念", "newunique", deleted=True)])
        assert not index.search("newunique", owner_id="a")
    finally:
        index.close()


def test_fts_syntax_is_data_not_query_operators():
    index = SearchIndex()
    try:
        index.rebuild([doc("one", "a", "认证", "授权")])
        index.search('" OR * NOT (', owner_id="a")
    finally:
        index.close()


class Catalog:
    def __init__(self, document):
        self.document = document

    def list_entries(self, **_kwargs):
        return [self.document]

    def get_entry(self, *_args, **_kwargs):
        return self.document


def test_nested_employer_classification_cannot_be_downgraded_to_public():
    item = doc(
        "one",
        "a",
        "架构授权",
        "授权边界",
        classification="public",
        metadata={"evidence_classification": "employer"},
    )
    actor = Actor("a", "agent", "agent", frozenset({"knowledge:read"}))
    with pytest.raises(DomainError, match="classification"):
        SearchService(Catalog(item)).search("授权", actor=actor)
    owner = Actor("a", "user", "user", frozenset({"knowledge:read"}))
    result = SearchService(Catalog(item)).search("授权", actor=owner)[0]
    assert result["classification"] == "employer"
    assert result["classification_labels"] == ["employer", "public"]


def test_unknown_projection_is_not_assumed_public_for_machine_actor():
    actor = Actor("a", "agent", "agent", frozenset({"knowledge:read"}))
    with pytest.raises(DomainError):
        SearchService(Catalog(doc("one", "a", "架构", "授权"))).search("授权", actor=actor)


def test_both_employer_and_third_party_permissions_are_required():
    item = doc(
        "one", "a", "架构", "授权", classification_labels=["employer", "third_party_private"]
    )
    actor = Actor(
        "a",
        "agent",
        "agent",
        frozenset({"knowledge:read"}),
        frozenset({"public", "personal", "employer"}),
    )
    with pytest.raises(DomainError):
        SearchService(Catalog(item)).search("授权", actor=actor)


def test_radar_legacy_questions_survive_provenance_upgrade_and_resolved_stays_closed():
    class RadarCatalog(Catalog):
        def __init__(self, document):
            super().__init__(document)
            self.claims = {}

        def create_radar_candidate(self, payload, *, identity, **_kwargs):
            previous = self.claims.get(identity)
            if previous:
                assert previous["payload"] == payload
                return previous
            task = {
                "task_id": str(len(self.claims)),
                "kind": "radar_suggestion",
                "payload": payload,
                "created_at": "2026-10-19",
            }
            self.claims[identity] = task
            return task

        def get_channel_state(self, *_args, **_kwargs):
            return {}

    document = doc(
        "one",
        "a",
        "架构",
        "事件溯源",
        content_hash="a" * 64,
        classification="personal",
        metadata={"open_questions": ["旧问题", "有来源问题", "已解决问题"]},
    )
    catalog = RadarCatalog(document)
    actor = Actor("a", "owner", "user", frozenset({"*"}))
    radar = RadarService(catalog)
    before = {item["proposed_exercise"]: item for item in radar.run(actor=actor)}
    document["metadata"]["open_question_records"] = [
        {
            "question_id": "q1",
            "text": "有来源问题",
            "state": "open",
            "origin_kind": "source",
            "evidence_ref": {"source_id": "s1"},
            "quote": "有来源问题",
        },
        {"question_id": "q2", "text": "已解决问题", "state": "resolved"},
    ]
    after = {item["proposed_exercise"]: item for item in radar.run(actor=actor)}
    assert set(after) == {"旧问题", "有来源问题"}
    assert "question_provenance" not in after["旧问题"]
    assert after["旧问题"]["suggestion_id"] == before["旧问题"]["suggestion_id"]
    assert after["有来源问题"]["suggestion_id"] != before["有来源问题"]["suggestion_id"]
    document["metadata"]["open_questions"] = "不能逐字变成候选"
    assert [item["proposed_exercise"] for item in radar.run(actor=actor)] == ["有来源问题"]
