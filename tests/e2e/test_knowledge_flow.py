"""Integrated HTTP/SQLite/Git flows with an EXPLICIT fixture model.

These tests are T1 integration evidence, not live subscription/channel acceptance.
They must never be reported as completion of J01-J05 real external journeys.
"""

from __future__ import annotations

import asyncio
import secrets

import pytest
from starlette.testclient import TestClient

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.model import FixtureModelRunner
from ahadiff.knowledge_platform.workflow import WorkflowSettings


TEXT = "事件溯源将状态变化保存为事件。适用于需要审计和重放的系统，但需要处理事件版本迁移。"


class EvidenceFixtureRunner:
    """Return schema-checked deterministic fixtures, explicitly tagged fixture."""

    def __init__(self):
        self.calls = []

    async def run(self, task):
        self.calls.append(task)
        if task.schema_name == "intake_preview_v1":
            output = {"summary": "介绍事件溯源的架构特点、适用边界和迁移代价。"}
        elif task.schema_name == "knowledge_proposal_v1":
            source = task.payload["evidence"][0]["text"]
            output = {"no_change": False, "title": "事件溯源", "body": source,
                      "summary": "补充架构取舍", "risk_flags": [], "claims": [{
                          "text": source, "quote": source, "evidence_index": 0,
                          "relation": "add", "personal_expression": False}]}
        elif task.schema_name == "knowledge_review_v1":
            output = {"decision": "approve", "reason": "原始证据支持此架构说明",
                      "patch_hash": task.payload["patch_hash"], "risk_flags": [],
                      "checks": [{"evidence_index": 0,
                                  "quote": task.payload["evidence"][0]["text"],
                                  "supports_patch": True}]}
        else:
            raise AssertionError(f"Unexpected fixture request {task.schema_name}")
        return await FixtureModelRunner({task.schema_name: output}, development=True).run(task)


@pytest.fixture
def system(tmp_path):
    token = secrets.token_urlsafe(32)
    user = Actor("test-owner", "test-user", "user", frozenset({"*"}))
    runner = EvidenceFixtureRunner()
    app = create_application(tmp_path / "state", tokens={token: user}, model_runner=runner,
                             workflow_settings=WorkflowSettings(
                                 owner_id=user.owner_id, allowed_provider_ids=("fixture",),
                                 allow_fixture=True, auto_commit_enabled=True))
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + token
        yield app, client, user, runner


def post(client, path, body, key):
    return client.post("/api/knowledge/v1" + path, json=body,
                       headers={"Idempotency-Key": key})


def test_http_preview_keep_review_git_search(system):
    app, client, user, runner = system
    accepted = post(client, "/intakes", {"text": TEXT}, "receipt-1")
    assert accepted.status_code == 202, accepted.text
    intake_id = accepted.json()["data"]["intake_id"]
    assert app.state.domain.list_entries(actor=user) == []
    assert asyncio.run(app.state.workflow.run_once())
    preview = client.get("/api/knowledge/v1/intakes/" + intake_id)
    assert preview.status_code == 200, preview.text
    record = preview.json()["data"]
    assert record["preview"]["summary_state"] == "ready", record
    assert record["preview"]["model_fixture"] is True
    assert app.state.domain.list_entries(actor=user) == []
    kept = post(client, f"/intakes/{intake_id}/decision", {
        "decision": "keep", "preview_revision": record["preview_revision"]}, "keep-1")
    assert kept.status_code == 200, kept.text
    source = kept.json()["data"]
    assert source["visibility"] == "private"
    assert asyncio.run(app.state.workflow.run_once())
    task = app.state.domain.read_task(source["task_id"], actor=user)
    assert task["state"] == "succeeded", task
    entries = app.state.domain.list_entries(actor=user)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["commit_sha"] and entry["body"] == TEXT
    assert {task.purpose for task in runner.calls} == {"preview", "generate", "review"}
    found = client.get("/api/knowledge/v1/search", params={"q": "事件溯源"})
    assert found.status_code == 200, found.text
    assert found.json()["data"]["results"]
    reread = client.get("/api/knowledge/v1/entries/" + entry["entry_id"])
    assert reread.status_code == 200, reread.text
    assert reread.json()["data"]["body"] == TEXT
    repeat = post(client, f"/intakes/{intake_id}/decision", {
        "decision": "keep", "preview_revision": record["preview_revision"]}, "keep-1")
    assert repeat.status_code == 200
    assert repeat.json()["data"]["source_id"] == source["source_id"]
    assert len(app.state.domain.list_entries(actor=user)) == 1


def test_application_requires_authentication_and_denies_owner_in_payload(system):
    _, client, _, _ = system
    unauthenticated = client.get("/api/knowledge/v1/entries", headers={"Authorization": ""})
    assert unauthenticated.status_code == 401
    forged = post(client, "/intakes", {"text": TEXT, "owner_id": "other"}, "bad-owner")
    assert forged.status_code in {403, 422}


def test_unretained_source_never_becomes_formal_knowledge(system):
    app, client, user, _ = system
    receipt = post(client, "/intakes", {"text": TEXT}, "unkept").json()["data"]
    asyncio.run(app.state.workflow.run_once())
    assert app.state.domain.list_entries(actor=user) == []
    assert asyncio.run(app.state.workflow.run_once()) is False
    record = client.get("/api/knowledge/v1/intakes/" + receipt["intake_id"]).json()["data"]
    assert not record.get("source_id")
