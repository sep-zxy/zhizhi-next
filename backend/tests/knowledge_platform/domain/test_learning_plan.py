"""Synthetic exact-source plans using real isolated domain persistence."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.learning.plan import accept_plan, preview_plan
from ahadiff.knowledge_platform.radar.service import RadarService


def reference(domain, owner, writer, proposal):
    applied = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="plan-seed")
    entry = domain.get_entry(applied["entry_id"], actor=owner)
    return {"entry_id": entry["entry_id"], "revision": entry["revision"],
            "expected_content_hash": entry["content_hash"], "focus": "architecture"}


def test_preview_no_tasks_accept_replay_and_pinned_completion(domain, owner, writer, proposal, monkeypatch):
    ref = reference(domain, owner, writer, proposal)
    before = domain.list_events(actor=owner)
    plan = preview_plan(domain, ref, actor=owner)
    assert plan["state"] == "preview"
    assert plan["authorship"] == "deterministic_template_not_user_answer"
    assert domain.list_events(actor=owner) == before
    payload = {**ref, "plan_hash": plan["plan_hash"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: accept_plan(domain, payload, actor=owner), range(2)))
    assert results[0] == results[1]
    result = results[0]
    assert domain.read_task(result["learning_id"], actor=owner)["kind"] == "learning"
    practice = RadarService(domain).read(result["suggestion_id"], actor=owner)
    assert practice["practice"]["source_reference"] == plan["source_reference"]
    assert practice["user_text"] == ""
    facts = RadarService(domain).validate_submission({
        "device_id": "fixture-device", "session_id": "radar:" + result["suggestion_id"],
        "version": 1, "entry_id": ref["entry_id"], "user_text": "我的真实观察，不是计划模板。",
        "metadata": {"radar_suggestion_id": result["suggestion_id"]},
    }, actor=owner)
    assert facts["metadata"]["source_reference"] == plan["source_reference"]
    outcome = domain.submit_learning_outcome(facts, actor=owner, idempotency_key="plan-outcome")
    original_decide = RadarService.decide
    def completing_decision(service, suggestion_id, decision, *, actor):
        service.complete(suggestion_id, outcome["outcome_id"], actor=actor)
        return original_decide(service, suggestion_id, decision, actor=actor)
    monkeypatch.setattr(RadarService, "decide", completing_decision)
    raced = accept_plan(domain, payload, actor=owner)
    assert raced["suggestion_state"] == "completed"
    assert domain.read_task(result["learning_id"], actor=owner)["state"] == "succeeded"
    replay = accept_plan(domain, payload, actor=owner)
    assert replay["learning_id"] == result["learning_id"]
    assert replay["suggestion_state"] == "completed"
    assert replay["learning_outcome_id"] == outcome["outcome_id"]


def test_reject_wrong_plan_hash_source_focus_extra_and_machine(domain, owner, writer, generator, proposal):
    ref = reference(domain, owner, writer, proposal)
    plan = preview_plan(domain, ref, actor=owner)
    for invalid in ({**ref, "focus": "syntax"}, {**ref, "revision": True},
                    {**ref, "user_text": "forged"}, {**ref, "expected_content_hash": "0" * 64}):
        with pytest.raises(DomainError):
            preview_plan(domain, invalid, actor=owner)
    with pytest.raises(DomainError) as changed:
        accept_plan(domain, {**ref, "plan_hash": "0" * 64}, actor=owner)
    assert changed.value.code == "PLAN_CHANGED"
    with pytest.raises(DomainError) as forbidden:
        accept_plan(domain, {**ref, "plan_hash": plan["plan_hash"]}, actor=generator)

    assert forbidden.value.code == "FORBIDDEN"


def test_new_entry_head_does_not_change_accepted_source(domain, owner, writer, source):
    from test_entry_versions import commit

    first = commit(domain, owner, writer, source)
    ref = {"entry_id": first["entry_id"], "revision": first["revision"],
           "expected_content_hash": first["content_hash"], "focus": "architecture"}
    plan = preview_plan(domain, ref, actor=owner)
    commit(domain, owner, writer, source, first, title="New head", body=first["body"] + "New architecture.\n")
    accepted = accept_plan(domain, {**ref, "plan_hash": plan["plan_hash"]}, actor=owner)
    practice = RadarService(domain).read(accepted["suggestion_id"], actor=owner)["practice"]
    assert practice["body"] == first["body"]
    assert practice["title"] == first["title"]
    assert practice["source_reference"]["revision"] == 1


@pytest.mark.parametrize("saved_reference,expected_status", [
    ("missing", "unknown"),
    ("recorded", "available"),
    ("missing_revision", "unavailable"),
    ("wrong_hash", "unavailable"),
])
def test_old_radar_completion_only_restores_verifiable_source(
    domain, owner, writer, source, saved_reference, expected_status,
):
    from test_entry_versions import commit, edit_history

    question = "What should be verified about addition?"
    metadata = {"open_questions": [question]}
    first = commit(domain, owner, writer, source, metadata=metadata)
    entry_id = first["entry_id"]
    identity = hashlib.sha256(json.dumps(
        {"entry_id": entry_id, "question": question, "evidence_refs": first["evidence_refs"]},
        ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()
    task = domain.create_task(
        "radar_suggestion",
        {"target_entry_ids": [entry_id], "proposed_exercise": question,
         "evidence_refs": first["evidence_refs"], "classification": "public"},
        actor=owner, idempotency_key="radar-candidate:" + identity,
    )
    sid = task["task_id"]
    radar = RadarService(domain)
    assert radar.run(actor=owner)[0]["suggestion_id"] == sid
    radar.decide(sid, "accept", actor=owner)
    facts = {
        "device_id": "legacy-fixture", "session_id": "radar:" + sid, "version": 1,
        "entry_id": entry_id, "user_text": "  保持这段原话，包括空格和换行。\n",
        "metadata": {"radar_suggestion_id": sid, "classification": "personal"},
    }
    if saved_reference != "missing":
        facts["metadata"]["source_reference"] = {
            "entry_id": entry_id,
            "revision": 999 if saved_reference == "missing_revision" else first["revision"],
            "expected_content_hash": "0" * 64 if saved_reference == "wrong_hash" else first["content_hash"],
        }
    outcome = domain.submit_learning_outcome(facts, actor=owner, idempotency_key="legacy-outcome")
    radar.complete(sid, outcome["outcome_id"], actor=owner)
    second = commit(
        domain, owner, writer, source, first, title="New title", body=first["body"] + "New text.\n",
        metadata=metadata,
    )
    assert radar.run(actor=owner) == []
    restored = radar.read(sid, actor=owner)
    assert restored["user_text"] == facts["user_text"]
    practice = restored["practice"]
    assert practice["source_status"] == expected_status
    if expected_status == "available":
        assert practice["title"] == first["title"]
        assert practice["body"] == first["body"]
        assert practice["source_reference"]["revision"] == first["revision"]
        reader = Actor(owner.owner_id, "personal-reader", "service", frozenset({"knowledge:read"}))
        assert radar.read(sid, actor=reader)["practice"]["body"] == first["body"]
        edit_history(domain, first["revision"], data_classification="secret", classification_labels=["secret"])
        restricted = radar.read(sid, actor=reader)
        assert restricted["user_text"] == facts["user_text"]
        assert restricted["practice"]["source_status"] == "unavailable"
        assert restricted["practice"]["title"] == restricted["practice"]["body"] == ""
        commit(domain, owner, writer, source, second, operation="delete")
        restored = radar.read(sid, actor=owner)
        assert restored["user_text"] == facts["user_text"]
        practice = restored["practice"]
        assert practice["source_status"] == "unavailable"
    assert practice["title"] == "" and practice["body"] == "" and "source_reference" not in practice
    viewer = Actor(owner.owner_id, "public-only", "service", frozenset({"knowledge:read"}), frozenset({"public"}))
    with pytest.raises(DomainError) as denied:
        radar.read(sid, actor=viewer)
    assert denied.value.code == "CLASSIFICATION_FORBIDDEN"
