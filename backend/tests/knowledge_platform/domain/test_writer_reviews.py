"""Actual bare Git CAS, exact-diff review and cross-system crash recovery tests."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.contracts import text_hash
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.errors import DomainError


def test_formal_body_is_git_and_commit_is_exactly_once(domain, writer, owner, proposal):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    assert entry["body"] == proposal["body"]
    assert domain.get_entry(entry["entry_id"], actor=owner) == entry | {"schema_version": 6}
    assert (
        domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply") == entry
    )
    assert (
        domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="repeat")[
            "commit_sha"
        ]
        == entry["commit_sha"]
    )
    with domain.store.reading() as connection:
        catalog = domain.store.get(connection, "entry", entry["entry_id"], owner.owner_id)
        assert "body" not in catalog
    events = domain.list_events(actor=owner, types=["knowledge.committed.v1"])
    assert len(events) == 1
    assert entry["publication_intent"] == "public" and entry["publication_eligible"] is True


def test_agent_cannot_apply_even_with_forged_scope(domain, proposal):
    privileged_agent = Actor("owner-one", "adversarial-agent", "agent", frozenset({"*"}))
    with pytest.raises(DomainError) as error:
        domain.apply_change(proposal["change_id"], actor=privileged_agent, idempotency_key="attack")
    assert error.value.code == "FORBIDDEN"
    assert domain.writer.head() is None


def test_external_formal_edit_cannot_be_overwritten_using_stale_catalog(
    domain, writer, owner, generator, proposal, source
):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="initial")
    external = {**entry, "body": "An external editor's unsynchronized work.\n"}
    external["content_hash"] = text_hash(external["body"])
    journal = domain.writer.prepare(
        {**proposal, "change_id": "external-edit", "base_git_revision": domain.writer.head()},
        external,
    )
    domain.writer.publish(journal)
    with pytest.raises(DomainError) as error:
        domain.propose_change(
            {
                "entry_id": entry["entry_id"],
                "base_revision": 1,
                "title": entry["title"],
                "body": entry["body"] + "Another addition.\n",
                "evidence_refs": [{"source_id": source["source_id"]}],
            },
            actor=generator,
            idempotency_key="stale-catalog-proposal",
        )
    assert error.value.code == "BASE_STALE"
    assert domain.writer.head() == journal["commit_sha"]
    assert (
        domain.writer.read_entry(journal["commit_sha"], entry["path"])["body"] == external["body"]
    )
    assert domain.get_entry(entry["entry_id"], actor=owner)["body"] == entry["body"]


def test_unindexed_formal_file_cannot_be_replaced_as_a_new_entry(
    domain, owner, generator, proposal, source
):
    external = {
        "entry_id": proposal["entry_id"],
        "owner_id": owner.owner_id,
        "revision": 1,
        "title": "Unindexed existing file",
        "body": "Do not overwrite me.",
        "updated_at": proposal["created_at"],
    }
    journal = domain.writer.prepare({**proposal, "change_id": "external-first"}, external)
    domain.writer.publish(journal)
    with pytest.raises(DomainError) as error:
        domain.propose_change(
            {
                "entry_id": proposal["entry_id"],
                "base_revision": 0,
                "title": "New entry",
                "body": "Evidence says addition preserves history.",
                "evidence_refs": [{"source_id": source["source_id"]}],
            },
            actor=generator,
            idempotency_key="unindexed-collision",
        )
    assert error.value.code == "BASE_STALE"
    assert domain.writer.head() == journal["commit_sha"]


def test_deletion_and_restoration_are_not_mistaken_for_whitespace_no_change(
    domain, owner, generator, writer, proposal
):
    original = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="initial")
    current = original
    for operation in ("delete", "update"):
        change = domain.propose_change(
            {
                "entry_id": current["entry_id"],
                "base_revision": current["revision"],
                "title": current["title"],
                "body": current["body"],
                "operation": operation,
                "evidence_refs": current["evidence_refs"],
                "metadata": current["metadata"],
            },
            actor=generator,
            idempotency_key=operation,
        )
        assert change["state"] == "needs_user"
        assert "important_merge_delete" in change["risk_flags"]
        nonce = domain.create_decision_challenge(change["change_id"], actor=owner)
        domain.record_review(
            change["change_id"],
            {
                "decision": "approve",
                "expected_patch_hash": change["patch_hash"],
                "base_revision": change["base_revision"],
                "decision_nonce": nonce["decision_nonce"],
            },
            actor=owner,
            idempotency_key="approve:" + operation,
        )
        current = domain.apply_change(
            change["change_id"], actor=writer, idempotency_key="apply:" + operation
        )
        assert current["deleted"] is (operation == "delete")
        assert current["body"] == original["body"]
    assert current["revision"] == 3
    assert (
        domain.get_entry(original["entry_id"], actor=owner, revision=1)["body"] == original["body"]
    )
    assert domain.get_entry(original["entry_id"], actor=owner, revision=2)["deleted"] is True


@pytest.mark.parametrize(
    "risk", ["core_conclusion", "important_merge_delete", "personal_position", "privacy_exposure"]
)
def test_all_four_major_risks_require_bound_user_review(
    domain, owner, generator, reviewer, writer, source, risk
):
    outcome = domain.submit_learning_outcome(
        {
            "device_id": "device",
            "session_id": "session",
            "version": 1,
            "user_text": "My actual personal statement",
        },
        actor=owner,
        idempotency_key="learning",
    )
    change = domain.propose_change(
        {
            "title": "Risk boundary",
            "body": "Evidence-based content",
            "risk_flags": [risk],
            "evidence_refs": [
                {"source_id": source["source_id"]},
                {"learning_outcome_id": outcome["outcome_id"]},
            ],
        },
        actor=generator,
        idempotency_key="risky",
    )
    assert change["state"] == "needs_user"
    with pytest.raises(DomainError) as error:
        domain.record_review(
            change["change_id"],
            {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
            actor=reviewer,
            idempotency_key="ai-approve",
        )
    assert error.value.code == "REVIEW_REQUIRED"
    with pytest.raises(DomainError):
        domain.apply_change(change["change_id"], actor=writer, idempotency_key="early")
    challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
    approved = domain.record_review(
        change["change_id"],
        {
            "decision": "approve",
            "expected_patch_hash": change["patch_hash"],
            "base_revision": challenge["base_revision"],
            "decision_nonce": challenge["decision_nonce"],
        },
        actor=owner,
        idempotency_key="user-approve",
    )
    assert approved["change_state"] == "approved"
    assert domain.read_task(approved["task_id"], actor=owner)["kind"] == "knowledge.apply"
    assert (
        domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply")["revision"]
        == 1
    )


def test_personal_understanding_cannot_be_invented(domain, generator, source):
    with pytest.raises(DomainError) as error:
        domain.propose_change(
            {
                "title": "Invented",
                "body": "我的理解：完全掌握了。",
                "evidence_refs": [{"source_id": source["source_id"]}],
            },
            actor=generator,
            idempotency_key="invent",
        )
    assert error.value.code == "PERSONAL_EVIDENCE_REQUIRED"


def test_ai_review_is_independent_and_can_escalate(
    domain, owner, generator, reviewer, source, writer
):
    change = domain.propose_change(
        {
            "title": "Independent",
            "body": "A paraphrase",
            "risk_flags": ["requires_ai_review"],
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=generator,
        idempotency_key="change",
    )
    self_reviewer = Actor(generator.owner_id, generator.actor_id, "agent", frozenset({"*"}))
    with pytest.raises(DomainError) as error:
        domain.record_review(
            change["change_id"],
            {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
            actor=self_reviewer,
            idempotency_key="self",
        )
    assert error.value.code == "FORBIDDEN"
    result = domain.record_review(
        change["change_id"],
        {
            "decision": "needs_user",
            "expected_patch_hash": change["patch_hash"],
            "risk_flags": ["core_conclusion"],
        },
        actor=reviewer,
        idempotency_key="escalate",
    )
    assert result["change_state"] == "needs_user"
    assert "core_conclusion" in domain.get_change(change["change_id"], actor=owner)["risk_flags"]
    with pytest.raises(DomainError):
        domain.apply_change(change["change_id"], actor=writer, idempotency_key="no-bypass")


@pytest.mark.parametrize("changed", ["base", "patch", "policy"])
def test_review_invalidated_by_base_patch_or_policy(
    domain, owner, generator, reviewer, writer, source, changed
):
    change = domain.propose_change(
        {
            "title": "Review binding",
            "body": "Evidence paraphrase",
            "risk_flags": ["requires_ai_review"],
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=generator,
        idempotency_key="reviewed",
    )
    domain.record_review(
        change["change_id"],
        {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
        actor=reviewer,
        idempotency_key="approve",
    )
    if changed == "base":
        other = domain.propose_change(
            {
                "title": "Other",
                "body": "Other",
                "evidence_refs": [{"source_id": source["source_id"]}],
            },
            actor=generator,
            idempotency_key="other",
        )
        domain.record_review(
            other["change_id"],
            {"decision": "approve", "expected_patch_hash": other["patch_hash"]},
            actor=reviewer,
            idempotency_key="other-review",
        )
        domain.apply_change(other["change_id"], actor=writer, idempotency_key="other")
    elif changed == "patch":
        with domain.store.transaction() as connection:
            record = domain.store.get(connection, "change", change["change_id"], owner.owner_id)
            record["body"] = "tampered after approval"
            domain.store.put(connection, "change", change["change_id"], record)
    else:
        domain.policy_version = "2"
    with pytest.raises(DomainError) as error:
        domain.apply_change(change["change_id"], actor=writer, idempotency_key="apply")
    assert error.value.code == ("BASE_STALE" if changed == "base" else "REVIEW_REQUIRED")


@pytest.mark.parametrize("stage", ["after_prepare", "after_ref_update", "after_commit"])
def test_crash_recovery_applies_once(domain, owner, writer, proposal, stage):
    def crash(point, journal):
        if point == stage:
            raise RuntimeError("injected process interruption")

    domain.fault_injector = crash
    with pytest.raises(RuntimeError):
        domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    recovered = DomainService(domain.store.path)
    try:
        recovered.recover_writer(actor=writer)
        entry = recovered.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
        assert entry["revision"] == 1
        assert len(recovered.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1
    finally:
        recovered.close()


def test_recovery_recognizes_ancestor_not_just_head(domain, owner, writer, proposal):
    captured = {}

    def crash(stage, journal):
        if stage == "after_ref_update":
            captured.update(journal)
            raise RuntimeError("crash after CAS")

    domain.fault_injector = crash
    with pytest.raises(RuntimeError):
        domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    # A later unrelated Git commit exists when the process returns.
    fake_change = {
        **proposal,
        "change_id": "chg_later",
        "entry_id": "ent_later",
        "base_git_revision": domain.writer.head(),
    }
    later_entry = {**captured["entry"], "entry_id": "ent_later", "body": "Unrelated later entry"}
    later = domain.writer.prepare(fake_change, later_entry)
    domain.writer.publish(later)
    domain.fault_injector = None
    domain.recover_writer(actor=writer)
    assert domain.writer.head() == later["commit_sha"]
    assert (
        domain.get_entry(proposal["entry_id"], actor=owner)["commit_sha"] == captured["commit_sha"]
    )
    assert len(domain.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1


def test_same_change_concurrent_processes_has_one_commit(domain, owner, writer, proposal):
    second = DomainService(domain.store.path)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    service.apply_change,
                    proposal["change_id"],
                    actor=writer,
                    idempotency_key=f"worker:{number}",
                )
                for number, service in enumerate((domain, second))
            ]
            results = [future.result() for future in futures]
        assert results[0]["commit_sha"] == results[1]["commit_sha"]
        assert len(domain.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1
    finally:
        second.close()


def test_semantic_rewrite_infers_core_gate_without_model_flag(
    domain, owner, generator, writer, source, proposal
):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="first")
    rewrite = domain.propose_change(
        {
            "entry_id": entry["entry_id"],
            "base_revision": 1,
            "title": entry["title"],
            "body": "Opposite conclusion.",
            "evidence_refs": [{"source_id": source["source_id"]}],
            "risk_flags": [],
        },
        actor=generator,
        idempotency_key="rewrite",
    )
    assert rewrite["state"] == "needs_user"
    assert "core_conclusion" in rewrite["risk_flags"]


def test_backup_restores_git_objects_ledger_and_sources(
    domain, owner, writer, proposal, source, tmp_path
):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    destination = tmp_path / "recovery-snapshot"
    manifest = domain.backup(destination, actor=writer)
    assert manifest["complete"] and manifest["git_revision"] == entry["commit_sha"]
    restored = DomainService(
        destination / "platform.db", destination / "vault.git", object_path=destination / "objects"
    )
    try:
        assert restored.get_entry(entry["entry_id"], actor=owner)["body"] == entry["body"]
        assert restored.read_evidence({"source_id": source["source_id"]}, actor=owner)["text"]
        assert len(restored.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1
        assert restored.read_task(source["task_id"], actor=owner)["state"] == "queued"
        from ahadiff.knowledge_platform.search.index import SearchService

        found = SearchService(restored).search("Evidence", actor=owner)
        assert found[0]["entry_id"] == entry["entry_id"]
        assert found[0]["revision"] == entry["revision"]
    finally:
        restored.close()


@pytest.mark.parametrize("stage", ["after_prepare", "after_ref_update", "after_commit"])
def test_real_process_kill_recovers_exactly_once(domain, owner, writer, proposal, stage):
    script = """
