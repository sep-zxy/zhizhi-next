"""Real Git/SQLite relation rebuilds, not model-inferred or persistent graph facts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.contracts import fingerprint
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.relations import RelationService


def commit(
    domain,
    owner,
    reviewer,
    writer,
    source,
    key,
    *,
    metadata=None,
    body=None,
    covered=False,
    visibility="public",
    previous=None,
):
    payload = {
        "entry_id": key,
        "title": key,
        "body": body or "Evidence says addition preserves history.",
        "metadata": metadata or {},
        "visibility": visibility,
        "evidence_refs": [{"source_id": source["source_id"]}],
        "risk_flags": ["requires_ai_review"],
    }
    if previous:
        payload["base_revision"] = previous["revision"]
    change = domain.propose_change(
        payload,
        actor=owner,
        idempotency_key="propose:" + key + str(payload.get("base_revision", 0)),
    )
    review = {"decision": "approve", "expected_patch_hash": change["patch_hash"]}
    actor = reviewer
    if change["state"] == "needs_user":
        actor = owner
        challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
        review.update(
            decision_nonce=challenge["decision_nonce"], base_revision=change["base_revision"]
        )
    coverage = {}
    if covered:
        coverage = {
            "reviewed_fields": ("body", "metadata"),
            "reviewed_relations_hash": fingerprint(payload["metadata"]["relations"]),
        }
    domain.record_review(
        change["change_id"],
        review,
        actor=actor,
        idempotency_key="review:" + change["change_id"],
        **coverage,
    )
    return domain.apply_change(
        change["change_id"], actor=writer, idempotency_key="apply:" + change["change_id"]
    )


def relation(source, target="target", **overrides):
    return {
        "kind": "confirmed",
        "target_entry_id": target,
        "target_revision": 1,
        "relation_type": "supports",
        "statement": "This statement was explicitly reviewed against its cited evidence.",
        "evidence_refs": [
            {
                "source_id": source["source_id"],
                "content_hash": source["content_hash"],
                "locator": {"quote": "addition preserves history"},
            }
        ],
        **overrides,
    }


def test_navigation_resolves_ids_unique_aliases_and_excludes_code_media_escape(
    domain, owner, reviewer, writer, source
):
    commit(
        domain, owner, reviewer, writer, source, "target", metadata={"aliases": ["Unique alias"]}
    )
    commit(domain, owner, reviewer, writer, source, "only-code")
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        body=(
            "[[Unique alias|read]] [[target#heading]] `[[only-code]]` ![[only-code]] "
            "\\[[only-code]] [[https://example.com]]\n\n```md\n[[only-code]]\n```\n"
        ),
    )
    view = RelationService(domain).neighbors("origin", actor=owner)
    assert len(view["edges"]) == 1
    assert view["edges"][0]["target_entry_id"] == "target"
    assert view["edges"][0]["kind"] == "navigation"
    assert view["edges"][0]["is_fact"] is False


@pytest.mark.parametrize("covered,kind", [(False, "candidate"), (True, "confirmed")])
def test_generic_approval_never_confirms_relation_but_exact_coverage_does(
    domain, owner, reviewer, writer, source, covered, kind
):
    commit(domain, owner, reviewer, writer, source, "target")
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        covered=covered,
        metadata={"relations": [relation(source)]},
    )
    graph = RelationService(domain).rebuild(actor=owner)
    edge = graph["edges"][0]
    assert edge["kind"] == kind
    assert edge["is_fact"] is covered
    if covered:
        assert edge["review_provenance"]["reviewed_relations_hash"] == fingerprint(
            [relation(source)]
        )
    assert graph["inference"] == "none"


@pytest.mark.parametrize(
    "quote,hash_present", [("fabricated quote", True), ("addition preserves history", False)]
)
def test_coverage_cannot_replace_exact_quote_and_hash(
    domain, owner, reviewer, writer, source, quote, hash_present
):
    commit(domain, owner, reviewer, writer, source, "target")
    declaration = relation(source)
    declaration["evidence_refs"][0]["locator"]["quote"] = quote
    if not hash_present:
        del declaration["evidence_refs"][0]["content_hash"]
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        covered=True,
        metadata={"relations": [declaration]},
    )
    assert RelationService(domain).rebuild(actor=owner)["edges"][0]["kind"] == "candidate"


def test_candidate_never_promoted_and_public_view_never_returns_raw_quote(
    domain, owner, reviewer, writer, source
):
    commit(domain, owner, reviewer, writer, source, "target")
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        covered=True,
        metadata={"relations": [relation(source, kind="candidate")]},
    )
    view = RelationService(domain).rebuild(actor=owner, visibility="public")
    assert view["edges"][0]["kind"] == "candidate"
    assert "locator" not in view["edges"][0]["evidence_refs"][0]
    assert "object_path" not in str(view)


def test_review_coverage_cannot_be_forged_in_payload_or_use_wrong_hash(
    domain, owner, reviewer, source
):
    change = domain.propose_change(
        {
            "title": "Relation",
            "body": "Evidence says addition preserves history.",
            "evidence_refs": [{"source_id": source["source_id"]}],
            "risk_flags": ["requires_ai_review"],
            "metadata": {"relations": [relation(source)]},
        },
        actor=owner,
        idempotency_key="propose",
    )
    payload = {"decision": "approve", "expected_patch_hash": change["patch_hash"]}
    for field in ("reviewed_fields", "reviewed_relations_hash"):
        with pytest.raises(DomainError) as error:
            domain.record_review(
                change["change_id"],
                {**payload, field: "forged"},
                actor=reviewer,
                idempotency_key="forged:" + field,
            )
        assert error.value.code == "FORBIDDEN"
    with pytest.raises(DomainError):
        domain.record_review(
            change["change_id"],
            payload,
            actor=reviewer,
            idempotency_key="wrong",
            reviewed_fields=("metadata",),
            reviewed_relations_hash="wrong",
        )
    assert domain.get_change(change["change_id"], actor=owner)["state"] == "needs_ai_review"


def test_visibility_classification_owner_and_ambiguous_aliases_do_not_leak_endpoints(
    domain, owner, reviewer, writer, source
):
    commit(domain, owner, reviewer, writer, source, "private", visibility="private")
    elevated = frozenset({"public", "personal", "employer"})
    commit(
        domain,
        owner,
        replace(reviewer, allowed_classifications=elevated),
        replace(writer, allowed_classifications=elevated),
        source,
        "restricted",
        visibility="private",
        metadata={"data_classification": "employer"},
    )
    for key in ("one", "two"):
        commit(domain, owner, reviewer, writer, source, key, metadata={"aliases": ["ambiguous"]})
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        body="[[private]] [[restricted]] [[ambiguous]] [[absent]]",
    )
    service = RelationService(domain)
    assert len(service.neighbors("origin", actor=owner)["edges"]) == 2
    machine = service.neighbors("origin", actor=reviewer)
    assert [edge["target_entry_id"] for edge in machine["edges"]] == ["private"]
    assert service.neighbors("origin", actor=owner, visibility="public")["edges"] == []
    stranger = Actor("another-owner", "another-user", "user", frozenset({"knowledge:read"}))
    assert service.rebuild(actor=stranger)["nodes"] == []
    with pytest.raises(DomainError) as error:
        service.neighbors("restricted", actor=reviewer)
    assert error.value.code == "NOT_FOUND"


def test_rebuild_is_deterministic_disposable_and_never_reuses_old_confirmation(
    domain, owner, reviewer, writer, source
):
    commit(domain, owner, reviewer, writer, source, "target")
    metadata = {"relations": [relation(source)]}
    original = commit(
        domain, owner, reviewer, writer, source, "origin", metadata=metadata, covered=True
    )
    first = RelationService(domain).rebuild(actor=owner)
    assert first == RelationService(domain).rebuild(actor=owner)
    changed = {
        "relations": [
            relation(source, statement="A newly changed relation has no specific review.")
        ]
    }
    commit(domain, owner, reviewer, writer, source, "origin", metadata=changed, previous=original)
    next_view = RelationService(domain).rebuild(actor=owner)
    assert next_view["projection_revision"] != first["projection_revision"]
    assert next_view["edges"][0]["kind"] == "candidate"


def test_bounded_traversal_handles_cycles_without_inventing_transitive_facts(
    domain, owner, reviewer, writer, source
):
    for key, next_key in (("a", "b"), ("b", "c"), ("c", "a")):
        commit(domain, owner, reviewer, writer, source, key, body=f"[[{next_key}]]")
    service = RelationService(domain)
    view = service.traverse("a", actor=owner, direction="out", max_depth=2)
    assert view["distances"] == {"a": 0, "b": 1, "c": 2}
    assert len(view["edges"]) == 2
    assert all(not edge["is_fact"] for edge in view["edges"])
    limited = service.traverse("a", actor=owner, max_nodes=1)
    assert limited["truncated"] is True
    assert len(limited["nodes"]) == 1
    assert service.neighbors("a", actor=owner, kinds=[])["edges"] == []


def test_concurrent_formal_revision_change_fails_instead_of_mixing_snapshot(
    domain, owner, monkeypatch
):
    heads = iter(["old", "new"])
    monkeypatch.setattr(domain.writer, "head", lambda: next(heads))
    with pytest.raises(DomainError) as error:
        RelationService(domain).rebuild(actor=owner)
    assert error.value.code == "PROJECTION_STALE"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_depth": 0},
        {"max_depth": 4},
        {"max_nodes": 201},
        {"kinds": [{}]},
        {"visibility": "private"},
        {"direction": "sideways"},
    ],
)
def test_invalid_traversal_options_fail_explicitly(domain, owner, kwargs):
    with pytest.raises(DomainError):
        RelationService(domain).traverse("absent", actor=owner, **kwargs)


def test_malformed_relation_fields_do_not_crash_or_become_confirmed(
    domain, owner, reviewer, writer, source
):
    commit(domain, owner, reviewer, writer, source, "target")
    declarations = [
        relation(source, kind=[]),
        relation(source, target_entry_id={}),
        relation(source, evidence_refs=["invalid"]),
        "invalid",
    ]
    commit(domain, owner, reviewer, writer, source, "origin", metadata={"relations": declarations})
    graph = RelationService(domain).rebuild(actor=owner)
    assert graph["edges"] == []
    assert graph["diagnostics"]["ignored_declarations"] == 4


def test_deleted_endpoint_disappears_on_rebuild_and_reads_do_not_write(
    domain, owner, reviewer, writer, source
):
    target = commit(domain, owner, reviewer, writer, source, "target")
    commit(domain, owner, reviewer, writer, source, "origin", body="[[target]]")
    service = RelationService(domain)
    before_head = domain.writer.head()
    before_events = domain.list_events(actor=owner)
    assert len(service.neighbors("origin", actor=owner)["edges"]) == 1
    assert domain.writer.head() == before_head
    assert domain.list_events(actor=owner) == before_events
    deletion = domain.propose_change(
        {
            "entry_id": "target",
            "base_revision": target["revision"],
            "operation": "delete",
            "title": "target",
            "body": target["body"],
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=owner,
        idempotency_key="delete",
    )
    challenge = domain.create_decision_challenge(deletion["change_id"], actor=owner)
    domain.record_review(
        deletion["change_id"],
        {
            "decision": "approve",
            "expected_patch_hash": deletion["patch_hash"],
            "base_revision": target["revision"],
            "decision_nonce": challenge["decision_nonce"],
        },
        actor=owner,
        idempotency_key="approve-delete",
    )
    domain.apply_change(deletion["change_id"], actor=writer, idempotency_key="apply-delete")
    view = service.neighbors("origin", actor=owner)
    assert view["edges"] == []
    assert [node["entry_id"] for node in view["nodes"]] == ["origin"]


def test_http_relations_require_auth_and_bound_read_only_queries(
    domain, owner, reviewer, writer, source
):
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.testclient import TestClient

    from ahadiff.knowledge_platform.auth import DomainAuthenticationMiddleware, TokenAuthenticator
    from ahadiff.knowledge_platform.core_routes import create_core_routes
    from ahadiff.knowledge_platform.relations import create_relation_routes

    commit(domain, owner, reviewer, writer, source, "target")
    commit(domain, owner, reviewer, writer, source, "origin", body="[[target]]")
    token = "temporary-isolated-relations-fixture-token"
    app = Starlette(
        routes=create_relation_routes(RelationService(domain)) + create_core_routes(domain),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware, authenticator=TokenAuthenticator({token: reviewer})
            )
        ],
    )
    headers = {"Authorization": "Bearer " + token}
    with TestClient(app) as client:
        path = "/api/knowledge/v1/relations/origin"
        assert client.get(path).status_code == 401
        assert client.post(path, headers=headers).status_code == 405
        response = client.get(path + "?kind=navigation&visibility=public&limit=1", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["edges"][0]["is_fact"] is False
        assert response.json()["data_classification"] == "public"
        assert client.get(path + "/traverse?max_depth=4", headers=headers).status_code == 422
        assert client.get(path + "/traverse?max_nodes=no", headers=headers).status_code == 422
        assert client.get(path + "/traverse?max_depth=2", headers=headers).json()["data"][
            "distances"
        ] == {"origin": 0, "target": 1}
        change = domain.propose_change(
            {
                "title": "forgery",
                "body": "Evidence says addition preserves history.",
                "evidence_refs": [{"source_id": source["source_id"]}],
                "risk_flags": ["requires_ai_review"],
            },
            actor=owner,
            idempotency_key="forgery",
        )
        response = client.post(
            "/api/knowledge/v1/changes/" + change["change_id"] + "/reviews",
            headers={**headers, "Idempotency-Key": "fake-coverage"},
            json={
                "decision": "approve",
                "expected_patch_hash": change["patch_hash"],
                "reviewed_relations_hash": "forged",
            },
        )
        assert response.status_code == 403


def test_old_relation_confirmation_cannot_bind_changed_target_revision(
    domain, owner, reviewer, writer, source
):
    target = commit(domain, owner, reviewer, writer, source, "target")
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        covered=True,
        metadata={"relations": [relation(source)]},
    )
    service = RelationService(domain)
    assert service.rebuild(actor=owner)["edges"][0]["kind"] == "confirmed"
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "target",
        previous=target,
        metadata={"revision_note": "The target meaning was updated."},
    )
    edge = service.rebuild(actor=owner)["edges"][0]
    assert edge["kind"] == "candidate"
    assert edge["is_fact"] is False
    assert edge["target_revision"] == 2


def test_missing_target_revision_never_confirms_legacy_declaration(
    domain, owner, reviewer, writer, source
):
    commit(domain, owner, reviewer, writer, source, "target")
    declaration = relation(source)
    del declaration["target_revision"]
    commit(
        domain,
        owner,
        reviewer,
        writer,
        source,
        "origin",
        covered=True,
        metadata={"relations": [declaration]},
    )
    assert RelationService(domain).rebuild(actor=owner)["edges"][0]["kind"] == "candidate"
