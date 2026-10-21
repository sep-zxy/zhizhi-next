"""Real isolated ledger/Git original-summary certificates, not live legal judgments."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from ahadiff.knowledge_platform.contracts import fingerprint, now, text_hash
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.publication import PUBLICATION_CHECKS

TEXT = (
    "A write-ahead journal records pending modifications before a durable commit becomes visible."
)
BODY = "Recording intended updates before publishing them makes interrupted writes recoverable."


def clean_source(
    domain, owner, *, key="clean", classification="personal", manifest=True, private_intent=False
):
    intake = domain.create_intake(
        {
            "text": "PRIVATE CHAT COMMENT",
            "data_classification": classification,
            "metadata": {"publication_intent": "private"} if private_intent else {},
        },
        actor=owner,
        idempotency_key=key + ":intake",
    )
    preview = domain.update_intake(
        intake["intake_id"],
        {"state": "preview_ready", "preview": {"title": "Journal"}},
        actor=owner,
        idempotency_key=key + ":preview",
    )
    acquisition = {
        "body_text": TEXT,
        "content_hash": text_hash(TEXT),
        "canonical_url": "https://example.com/articles/journals",
        "final_url": "https://example.com/articles/journals",
        "captured_at": now(),
        "fetcher_version": "anonymous-safe-fetch-v1",
        "authenticated": False,
        "publicly_accessible": True,
        "context_separated": True,
        "content_kind": "external_article",
        "privacy_assessment": "not_assessed",
        "observed_restrictions": [],
    }
    return domain.retain_source(
        intake["intake_id"],
        {
            "raw_text": json.dumps(
                {"document": {"body_text": TEXT}, "origin": {"text": "PRIVATE CHAT COMMENT"}}
            ),
            "preview_revision": preview["preview_revision"],
            "rights": "unknown",
            "content_identity": "same-external-body",
            "metadata": {
                "extraction": {"body_text": TEXT, "source_url": acquisition["canonical_url"]}
            },
        },
        actor=owner,
        idempotency_key=key + ":keep",
        external_document_manifest=acquisition if manifest else None,
    )


def candidate(domain, owner, generator, writer):
    source = clean_source(domain, owner)
    document = domain.read_external_document(source["source_id"], actor=generator)
    task = domain.claim_task("publication-fixture", actor=writer, kinds=["knowledge.generate"])
    manifest = {
        "artifact_kind": "original_factual_synthesis",
        "documents": [
            {key: document[key] for key in ("document_id", "content_hash", "acquisition_hash")}
        ],
        "public_catalogue": [],
        "model_input_hash": fingerprint({"text": TEXT}),
    }
    change = domain.propose_change(
        {
            "title": "Recoverable journal",
            "body": BODY,
            "visibility": "public",
            "evidence_refs": [document["evidence_ref"]],
        },
        actor=generator,
        idempotency_key="clean-proposal",
        workflow_task_id=task["task_id"],
        derivative_input_manifest=manifest,
    )
    return source, change, task


def approve(domain, reviewer, change, task, *, certificate=True, override=None):
    context = domain.publication_review_context(change["change_id"], actor=reviewer)
    proof = {
        key: context[key]
        for key in (
            "policy_id",
            "policy_version",
            "candidate_hash",
            "source_manifest_hash",
            "input_manifest_hash",
        )
    }
    proof.update(
        review_task_id=task["task_id"],
        checks={
            name: {"passed": True, "reason": "Isolated fixture reviewed this precise artifact."}
            for name in PUBLICATION_CHECKS
        },
    )
    if override:
        override(proof)
    return domain.record_review(
        change["change_id"],
        {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
        actor=reviewer,
        idempotency_key="review-clean",
        publication_review=proof if certificate else None,
    )


def test_unknown_raw_rights_can_have_independently_reviewed_original_artifact(
    domain,
    owner,
    generator,
    reviewer,
    writer,
):
    source, change, task = candidate(domain, owner, generator, writer)
    assert change["publication_eligible"] is False
    approve(domain, reviewer, change, task)
    entry = domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply-clean")
    assert entry["body"] == BODY
    assert entry["publication_eligible"] is True
    assert domain.get_change(change["change_id"], actor=owner)["patch_hash"] == change["patch_hash"]
    assert domain.get_change(change["change_id"], actor=owner)["publication_eligible"] is False
    retained = domain.get_source(source["source_id"], actor=owner)
    assert retained["rights"] == "unknown" and retained["visibility"] == "private"
    artifact = domain.verify_publication_entry(entry, actor=writer)
    assert "PRIVATE CHAT COMMENT" not in json.dumps(artifact)
    assert artifact["body"] == BODY and artifact["citations"][0]["url"].startswith("https://")
    readonly = DomainService.open_readonly(domain.store.path)
    try:
        assert readonly.verify_publication_entry(entry, actor=writer) == artifact
    finally:
        readonly.close()


def test_generic_approval_never_creates_publication_certificate(
    domain, owner, generator, reviewer, writer
):
    _, change, task = candidate(domain, owner, generator, writer)
    approve(domain, reviewer, change, task, certificate=False)
    entry = domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply-clean")
    assert entry["publication_eligible"] is False
    assert "publication_certificate" not in entry
    with pytest.raises(DomainError):
        domain.verify_publication_entry(entry, actor=writer)


def test_ordinary_proposal_has_no_derivative_review_context(domain, owner, proposal):
    assert domain.publication_review_context(proposal["change_id"], actor=owner) is None


@pytest.mark.parametrize("classification", ["employer", "third_party_private", "secret", "unknown"])
def test_content_dedupe_cannot_cross_private_envelope_classes(domain, owner, classification):
    clean = clean_source(domain, owner)
    restricted = clean_source(
        domain, owner, key="restricted", classification=classification, manifest=False
    )
    assert clean["source_id"] != restricted["source_id"]
    assert clean["task_id"] != restricted["task_id"]
    assert "external_document_id" not in restricted


@pytest.mark.parametrize(
    "field",
    [
        "external_document_manifest",
        "publication_review",
        "public_artifact",
        "publication_certificate",
    ],
)
def test_client_cannot_supply_publication_proofs(domain, owner, field):
    with pytest.raises(DomainError, match="not a payload capability"):
        domain.create_intake(
            {"text": "example", "metadata": {field: {}}}, actor=owner, idempotency_key=field
        )


def published(domain, owner, generator, reviewer, writer):
    source, change, task = candidate(domain, owner, generator, writer)
    approve(domain, reviewer, change, task)
    entry = domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply-clean")
    return source, change, entry


@pytest.mark.parametrize("part", ["body", "title", "metadata", "citations", "certificate", "owner"])
def test_changed_git_candidate_cannot_borrow_a_certificate(
    domain, owner, generator, reviewer, writer, part
):
    _, _, entry = published(domain, owner, generator, reviewer, writer)
    tampered = copy.deepcopy(entry)
    if part == "body":
        tampered["body"] += " Changed."
    elif part == "title":
        tampered["title"] = "Different title"
    elif part == "metadata":
        tampered["metadata"]["tags"] = ["secret"]
    elif part == "citations":
        tampered["public_artifact"]["citations"] = []
    elif part == "certificate":
        tampered["publication_certificate"]["certificate_id"] = "fake-certificate"
    else:
        tampered["owner_id"] = "another-owner"
    with pytest.raises(DomainError):
        domain.verify_publication_entry(tampered, actor=writer)


def test_actual_git_blob_and_readonly_connection_need_no_initialization(
    domain, owner, generator, reviewer, writer, monkeypatch
):
    _, _, entry = published(domain, owner, generator, reviewer, writer)
    raw_entry = domain.writer.read_entry(entry["commit_sha"], entry["path"])
    assert "owner_id" not in raw_entry
    from ahadiff.knowledge_platform.writer import GitWriter

    monkeypatch.setattr(
        GitWriter, "initialize", lambda _: pytest.fail("Read-only resolver initialized Git")
    )
    readonly = DomainService.open_readonly(domain.store.path)
    try:
        assert readonly.verify_publication_entry(raw_entry, actor=writer)["body"] == BODY
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            readonly.store.connection.execute("DELETE FROM kp_records")
        with pytest.raises(DomainError, match="read-only"):
            readonly.writer._git("update-ref", "refs/heads/knowledge-main", entry["commit_sha"])
    finally:
        readonly.close()


def test_readonly_missing_database_creates_nothing(tmp_path):
    destination = tmp_path / "missing" / "platform.db"
    with pytest.raises(DomainError):
        DomainService.open_readonly(destination)
    assert not destination.parent.exists()


@pytest.mark.parametrize(
    "drift", ["policy", "source", "input_manifest", "internal_patch", "review"]
)
def test_certificate_fails_closed_after_trusted_input_or_policy_drift(
    domain, owner, generator, reviewer, writer, drift
):
    source, change, entry = published(domain, owner, generator, reviewer, writer)
    if drift == "policy":
        domain.policy_version = "2"
    elif drift == "source":
        (domain.object_path / source["object_path"]).write_text("changed RAW", encoding="utf-8")
    else:
        with domain.store.transaction() as connection:
            if drift == "review":
                record = domain.store.get(
                    connection,
                    "review",
                    entry["publication_certificate"]["review_id"],
                    owner.owner_id,
                )
                record["decision"] = "reject"
                domain.store.put(connection, "review", record["review_id"], record)
            else:
                record = domain.store.get(connection, "change", change["change_id"], owner.owner_id)
                if drift == "input_manifest":
                    record["derivative_input_manifest"]["model_input_hash"] = "0" * 64
                else:
                    record["metadata"]["internal_note"] = "An unreviewed internal patch"
                domain.store.put(connection, "change", change["change_id"], record)
    with pytest.raises(DomainError):
        domain.verify_publication_entry(entry, actor=writer)


def test_explicit_private_withdrawal_blocks_old_git_revision(
    domain, owner, generator, reviewer, writer
):
    _, _, old = published(domain, owner, generator, reviewer, writer)
    revoke = domain.propose_change(
        {
            "entry_id": old["entry_id"],
            "base_revision": old["revision"],
            "title": old["title"],
            "body": old["body"],
            "visibility": "private",
            "publication_intent": "private",
            "evidence_refs": old["evidence_refs"],
        },
        actor=owner,
        idempotency_key="withdraw-publication",
    )
    challenge = domain.create_decision_challenge(revoke["change_id"], actor=owner)
    domain.record_review(
        revoke["change_id"],
        {
            "decision": "approve",
            "expected_patch_hash": revoke["patch_hash"],
            "decision_nonce": challenge["decision_nonce"],
            "base_revision": old["revision"],
        },
        actor=owner,
        idempotency_key="withdraw-approval",
    )
    latest = domain.apply_change(
        revoke["change_id"], actor=writer, idempotency_key="withdraw-apply"
    )
    assert latest["visibility"] == "private"
    with pytest.raises(DomainError, match="revoked"):
        domain.verify_publication_entry(old, actor=writer)


def test_preexisting_authorized_public_path_is_verified_not_metadata_trusted(
    domain, owner, generator, writer, source
):
    change = domain.propose_change(
        {
            "title": "Licensed summary",
            "body": "Evidence says addition preserves history.",
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=generator,
        idempotency_key="licensed",
    )
    entry = domain.apply_change(change["change_id"], actor=writer, idempotency_key="licensed-apply")
    assert domain.verify_publication_entry(entry, actor=writer)["body"] == entry["body"]
    with domain.store.transaction() as connection:
        original = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
        original["rights"] = "unknown"
        domain.store.put(connection, "source", source["source_id"], original)
    with pytest.raises(DomainError):
        domain.verify_publication_entry(entry, actor=writer)


@pytest.mark.parametrize("part", ["body", "title", "aliases"])
def test_verbatim_copy_alarms_cover_all_public_text(domain, owner, generator, writer, part):
    _, change, _ = candidate(domain, owner, generator, writer)
    with domain.store.transaction() as connection:
        candidate_record = domain.store.get(
            connection, "change", change["change_id"], owner.owner_id
        )
        if part == "aliases":
            candidate_record["metadata"]["aliases"] = [TEXT]
        else:
            candidate_record[part] = TEXT
        domain.store.put(connection, "change", change["change_id"], candidate_record)
    with pytest.raises(DomainError, match="overlap"):
        domain.publication_review_context(change["change_id"], actor=owner)


@pytest.mark.parametrize(
    "part",
    [
        "candidate_hash",
        "source_manifest_hash",
        "input_manifest_hash",
        "policy_version",
        "review_task_id",
        "missing_check",
        "false_check",
    ],
)
def test_specialist_proof_requires_every_current_binding_and_check(
    domain, owner, generator, reviewer, writer, part
):
    _, change, task = candidate(domain, owner, generator, writer)

    def corrupt(proof):
        if part == "missing_check":
            proof["checks"].pop("privacy_clear")
        elif part == "false_check":
            proof["checks"]["privacy_clear"]["passed"] = False
        else:
            proof[part] = "wrong-binding"

    with pytest.raises(DomainError):
        approve(domain, reviewer, change, task, override=corrupt)
    assert domain.get_change(change["change_id"], actor=owner)["state"] == "needs_ai_review"


def test_expired_workflow_lease_cannot_issue_certificate(
    domain, owner, generator, reviewer, writer
):
    _, change, task = candidate(domain, owner, generator, writer)
    with domain.store.transaction() as connection:
        record = domain.store.get(connection, "task", task["task_id"], owner.owner_id)
        record["lease_until"] = "2000-01-01T00:00:00+00:00"
        domain.store.put(connection, "task", task["task_id"], record)
    with pytest.raises(DomainError, match="leased"):
        approve(domain, reviewer, change, task)


def test_explicit_private_intake_intent_never_receives_clean_broker_capability(domain, owner):
    public_candidate = clean_source(domain, owner)
    private = clean_source(domain, owner, key="explicit-private", private_intent=True)
    assert private["source_id"] != public_candidate["source_id"]
    assert "external_document_id" not in private
    assert domain.read_external_document(private["source_id"], actor=owner) is None


def test_existing_certified_public_entry_accepts_reviewed_addition_without_changing_old_text(
    domain, owner, generator, reviewer, writer
):
    _, original, old = published(domain, owner, generator, reviewer, writer)
    change = domain.propose_change(
        {
            "entry_id": old["entry_id"],
            "base_revision": old["revision"],
            "title": old["title"],
            "body": old["body"]
            + "\n\nVisibility follows successful completion of the durable log.",
            "visibility": "public",
            "evidence_refs": old["evidence_refs"],
        },
        actor=generator,
        idempotency_key="public-addition",
        derivative_input_manifest=original["derivative_input_manifest"],
    )
    task = domain.claim_task("review-addition", actor=writer, kinds=["knowledge.review"])
    approve(domain, reviewer, change, task)
    updated = domain.apply_change(
        change["change_id"], actor=writer, idempotency_key="apply-addition"
    )
    assert updated["revision"] == 2 and updated["body"].startswith(old["body"])
    assert domain.verify_publication_entry(updated, actor=writer)["body"] == updated["body"]


def test_existing_ineligible_entry_cannot_be_upgraded_by_clean_manifest(
    domain, owner, generator, reviewer, writer
):
    _, change, task = candidate(domain, owner, generator, writer)
    approve(domain, reviewer, change, task, certificate=False)
    old = domain.apply_change(
        change["change_id"], actor=writer, idempotency_key="apply-unqualified"
    )
    with pytest.raises(DomainError, match="cannot be auto-upgraded"):
        domain.propose_change(
            {
                "entry_id": old["entry_id"],
                "base_revision": 1,
                "title": old["title"],
                "body": old["body"] + "\n\nAdditional detail.",
                "visibility": "public",
                "evidence_refs": old["evidence_refs"],
            },
            actor=generator,
            idempotency_key="forbidden-upgrade",
            derivative_input_manifest=change["derivative_input_manifest"],
        )
