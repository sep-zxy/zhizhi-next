"""Data provenance cannot be weakened by presentation labels or agent capability claims."""

from __future__ import annotations

import pytest

from ahadiff.knowledge_platform.auth import (
    Actor,
    classification_labels,
    classify_record,
    enforce_read_classification,
)
from ahadiff.knowledge_platform.errors import DomainError


def classified_source(domain, owner, label):
    intake = domain.create_intake(
        {"text": "private evidence", "data_classification": label},
        actor=owner,
        idempotency_key="intake:" + label,
    )
    domain.update_intake(
        intake["intake_id"],
        {"preview": {"text": "private evidence"}, "state": "preview_ready"},
        actor=owner,
        idempotency_key="preview:" + label,
    )
    return domain.retain_source(
        intake["intake_id"],
        {
            "raw_text": "private evidence",
            "preview_revision": 1,
            "rights": "public",
            "data_classification": "public",
        },
        actor=owner,
        idempotency_key="retain:" + label,
    )


def test_all_nested_labels_must_be_authorized(owner, generator):
    record = {
        "data_classification": "public",
        "items": [{"classification": "employer"}, {"classification": "third_party_private"}],
    }
    assert classify_record(record) == "employer"
    assert classification_labels(record) == {"public", "employer", "third_party_private"}
    assert enforce_read_classification(owner, record) == "employer"
    employer_only = Actor(
        generator.owner_id,
        "restricted",
        "agent",
        generator.scopes,
        frozenset({"public", "personal", "employer"}),
    )
    with pytest.raises(DomainError, match="classification"):
        enforce_read_classification(employer_only, record)


@pytest.mark.parametrize("label", ["secret", "unknown", "invented"])
def test_secret_unknown_cannot_be_enabled_for_machine(owner, label):
    actor = Actor(
        owner.owner_id,
        "machine",
        "service",
        frozenset({"*"}),
        frozenset({"public", "personal", "employer", "secret", "unknown"}),
    )
    with pytest.raises(DomainError) as error:
        enforce_read_classification(actor, {"data_classification": label})
    assert error.value.code == "CLASSIFICATION_FORBIDDEN"


def test_intake_source_task_and_evidence_inherit_provenance(domain, owner, generator, writer):
    source = classified_source(domain, owner, "employer")
    assert source["data_classification"] == "employer"
    for action in (
        lambda: domain.read_intake(source["intake_id"], actor=generator),
        lambda: domain.get_source(source["source_id"], actor=generator),
        lambda: domain.read_task(source["task_id"], actor=generator),
        lambda: domain.read_evidence({"source_id": source["source_id"]}, actor=generator),
    ):
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.code == "CLASSIFICATION_FORBIDDEN"
    assert domain.claim_task("restricted-worker", actor=writer) is None
    authorized = Actor(
        owner.owner_id,
        "authorized",
        "service",
        frozenset({"*"}),
        frozenset({"public", "personal", "employer"}),
    )
    assert domain.claim_task("authorized-worker", actor=authorized)["task_id"] == source["task_id"]
    assert (
        domain.read_evidence({"source_id": source["source_id"]}, actor=authorized)["text"]
        == "private evidence"
    )