import os, signal, sys
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
service = DomainService(sys.argv[1])
def kill(stage, journal):
    if stage == sys.argv[3]:
        if os.name == 'nt':
            os._exit(137)
        os.kill(os.getpid(), signal.SIGKILL)
service.fault_injector = kill
actor = Actor('owner-one', 'child-writer', 'service', frozenset({'*'}))
service.apply_change(sys.argv[2], actor=actor, idempotency_key='child-apply')
raise RuntimeError('kill point was not reached')
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(domain.store.path), proposal["change_id"], stage],
        env=dict(os.environ),
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == (137 if os.name == "nt" else -signal.SIGKILL)
    restarted = DomainService(domain.store.path)
    try:
        restarted.recover_writer(actor=writer)
        result = restarted.apply_change(
            proposal["change_id"], actor=writer, idempotency_key="after-kill"
        )
        assert result["body"] == proposal["body"]
        assert result["revision"] == 1
        assert len(restarted.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1
        assert restarted.writer._git("rev-list", "--count", restarted.writer.ref).strip() == b"1"
    finally:
        restarted.close()


def test_committed_body_survives_projection_failure_then_rebuild(
    domain, owner, writer, proposal, monkeypatch
):
    from ahadiff.knowledge_platform.search.index import SearchIndex, SearchService

    entry = domain.apply_change(
        proposal["change_id"], actor=writer, idempotency_key="commit-before-index"
    )

    def broken_index(_self, _documents):
        raise sqlite3.OperationalError("fixture projection failure")

    with monkeypatch.context() as context:
        context.setattr(SearchIndex, "rebuild", broken_index)
        with pytest.raises(sqlite3.OperationalError):
            SearchService(domain).search("Evidence", actor=owner)
    assert domain.get_change(proposal["change_id"], actor=owner)["state"] == "committed"
    assert domain.get_entry(entry["entry_id"], actor=owner)["commit_sha"] == entry["commit_sha"]
    found = SearchService(domain).search("Evidence", actor=owner)
    assert found[0]["entry_id"] == entry["entry_id"] and found[0]["revision"] == 1
    assert found[0]["stale_index"] is False


def test_repeated_user_approval_reuses_apply_task_and_nonce_is_single_use(
    domain, owner, generator, source
):
    change = domain.propose_change(
        {
            "title": "Major",
            "body": "Safe proposal",
            "risk_flags": ["core_conclusion"],
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=generator,
        idempotency_key="major",
    )
    first_challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
    payload = {
        "decision": "approve",
        "expected_patch_hash": change["patch_hash"],
        "base_revision": 0,
        "decision_nonce": first_challenge["decision_nonce"],
    }
    first = domain.record_review(
        change["change_id"], payload, actor=owner, idempotency_key="approval-1"
    )
    with pytest.raises(DomainError):
        domain.record_review(
            change["change_id"], payload, actor=owner, idempotency_key="nonce-reuse"
        )
    second_challenge = domain.create_decision_challenge(change["change_id"], actor=owner)
    second = domain.record_review(
        change["change_id"],
        {**payload, "decision_nonce": second_challenge["decision_nonce"]},
        actor=owner,
        idempotency_key="approval-2",
    )
    assert second["task_id"] == first["task_id"]


def test_only_whitespace_does_not_report_new_knowledge(domain, owner, generator, writer, proposal):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    formatting = domain.propose_change(
        {
            "entry_id": entry["entry_id"],
            "base_revision": 1,
            "title": entry["title"],
            "body": entry["body"] + "\n\n",
            "evidence_refs": entry["evidence_refs"],
        },
        actor=generator,
        idempotency_key="formatting",
    )
    assert formatting["state"] == "no_change"
    assert len(domain.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1


def test_entry_id_collision_cannot_overwrite_another_owner(domain, owner, writer, proposal):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="apply")
    attacker = Actor("other-owner", "other-human", "user", frozenset({"*"}))
    outcome = domain.submit_learning_outcome(
        {
            "device_id": "other",
            "session_id": "other",
            "version": 1,
            "user_text": "Different owned evidence",
        },
        actor=attacker,
        idempotency_key="evidence",
    )
    with pytest.raises(DomainError) as error:
        domain.propose_change(
            {
                "entry_id": entry["entry_id"],
                "base_revision": 0,
                "title": "Overwrite attempt",
                "body": "Cannot overwrite",
                "evidence_refs": [{"learning_outcome_id": outcome["outcome_id"]}],
            },
            actor=attacker,
            idempotency_key="collision",
        )
    assert error.value.code == "NOT_FOUND"
    assert domain.get_entry(entry["entry_id"], actor=owner)["commit_sha"] == entry["commit_sha"]


def test_source_id_does_not_auto_approve_unsupported_paraphrase(domain, generator, source):
    change = domain.propose_change(
        {
            "title": "Unsupported",
            "body": "Unrelated invented conclusion",
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=generator,
        idempotency_key="unsupported",
    )
    assert change["state"] == "needs_ai_review"
    assert "requires_ai_review" in change["risk_flags"]


def test_standalone_proposal_has_durable_review_but_workflow_association_does_not_duplicate(
    domain, owner, generator, source
):
    payload = {
        "title": "Proposal",
        "body": "Paraphrase",
        "evidence_refs": [{"source_id": source["source_id"]}],
    }
    standalone = domain.propose_change(payload, actor=generator, idempotency_key="standalone")
    assert domain.read_task(standalone["task_id"], actor=owner)["kind"] == "knowledge.review"
    workflow = domain.claim_task("generation", actor=owner, kinds=["knowledge.generate"])
    linked = domain.propose_change(
        payload, actor=generator, idempotency_key="linked", workflow_task_id=workflow["task_id"]
    )
    assert linked["task_id"] == workflow["task_id"]
    with domain.store.reading() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM kp_records WHERE kind='task' "
                "AND json_extract(data,'$.kind')='knowledge.review'"
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(DomainError):
        domain.propose_change(
            payload,
            actor=generator,
            idempotency_key="wrong",
            workflow_task_id=standalone["task_id"],
        )
