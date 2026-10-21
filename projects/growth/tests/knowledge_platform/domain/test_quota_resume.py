"""Durable quota recovery is bounded, scoped and never grants auth or human approval."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.errors import DomainError


def waiting(
    domain,
    owner,
    key,
    *,
    state="waiting_quota",
    retry="2000-01-01T09:00:00+09:00",
    classification="personal",
    attempts=5,
):
    task = domain.create_task(
        key, {"data_classification": classification}, actor=owner, idempotency_key=key
    )
    claim = domain.claim_task(key, actor=owner, kinds=[key])
    return domain.update_task(
        task["task_id"],
        {
            "state": state,
            "lease_token": claim["lease_token"],
            "max_attempts": attempts,
            "result": {"model": {"retry_after": retry}},
            "checkpoint": {"change_id": "chg_saved"},
        },
        actor=owner,
        idempotency_key="wait:" + key,
    )


def test_due_quota_is_transactionally_resumed_once_and_checkpoint_survives(domain, owner, writer):
    first = waiting(domain, owner, "due-first")
    second = waiting(domain, owner, "due-second")
    other = DomainService(domain.store.path)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda service: service.resume_due_quota_tasks(actor=writer, limit=1),
                    [domain, other],
                )
            )
        assert {row["task_id"] for batch in results for row in batch} == {
            first["task_id"],
            second["task_id"],
        }
        assert domain.resume_due_quota_tasks(actor=writer) == []
        assert len(domain.list_events(actor=owner, types=["task.resumed.v1"])) == 2
        resumed = domain.claim_task("restart", actor=writer, kinds=["due-first"])
        assert resumed["attempt"] == 2
        assert resumed["checkpoint"] == {"change_id": "chg_saved"}
    finally:
        other.close()


@pytest.mark.parametrize("state", ["waiting_auth", "waiting_user", "blocked", "waiting_review"])
def test_quota_maintenance_never_revives_other_wait_reasons(domain, owner, writer, state):
    task = waiting(domain, owner, "not-quota", state=state)
    assert domain.resume_due_quota_tasks(actor=writer) == []
    assert domain.read_task(task["task_id"], actor=owner)["state"] == state


@pytest.mark.parametrize(
    "retry", [None, "invalid", "2000-01-01T00:00:00", "2999-01-01T00:00:00Z", 0]
)
def test_missing_naive_invalid_or_future_deadline_remains_waiting(domain, owner, writer, retry):
    waiting(domain, owner, "bad-deadline", retry=retry)
    assert domain.resume_due_quota_tasks(actor=writer) == []


def test_maintenance_respects_owner_classification_attempt_limit_and_service_kind(
    domain, owner, writer
):
    waiting(domain, owner, "restricted", classification="employer")
    waiting(domain, owner, "exhausted", attempts=1)
    stranger = Actor("other-owner", "other-user", "user", frozenset({"*"}))
    waiting(domain, stranger, "other-owner")
    assert domain.resume_due_quota_tasks(actor=writer) == []
    with pytest.raises(DomainError) as error:
        domain.resume_due_quota_tasks(actor=owner)
    assert error.value.code == "FORBIDDEN"


def test_real_process_restart_auto_resumes_review_without_regenerating(domain, owner, source):
    """T1: real process/persistence/worker, deliberately fixture model, never live quota."""
    script = r"""
import asyncio, json, sys
from datetime import UTC, datetime
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.model import FixtureModelRunner
from ahadiff.knowledge_platform.model.contracts import ModelResult
from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings

service = DomainService(sys.argv[1])
task_id, stage = sys.argv[2:4]
user = Actor("owner-one", "quota-fixture-human", "user", frozenset({"*"}))
text = "Evidence says addition preserves history."
if stage == "quota":
    outputs = {
        "knowledge_proposal_v1": {
            "no_change": False, "title": "Quota restart evidence", "body": text,
            "summary": "Preserved proposal", "risk_flags": [],
            "claims": [{"text": text, "quote": text, "evidence_index": 0,
                        "relation": "add", "personal_expression": False}],
        },
        "knowledge_review_v1": ModelResult(
            task_id="fixture", status="waiting_quota", error_code="FIXTURE_QUOTA",
            retry_after=datetime(2000, 1, 1, tzinfo=UTC),
        ),
    }
else:
    saved = service.read_task(task_id, actor=user)
    change = service.get_change(saved["checkpoint"]["change_id"], actor=user)
    outputs = {
        "knowledge_review_v1": {
            "decision": "approve", "reason": "Fixture original evidence supports the patch",
            "patch_hash": change["patch_hash"], "risk_flags": [],
            "checks": [{"evidence_index": 0, "quote": text, "supports_patch": True}],
        },
    }
runner = FixtureModelRunner(outputs, development=True)
workflow = KnowledgeWorkflow(service, runner, WorkflowSettings(
    owner_id="owner-one", allow_fixture=True, auto_commit_enabled=True, poll_seconds=0.01,
))

async def run():
    if stage == "quota":
        assert await workflow.run_once()
        return
    stop = asyncio.Event()
    async def observe():
        for _ in range(500):
            await asyncio.sleep(0.01)
            current = service.read_task(task_id, actor=user)
            if current["state"] in {"succeeded", "failed", "blocked", "partial"}:
                stop.set()
                return
        stop.set()
        raise AssertionError("Automatic quota resume did not reach a terminal observation")
    await asyncio.wait_for(asyncio.gather(workflow.run_forever(stop), observe()), 10)

try:
    asyncio.run(run())
    task = service.read_task(task_id, actor=user)
    print(json.dumps({"state": task["state"], "checkpoint": task.get("checkpoint"),
                      "result": task.get("result"), "attempt": task["attempt"],
                      "calls": [call.schema_name for call in runner.calls]}))
finally:
    service.close()
"""

    def run(stage):
        child = subprocess.run(
            [sys.executable, "-c", script, str(domain.store.path), source["task_id"], stage],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            check=False,
        )
        assert child.returncode == 0, child.stderr
        return json.loads(child.stdout)

    paused = run("quota")
    assert paused["state"] == "waiting_quota"
    assert paused["calls"] == ["knowledge_proposal_v1", "knowledge_review_v1"]
    assert paused["checkpoint"]["change_id"]
    assert paused["result"]["model"]["retry_after"].startswith("2000-")
    resumed = run("resume")
    assert resumed["state"] == "succeeded"
    assert resumed["checkpoint"] == paused["checkpoint"]
    assert resumed["attempt"] == paused["attempt"] + 1
    assert resumed["calls"] == ["knowledge_review_v1"]
    replayed = run("restart-again")
    assert replayed["state"] == "succeeded"
    assert replayed["calls"] == []
    assert replayed["checkpoint"] == paused["checkpoint"]
    assert len(domain.list_events(actor=owner, types=["change.proposed.v1"])) == 1
    assert len(domain.list_events(actor=owner, types=["task.resumed.v1"])) == 1
    assert len(domain.list_events(actor=owner, types=["knowledge.committed.v1"])) == 1
    assert domain.writer._git("rev-list", "--count", domain.writer.ref).strip() == b"1"
    entry_id = resumed["result"]["entry_id"]
    assert domain.get_entry(entry_id, actor=owner)["revision"] == 1