def test_change_entry_and_events_cannot_launder_private_evidence(domain, owner, generator):
    source = classified_source(domain, owner, "employer")
    change = domain.propose_change(
        {
            "title": "Owner draft",
            "body": "private evidence",
            "data_classification": "public",
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=owner,
        idempotency_key="private-draft",
    )
    assert change["publication_intent"] == "public"
    assert change["publication_eligible"] is False
    assert change["data_classification"] == "employer"
    entry = domain.apply_change(change["change_id"], actor=owner, idempotency_key="private-apply")
    with pytest.raises(DomainError):
        domain.get_change(change["change_id"], actor=generator)
    with pytest.raises(DomainError):
        domain.get_entry(entry["entry_id"], actor=generator)
    assert domain.list_entries(actor=generator) == []
    assert domain.list_reviews(actor=generator, state=None) == []
    assert domain.list_events(actor=generator) == []


def test_idempotency_replay_does_not_bypass_machine_classification(domain, owner, generator):
    payload = {"text": "same owner shared receipt", "data_classification": "employer"}
    domain.create_intake(payload, actor=owner, idempotency_key="shared-receipt")
    with pytest.raises(DomainError) as error:
        domain.create_intake(payload, actor=generator, idempotency_key="shared-receipt")
    assert error.value.code == "CLASSIFICATION_FORBIDDEN"


def test_request_payload_cannot_expand_deployed_allowset(domain, generator):
    with pytest.raises(DomainError) as error:
        domain.create_intake(
            {"text": "x", "allowed_classifications": ["employer"]},
            actor=generator,
            idempotency_key="forged-allowset",
        )
    assert error.value.code == "FORBIDDEN"


def test_recovery_with_reduced_classification_grant_preserves_pending_journal(
    domain, owner, writer
):
    source = classified_source(domain, owner, "employer")
    change = domain.propose_change(
        {
            "title": "Retain prepared work",
            "body": "private evidence",
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=owner,
        idempotency_key="prepared-private",
    )
    authorized = Actor(
        owner.owner_id,
        "authorized-writer",
        "service",
        frozenset({"*"}),
        frozenset({"public", "personal", "employer"}),
    )

    def crash(stage, _journal):
        if stage == "after_prepare":
            raise RuntimeError("simulated process loss")

    domain.fault_injector = crash
    with pytest.raises(RuntimeError):
        domain.apply_change(change["change_id"], actor=authorized, idempotency_key="prepare")
    domain.fault_injector = None
    assert domain.recover_writer(actor=writer)[0]["state"] == "blocked"
    assert domain.writer.head() is None
    assert domain.get_change(change["change_id"], actor=owner)["state"] == "prepared"
    assert domain.recover_writer(actor=authorized)[0]["state"] == "committed"


def test_task_output_classification_does_not_reclassify_original_public_evidence(
    domain, source, owner, writer
):
    claimed = domain.claim_task("model-worker", actor=writer, kinds=["knowledge.generate"])
    domain.update_task(
        claimed["task_id"],
        {
            "state": "succeeded",
            "lease_token": claimed["lease_token"],
            "result": {"input_classification": "personal", "summary": "context"},
        },
        actor=writer,
        idempotency_key="model-output",
    )
    assert domain.get_source(source["source_id"], actor=owner)["data_classification"] == "public"
    assert domain.read_task(claimed["task_id"], actor=owner)["data_classification"] == "personal"


def test_authorized_pagination_and_outbox_skip_inaccessible_prefix(
    domain, owner, generator, writer
):
    employer = classified_source(domain, owner, "employer")
    public = classified_source(domain, owner, "public")
    for identity, source in (("ent_a", employer), ("ent_b", public), ("ent_c", public)):
        change = domain.propose_change(
            {
                "entry_id": identity,
                "title": identity,
                "body": "private evidence",
                "evidence_refs": [{"source_id": source["source_id"]}],
            },
            actor=owner,
            idempotency_key="pagination:" + identity,
        )
        domain.apply_change(change["change_id"], actor=owner, idempotency_key="apply:" + identity)
    assert [entry["entry_id"] for entry in domain.list_entries(actor=generator, limit=1)] == [
        "ent_b"
    ]
    assert [
        entry["entry_id"] for entry in domain.list_entries(actor=generator, limit=1, offset=1)
    ] == ["ent_c"]
    reviews = domain.list_reviews(actor=generator, state=None, limit=1)
    assert len(reviews) == 1 and reviews[0]["entry_id"] == "ent_b"
    events = domain.list_events(actor=generator, limit=1)
    assert len(events) == 1 and events[0]["aggregate_id"] == public["intake_id"]
    delivery = domain.claim_events("restricted-projection", actor=writer, limit=1)
    assert len(delivery) == 1 and delivery[0]["event_id"] == events[0]["event_id"]


@pytest.mark.parametrize("label", ["secret", "unknown", "employer"])
def test_maintenance_expires_restricted_raw_without_granting_read(
    domain, owner, writer, generator, label
):
    marker = "restricted-input-must-not-outlive-ttl"
    intake = domain.create_intake(
        {"text": marker, "data_classification": label}, actor=owner, idempotency_key="restricted"
    )
    domain.update_intake(
        intake["intake_id"],
        {"expires_at": "2000-01-01T00:00:00Z"},
        actor=owner,
        idempotency_key="past-due",
    )
    assert domain.list_expired_intakes(actor=writer) == []
    with pytest.raises(DomainError):
        domain.read_intake(intake["intake_id"], actor=writer)
    result = domain.expire_intakes(actor=writer)
    assert result[0]["state"] == "expired" and "payload" not in result[0]
    assert set(result[0]) == {
        "intake_id",
        "state",
        "source_id",
        "expires_at",
        "revision",
        "data_classification",
        "classification_labels",
    }
    assert domain.read_intake_lifecycle(intake["intake_id"], actor=writer) == result[0]
    with pytest.raises(DomainError):
        domain.read_intake_lifecycle(intake["intake_id"], actor=generator)
    with pytest.raises(DomainError):
        domain.read_intake(intake["intake_id"], actor=writer)
    stranger = Actor("other-owner", "other-worker", "service", frozenset({"*"}))
    with pytest.raises(DomainError) as error:
        domain.read_intake_lifecycle(intake["intake_id"], actor=stranger)
    assert error.value.code == "NOT_FOUND"
    with domain.store.reading() as connection:
        data = [row[0] for row in connection.execute("SELECT data FROM kp_records")]
        receipts = [row[0] for row in connection.execute("SELECT response FROM kp_receipts")]
    assert marker not in str(data + receipts)
    assert domain.expire_intakes(actor=writer) == []


def test_model_cannot_shorten_retention_to_trigger_unauthorized_discard(domain, owner, generator):
    intake = domain.create_intake(
        {"text": "keep this preview"}, actor=owner, idempotency_key="expiry"
    )
    with pytest.raises(DomainError) as error:
        domain.update_intake(
            intake["intake_id"],
            {"expires_at": "2000-01-01T00:00:00Z"},
            actor=generator,
            idempotency_key="force-expire",
        )
    assert error.value.code == "FORBIDDEN"
