"""Human exercise completion is evidence-bound, not an arbitrary task-state shortcut."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from ahadiff.knowledge_platform.errors import DomainError


def exercise(domain, owner, writer, proposal):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="seed")
    suggestion = domain.create_task(
        "radar_suggestion",
        {"target_entry_ids": [entry["entry_id"]]},
        actor=owner,
        idempotency_key="suggestion",
    )
    learning = domain.create_task(
        "learning",
        {"suggestion_id": suggestion["task_id"], "target_entry_ids": [entry["entry_id"]]},
        actor=owner,
        idempotency_key="exercise",
    )
    domain.set_channel_state(
        "radar:" + suggestion["task_id"],
        {"suggestion_state": "accepted", "learning_id": learning["task_id"]},
        actor=owner,
        idempotency_key="accept",
    )
    facts = {
        "device_id": "actual-device",
        "session_id": "actual-exercise",
        "version": 1,
        "entry_id": entry["entry_id"],
        "user_text": "My observed practice result",
        "metadata": {"radar_suggestion_id": suggestion["task_id"]},
    }
    outcome = domain.submit_learning_outcome(facts, actor=owner, idempotency_key="outcome")
    return learning, outcome, facts


def test_human_completion_is_once_under_replay_and_concurrency(domain, owner, writer, proposal):
    task, outcome, _ = exercise(domain, owner, writer, proposal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda n: domain.complete_learning_task(
                    task["task_id"],
                    outcome["outcome_id"],
                    actor=owner,
                    idempotency_key="done:" + str(n),
                ),
                range(2),
            )
        )
    assert results[0] == results[1]
    assert results[0]["state"] == "succeeded"
    assert results[0]["attempt"] == 0
    assert len(domain.list_events(actor=owner, types=["learning.task.completed.v1"])) == 1


def test_machine_wrong_outcome_and_wrong_task_cannot_forge_completion(
    domain, owner, writer, generator, proposal
):
    task, outcome, facts = exercise(domain, owner, writer, proposal)
    for actor in (writer, generator):
        with pytest.raises(DomainError) as error:
            domain.complete_learning_task(
                task["task_id"], outcome["outcome_id"], actor=actor, idempotency_key="forged"
            )
        assert error.value.code == "FORBIDDEN"
    wrong = domain.submit_learning_outcome(
        {**facts, "version": 2, "metadata": {}}, actor=owner, idempotency_key="wrong"
    )
    with pytest.raises(DomainError) as error:
        domain.complete_learning_task(
            task["task_id"], wrong["outcome_id"], actor=owner, idempotency_key="wrong"
        )
    assert error.value.code == "EVIDENCE_REQUIRED"
    assert domain.read_task(task["task_id"], actor=owner)["state"] == "queued"
    worker_job = domain.create_task(
        "knowledge.generate", {}, actor=owner, idempotency_key="worker-job"
    )
    with pytest.raises(DomainError):
        domain.complete_learning_task(
            worker_job["task_id"], outcome["outcome_id"], actor=owner, idempotency_key="bypass"
        )
