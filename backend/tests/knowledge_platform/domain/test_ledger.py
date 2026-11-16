"""Persistent tasks, exact idempotency, owner isolation and outbox lease tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from ahadiff.knowledge_platform.auth import Actor, TokenAuthenticator
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.errors import DomainError


def test_task_durable_idempotency_and_input_conflict(domain, owner):
    first = domain.create_task("fixture", {"input": 1}, actor=owner, idempotency_key="key")
    assert domain.create_task("fixture", {"input": 1}, actor=owner, idempotency_key="key") == first
    with pytest.raises(DomainError, match="different input") as error:
        domain.create_task("fixture", {"input": 2}, actor=owner, idempotency_key="key")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    second = DomainService(domain.store.path)
    try:
        assert second.read_task(first["task_id"], actor=owner) == first
    finally:
        second.close()


def test_owner_and_untrusted_actor_rejected(domain, owner):
    task = domain.create_task("fixture", {}, actor=owner, idempotency_key="key")
    stranger = Actor("other-owner", "other", "user", frozenset({"*"}))
    with pytest.raises(DomainError) as error:
        domain.read_task(task["task_id"], actor=stranger)
    assert error.value.code == "NOT_FOUND"
    with pytest.raises(DomainError) as error:
        domain.create_task(
            "fixture", {}, actor={"owner_id": owner.owner_id}, idempotency_key="fake"
        )
    assert error.value.code == "UNAUTHENTICATED"
    with pytest.raises(DomainError):
        domain.create_task(
            "fixture", {"owner_id": "other-owner"}, actor=owner, idempotency_key="spoof"
        )


def test_claim_is_atomic_and_lease_required(domain, owner):
    task = domain.create_task("fixture", {}, actor=owner, idempotency_key="task")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda number: domain.claim_task(f"worker-{number}", actor=owner), range(2))
        )
    claimed = [result for result in results if result]
    assert len(claimed) == 1
    with pytest.raises(DomainError) as error:
        domain.update_task(
            task["task_id"], {"state": "succeeded"}, actor=owner, idempotency_key="bad"
        )
    assert error.value.code == "LEASE_STALE"
    result = domain.update_task(
        task["task_id"],
        {"state": "succeeded", "lease_token": claimed[0]["lease_token"], "result": {"ok": True}},
        actor=owner,
        idempotency_key="done",
    )
    assert result["state"] == "succeeded"
    assert domain.claim_task("again", actor=owner) is None


def test_expired_lease_reclaims_and_old_worker_cannot_write(domain, owner):
    task = domain.create_task("fixture", {}, actor=owner, idempotency_key="task")
    old = domain.claim_task("old", actor=owner)
    with domain.store.transaction() as connection:
        record = domain.store.get(connection, "task", task["task_id"], owner.owner_id)
        record["lease_until"] = "2000-01-01T00:00:00+00:00"
        domain.store.put(connection, "task", task["task_id"], record)
    fresh = domain.claim_task("new", actor=owner)
    assert fresh["attempt"] == 2 and fresh["reconciliation_required"]
    with pytest.raises(DomainError) as error:
        domain.update_task(
            task["task_id"],
            {"state": "succeeded", "lease_token": old["lease_token"]},
            actor=owner,
            idempotency_key="late",
        )
    assert error.value.code == "LEASE_STALE"


@pytest.mark.parametrize(
    "wait_state", ["waiting_auth", "waiting_quota", "blocked", "partial", "waiting_user"]
)
def test_waiting_state_checkpoint_and_resume(domain, owner, wait_state):
    task = domain.create_task("fixture", {}, actor=owner, idempotency_key="task")
    lease = domain.claim_task("worker", actor=owner)
    waiting = domain.update_task(
        task["task_id"],
        {
            "state": wait_state,
            "lease_token": lease["lease_token"],
            "checkpoint": {"change_id": "chg_resume"},
            "error": {"code": "EXPECTED_WAIT"},
        },
        actor=owner,
        idempotency_key="wait",
    )
    assert domain.claim_task("blocked-worker", actor=owner) is None
    resumed = domain.update_task(
        task["task_id"],
        {"state": "queued", "expected_revision": waiting["revision"]},
        actor=owner,
        idempotency_key="resume",
    )
    assert resumed["checkpoint"] == {"change_id": "chg_resume"}
    assert domain.claim_task("resumed-worker", actor=owner)["task_id"] == task["task_id"]


def test_outbox_independent_consumers_and_ack_lease(domain, owner):
    domain.create_task("fixture", {}, actor=owner, idempotency_key="task")
    batch = domain.claim_events("search", actor=owner)
    assert len(batch) == 1
    assert domain.claim_events("search", actor=owner) == []
    assert len(domain.claim_events("notifications", actor=owner)) == 1
    with pytest.raises(DomainError):
        domain.acknowledge_event(batch[0]["event_id"], "search", "wrong", actor=owner)
    domain.acknowledge_event(
        batch[0]["event_id"],
        "search",
        batch[0]["lease_token"],
        actor=owner,
        receipt={"indexed": True},
    )
    assert domain.claim_events("search", actor=owner) == []


def test_token_authentication_has_no_implicit_owner(owner):
    credential = "isolated-fixture-token-long-enough"
    auth = TokenAuthenticator({credential: owner})
    assert auth.authenticate("Bearer " + credential) == owner
    for value in (None, "Bearer wrong", "Basic ignored"):
        with pytest.raises(DomainError) as error:
            auth.authenticate(value)
        assert error.value.code == "UNAUTHENTICATED"


def test_sqlite_backup_includes_wal(domain, owner, tmp_path):
    task = domain.create_task(
        "fixture", {"checkpoint": "saved"}, actor=owner, idempotency_key="task"
    )
    destination = tmp_path / "backup" / "platform.db"
    domain.store.backup(destination)
    restored = DomainService(destination)
    try:
        assert restored.read_task(task["task_id"], actor=owner)["payload"] == {
            "checkpoint": "saved"
        }
    finally:
        restored.close()


def test_queued_cannot_claim_fake_success(domain, owner):
    task = domain.create_task("fixture", {}, actor=owner, idempotency_key="task")
    with pytest.raises(DomainError) as error:
        domain.update_task(
            task["task_id"], {"state": "succeeded"}, actor=owner, idempotency_key="fake"
        )
    assert error.value.code == "INVALID_STATE"
