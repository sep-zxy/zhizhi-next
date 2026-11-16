"""Real SQLite/Git and independent-review service fixture; no real model or public deployment."""

from __future__ import annotations

import copy

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.publish import ProjectionError
from ahadiff.knowledge_platform.publish.projection import build_projection, project_git_revision
from ahadiff.knowledge_platform.publish.revalidate import (
    bind_domain_publication,
    revalidate_public_projection,
)
from ahadiff.knowledge_platform.writer import GitWriter


@pytest.fixture
def reviewed_publication(tmp_path):
    from tests.knowledge_platform.agent.test_domain_publication_integration import (
        SOURCE_TEXT,
        build_reviewed_derivative,
    )

    database = tmp_path / "trusted" / "domain.db"
    domain = DomainService(database)
    owner = Actor("publication-owner", "human", "user", frozenset({"*"}))
    generator = Actor(owner.owner_id, "generator", "agent", frozenset({"*"}))
    reviewer = Actor(owner.owner_id, "reviewer", "agent", frozenset({"*"}))
    writer = Actor(owner.owner_id, "writer", "service", frozenset({"*"}))
    entry = build_reviewed_derivative(
        domain, owner=owner, generator=generator, reviewer=reviewer, writer=writer
    )
    with domain.store.reading() as connection:
        document = domain.store.get(
            connection,
            "external_document",
            entry["evidence_refs"][0]["external_document_id"],
            owner.owner_id,
        )
    policy = {
        "sources": {},
        "assets": {},
        "forbidden_fingerprints": [
            "PRIVATE CHAT NOTE",
            SOURCE_TEXT,
            document["origin_source_id"],
            document["document_id"],
        ],
    }
    yield domain, owner, reviewer, writer, entry, database, policy
    domain.close()


def test_fixed_git_projection_revalidates_in_readonly_ledger_without_raw_export(
    reviewed_publication, tmp_path, monkeypatch
):
    domain, owner, _, _, entry, database, policy = reviewed_publication
    repository = domain.writer.repository

    def never_initialize(*_args, **_kwargs):
        raise AssertionError("Publication must not initialize/migrate/recover a ledger")

    monkeypatch.setattr(DomainService, "__init__", never_initialize)
    readonly, resolver = bind_domain_publication(database, owner.owner_id, repository)
    try:
        before = readonly.store.connection.total_changes
        result = project_git_revision(
            repository,
            entry["commit_sha"],
            tmp_path / "public",
            policy=policy,
            publication_resolver=resolver,
        )
        manifest = result["public_manifest"]
        assert manifest["counts"]["content_pages"] == 1
        assert manifest["pages"][0]["slug"] == "knowledge/" + entry["entry_id"]
        assert manifest["pages"][0]["publication_binding"]["source_manifest_hash"]
        assert manifest["pages"][0]["publication_binding"]["input_manifest_hash"]
        revalidate_public_projection(
            manifest, repository=repository, policy=policy, publication_resolver=resolver
        )
        assert readonly.store.connection.total_changes == before == 0
        for file in (tmp_path / "public").rglob("*"):
            if file.is_file():
                text = file.read_text(encoding="utf-8")
                assert all(value not in text for value in policy["forbidden_fingerprints"])
        assert (
            domain.get_source(policy["forbidden_fingerprints"][2], actor=owner)["rights"]
            == "unknown"
        )
    finally:
        readonly.close()


@pytest.mark.parametrize("field", ["body", "certificate", "public_artifact"])
def test_forged_or_stale_artifact_is_rejected_by_real_certificate_ledger(
    reviewed_publication, tmp_path, field
):
    domain, owner, _, _, entry, database, policy = reviewed_publication
    canonical = domain.writer.read_entry(entry["commit_sha"], entry["path"])
    forged = copy.deepcopy(canonical)
    if field == "body":
        forged["body"] += "未经复核的追加结论。"
    elif field == "certificate":
        forged["publication_certificate"]["input_manifest_hash"] = "0" * 64
    else:
        forged["public_artifact"]["body"] += "未经复核的追加结论。"
    readonly, resolver = bind_domain_publication(database, owner.owner_id, domain.writer.repository)
    try:
        result = build_projection(
            {entry["path"]: GitWriter.document(forged)},
            tmp_path / field,
            revision=entry["commit_sha"],
            policy=policy,
            policy_version="1",
            template_version="1",
            publication_resolver=resolver,
        )
        assert result["public_manifest"]["pages"] == []
        assert result["internal_manifest"]["excluded"]
    finally:
        readonly.close()


