"""Bounded history contracts using real isolated SQLite and bare Git, not model E2E."""
from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from ahadiff.knowledge_platform.auth import Actor, DomainAuthenticationMiddleware, TokenAuthenticator
from ahadiff.knowledge_platform.contracts import canonical
from ahadiff.knowledge_platform.core_routes import create_core_routes
from ahadiff.knowledge_platform.errors import DomainError


def commit(domain, owner, writer, source, previous=None, **overrides):
    payload = {
        "entry_id": "history-example", "title": "Synthetic architecture",
        "body": "Evidence says addition preserves history.\n", "visibility": "public",
        "evidence_refs": [{"source_id": source["source_id"]}], **overrides,
    }
    if previous:
        payload["base_revision"] = previous["revision"]
    key = str(payload.get("base_revision", 0))
    change = domain.propose_change(payload, actor=owner, idempotency_key="history-propose-" + key)
    if change["state"] != "ready":
        review = {"decision": "approve", "expected_patch_hash": change["patch_hash"]}
        challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
        review.update(decision_nonce=challenge["decision_nonce"], base_revision=change["base_revision"])
        domain.record_review(change["change_id"], review, actor=owner, idempotency_key="history-review-" + key)
    return domain.apply_change(change["change_id"], actor=writer, idempotency_key="history-apply-" + key)


@pytest.fixture
def versions(domain, owner, writer, source):
    first = commit(domain, owner, writer, source)
    second = commit(domain, owner, writer, source, first, title="Renamed architecture", body=first["body"] + "A technical stack boundary.\n")
    return first, second


def test_exact_versions_and_diff_are_readonly(domain, owner, versions):
    first, second = versions
    head = domain.writer.head()
    changes = domain.store.connection.total_changes
    result = domain.list_entry_versions(first["entry_id"], actor=owner, limit=1)
    assert [item["revision"] for item in result["items"]] == [2]
    assert result["items"][0]["title"] == "Renamed architecture"
    assert result["has_more"] is True
    older = domain.list_entry_versions(first["entry_id"], actor=owner, limit=1, offset=1)
    assert older["items"][0]["title"] == first["title"]
    assert older["has_more"] is False
    delta = domain.diff_entry_versions(first["entry_id"], actor=owner, from_revision=1, to_revision=2)
    assert "+A technical stack boundary." in delta["unified_diff"]
    assert delta["from_content_hash"] == first["content_hash"]
    assert delta["to_content_hash"] == second["content_hash"]
    assert domain.diff_entry_versions(first["entry_id"], actor=owner, from_revision=1, to_revision=1)["unified_diff"] == ""
    assert not {"commit_sha", "path", "body", "publication_eligible"} & result["items"][0].keys()
    assert domain.writer.head() == head
    assert domain.store.connection.total_changes == changes


@pytest.mark.parametrize("revision", [0, -1, True, "1", 2**40])
def test_invalid_revision(domain, owner, revision):
    with pytest.raises(DomainError) as exc:
        domain.diff_entry_versions("missing", actor=owner, from_revision=revision, to_revision=1)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (1, 1001), (True, 0), (1, -1)])
def test_invalid_pagination(domain, owner, limit, offset):
    with pytest.raises(DomainError) as exc:
        domain.list_entry_versions("missing", actor=owner, limit=limit, offset=offset)
    assert exc.value.status_code == 422


def test_owner_scope_and_missing_revision(domain, owner, versions):
    entry_id = versions[0]["entry_id"]
    other = Actor("other", "other", "user", frozenset({"*"}))
    for actor, revision in [(other, 1), (owner, 999)]:
        with pytest.raises(DomainError) as exc:
            domain.diff_entry_versions(entry_id, actor=actor, from_revision=revision, to_revision=2)
        assert exc.value.code == "NOT_FOUND"
    with pytest.raises(DomainError) as exc:
        domain.list_entry_versions(entry_id, actor=Actor(owner.owner_id, "none", "user", frozenset()))
    assert exc.value.code == "FORBIDDEN"


def test_current_delete_blocks_old_history(domain, owner, writer, source, versions):
    commit(domain, owner, writer, source, versions[1], operation="delete")
    for operation in [lambda: domain.list_entry_versions("history-example", actor=owner),
                      lambda: domain.diff_entry_versions("history-example", actor=owner, from_revision=1, to_revision=2)]:
        with pytest.raises(DomainError) as exc:
            operation()
        assert exc.value.code == "NOT_FOUND"


def edit_history(domain, target_revision, **patch):
    with domain.store.transaction() as connection:
        row = connection.execute("SELECT metadata FROM kp_entry_revisions WHERE entry_id=? AND revision=?", ("history-example", target_revision)).fetchone()
        metadata = json.loads(row["metadata"])
        metadata.update(patch)
        connection.execute("UPDATE kp_entry_revisions SET metadata=? WHERE entry_id=? AND revision=?", (canonical(metadata), "history-example", target_revision))


@pytest.mark.parametrize("patch", [{"path": "entries/other/private.md"}, {"entry_id": "other"}, {"title": "Forged title"}, {"content_hash": "bad"}, {"revision": 88}])
def test_catalog_tampering_fails_closed(domain, owner, versions, patch):
    edit_history(domain, 1, **patch)
    with pytest.raises(DomainError) as exc:
        domain.diff_entry_versions("history-example", actor=owner, from_revision=1, to_revision=2)
    assert exc.value.code == "CONTENT_CORRUPT"


def test_hidden_revision_never_exposes_title_or_count(domain, owner, generator, versions):
    edit_history(domain, 1, data_classification="secret", classification_labels=["secret"], title="Hidden secret title")
    page = domain.list_entry_versions("history-example", actor=generator)
    assert [item["revision"] for item in page["items"]] == [2]
    assert page["has_more"] is False
    assert "Hidden secret title" not in json.dumps(page)
    with pytest.raises(DomainError) as exc:
        domain.diff_entry_versions("history-example", actor=generator, from_revision=1, to_revision=2)
    assert exc.value.code == "NOT_FOUND"