def test_current_private_decision_invalidates_previously_public_git_revision(
    reviewed_publication, tmp_path
):
    domain, owner, reviewer, writer, entry, database, policy = reviewed_publication
    readonly, resolver = bind_domain_publication(database, owner.owner_id, domain.writer.repository)
    try:
        manifest = project_git_revision(
            domain.writer.repository,
            entry["commit_sha"],
            tmp_path / "before",
            policy=policy,
            publication_resolver=resolver,
        )["public_manifest"]
    finally:
        readonly.close()
    change = domain.propose_change(
        {
            "entry_id": entry["entry_id"],
            "base_revision": entry["revision"],
            "title": entry["title"],
            "body": entry["body"],
            "entry_type": entry["entry_type"],
            "metadata": entry["metadata"],
            "evidence_refs": [{"source_id": policy["forbidden_fingerprints"][2]}],
            "visibility": "private",
        },
        actor=owner,
        idempotency_key="revoke-publication",
    )
    review = {"decision": "approve", "expected_patch_hash": change["patch_hash"]}
    actor = reviewer
    if change["state"] == "needs_user":
        actor = owner
        challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
        review.update(
            decision_nonce=challenge["decision_nonce"], base_revision=change["base_revision"]
        )
    domain.record_review(change["change_id"], review, actor=actor, idempotency_key="revoke-review")
    domain.apply_change(change["change_id"], actor=writer, idempotency_key="revoke-apply")
    readonly, resolver = bind_domain_publication(database, owner.owner_id, domain.writer.repository)
    try:
        with pytest.raises(ProjectionError, match="changed"):
            revalidate_public_projection(
                manifest,
                repository=domain.writer.repository,
                policy=policy,
                publication_resolver=resolver,
            )
    finally:
        readonly.close()


def test_missing_publication_ledger_is_not_created(tmp_path):
    database = tmp_path / "must-not-create.db"
    with pytest.raises(ProjectionError, match="existing"):
        bind_domain_publication(database, "owner", tmp_path / "vault.git")
    assert not database.exists()


def test_changed_trusted_domain_policy_rejects_old_certificate(reviewed_publication, tmp_path):
    domain, owner, _, _, entry, database, policy = reviewed_publication
    readonly, resolver = bind_domain_publication(
        database, owner.owner_id, domain.writer.repository, domain_policy_version="2"
    )
    try:
        result = project_git_revision(
            domain.writer.repository,
            entry["commit_sha"],
            tmp_path / "new-policy",
            policy=policy,
            publication_resolver=resolver,
        )
        assert result["public_manifest"]["pages"] == []
        assert result["internal_manifest"]["excluded"]
    finally:
        readonly.close()


def test_changed_source_object_invalidates_complete_projection(reviewed_publication, tmp_path):
    domain, owner, _, _, entry, database, policy = reviewed_publication
    readonly, resolver = bind_domain_publication(database, owner.owner_id, domain.writer.repository)
    try:
        manifest = project_git_revision(
            domain.writer.repository,
            entry["commit_sha"],
            tmp_path / "before",
            policy=policy,
            publication_resolver=resolver,
        )["public_manifest"]
        with domain.store.reading() as connection:
            source = domain.store.get(
                connection, "source", policy["forbidden_fingerprints"][2], owner.owner_id
            )
        # Corrupt only this synthetic fixture's RAW object, not a real source or ledger proof.
        (domain.object_path / source["object_path"]).write_bytes(b"changed synthetic source")
        with pytest.raises(ProjectionError, match="changed"):
            revalidate_public_projection(
                manifest,
                repository=domain.writer.repository,
                policy=policy,
                publication_resolver=resolver,
            )
    finally:
        readonly.close()


def test_wrong_owner_cannot_export_certified_git_revision(reviewed_publication, tmp_path):
    domain, _, _, _, entry, database, policy = reviewed_publication
    readonly, resolver = bind_domain_publication(
        database, "different-owner", domain.writer.repository
    )
    try:
        result = project_git_revision(
            domain.writer.repository,
            entry["commit_sha"],
            tmp_path / "wrong-owner",
            policy=policy,
            publication_resolver=resolver,
        )
        assert not result["public_manifest"]["pages"]
    finally:
        readonly.close()