def test_current_private_classification_blocks_history(domain, owner, versions):
    with domain.store.transaction() as connection:
        current = domain.store.get(connection, "entry", "history-example", owner.owner_id)
        current.update(data_classification="personal", classification_labels=["personal"], visibility="private")
        domain.store.put(connection, "entry", "history-example", current)
    public = Actor(owner.owner_id, "public-reader", "service", frozenset({"knowledge:read"}), frozenset({"public"}))
    with pytest.raises(DomainError) as exc:
        domain.list_entry_versions("history-example", actor=public)
    assert exc.value.code == "NOT_FOUND"


def test_publication_withdrawal_not_undone(domain, owner, writer, source, versions):
    commit(domain, owner, writer, source, versions[1], visibility="private", publication_intent="private")
    page = domain.list_entry_versions("history-example", actor=owner)
    delta = domain.diff_entry_versions("history-example", actor=owner, from_revision=1, to_revision=2)
    assert "publication_eligible" not in json.dumps(page)
    assert "publication_eligible" not in json.dumps(delta)
    assert domain.get_entry("history-example", actor=owner)["publication_intent"] == "private"


def test_read_dispatch_preserves_subclass_seal(domain, owner, versions, monkeypatch):
    def denied(*args, **kwargs):
        raise DomainError("ACCEPTANCE_SCOPE_DENIED", "Synthetic seal denied", status_code=403)
    monkeypatch.setattr(domain, "get_entry", denied)
    with pytest.raises(DomainError) as exc:
        domain.list_entry_versions("history-example", actor=owner)
    assert exc.value.code == "ACCEPTANCE_SCOPE_DENIED"


def test_http_contract(domain, owner, versions):
    token = "synthetic-history-http-token-24-chars"
    app = Starlette(routes=create_core_routes(domain), middleware=[Middleware(DomainAuthenticationMiddleware, authenticator=TokenAuthenticator({token: owner}))])
    with TestClient(app) as client:
        root = "/api/knowledge/v1/entries/history-example"
        headers = {"Authorization": "Bearer " + token}
        assert client.get(root + "/versions").status_code == 401
        result = client.get(root + "/versions?limit=1", headers=headers)
        assert result.status_code == 200
        assert result.json()["data"]["items"][0]["revision"] == 2
        delta = client.get(root + "/diff?from_revision=1&to_revision=2", headers=headers)
        assert delta.status_code == 200
        assert delta.json()["data"]["to_revision"] == 2
        for query in ["", "?from_revision=1", "?from_revision=abc&to_revision=2", "?from_revision=1&to_revision=2&path=private"]:
            assert client.get(root + "/diff" + query, headers=headers).status_code == 422


def test_scan_budget_is_bounded_even_if_all_history_hidden(domain, owner, generator, versions):
    with domain.store.transaction() as connection:
        row = connection.execute("SELECT * FROM kp_entry_revisions WHERE entry_id=? AND revision=1", ("history-example",)).fetchone()
        metadata = json.loads(row["metadata"])
        metadata.update(data_classification="secret", classification_labels=["secret"])
        for revision in range(3, 1004):
            connection.execute("INSERT INTO kp_entry_revisions VALUES(?,?,?,?,?,?,?)", ("history-example", revision, owner.owner_id, row["commit_sha"], row["path"], row["content_hash"], canonical(metadata)))
    with pytest.raises(DomainError) as exc:
        domain.list_entry_versions("history-example", actor=generator)
    assert exc.value.code == "HISTORY_LIMIT_EXCEEDED"


@pytest.mark.parametrize("body", ["字" * 45000, "x\n" * 4001], ids=["bytes", "lines"])
def test_diff_size_limits(domain, owner, writer, source, body):
    first = commit(domain, owner, writer, source, body=body)
    with pytest.raises(DomainError) as exc:
        domain.diff_entry_versions(first["entry_id"], actor=owner, from_revision=1, to_revision=1)
    assert exc.value.code == "DIFF_LIMIT_EXCEEDED"


def test_unterminated_lines_are_not_concatenated(domain, owner, writer, source):
    first = commit(domain, owner, writer, source, body="old")
    commit(domain, owner, writer, source, first, body="new")
    delta = domain.diff_entry_versions("history-example", actor=owner, from_revision=1, to_revision=2)["unified_diff"]
    assert "-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n" in delta


def test_formal_git_hash_tamper_fails_closed(domain, owner, versions):
    first, second = versions
    journal = domain.writer.prepare({"owner_id": owner.owner_id, "entry_id": first["entry_id"], "change_id": "synthetic-tamper", "base_git_revision": domain.writer.head(), "patch_hash": "synthetic"}, {**first, "body": "tampered"})
    edit_history(domain, 1, commit_sha=journal["commit_sha"])
    with pytest.raises(DomainError) as exc:
        domain.diff_entry_versions(first["entry_id"], actor=owner, from_revision=1, to_revision=2)
    assert exc.value.code == "CONTENT_CORRUPT"


def test_readonly_domain_does_not_need_git_write_capabilities(domain, owner, versions):
    from ahadiff.knowledge_platform.domain import DomainService
    readonly = DomainService.open_readonly(domain.store.path, domain.writer.repository)
    try:
        assert len(readonly.list_entry_versions("history-example", actor=owner)["items"]) == 2
        assert readonly.diff_entry_versions("history-example", actor=owner, from_revision=1, to_revision=2)["to_revision"] == 2
    finally:
        readonly.close()
