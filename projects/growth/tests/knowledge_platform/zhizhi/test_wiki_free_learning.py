"""Synthetic words, real domain/ASGI/SQLite/Writer; no live model or Windows claim."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from starlette.testclient import TestClient
from test_real_domain_bridge import make_entry
from test_real_domain_bridge import system as _real_system
from test_understanding_bridge import EvidenceFixture

from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.learning.backend import BackendError
from ahadiff.knowledge_platform.learning.desktop import configure_desktop_backend
from ahadiff.knowledge_platform.learning.model_bridge import (
    ModelBridgeConfiguration,
    configure_model_bridge,
)
from ahadiff.knowledge_platform.learning.understanding import note_hash
from ahadiff.knowledge_platform.learning.wiki_learning import (
    create_learning_context,
    list_learning_contexts,
    read_learning_context,
    read_learning_submissions,
    save_learning_note,
    submit_learning_understanding,
)
from ahadiff.knowledge_platform.model import DisabledModelRunner, FixtureModelRunner
from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings
from ahadiff.serve import ServeState, create_app

TEXT = "  合成原话：正式知识与来源证据分开；精确版本不能偷偷换成最新。\r\n"
BASE = "/api/growth/local/knowledge/entry-learning-contexts"


@pytest.fixture(name="system")
def real_domain_system(tmp_path, monkeypatch):
    yield from _real_system.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def context(system, tmp_path):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer)
    path = tmp_path / "desktop/growth.sqlite"
    payload = {
        "request_id": str(uuid4()),
        "entry_id": entry["entry_id"],
        "revision": 1,
        "expected_content_hash": entry["content_hash"],
    }
    return path, payload, entry, backend


def create(context):
    path, payload, _, _ = context
    return asyncio.run(create_learning_context(path, payload))


def save(context, item, text=TEXT, *, request_id=None):
    return asyncio.run(
        save_learning_note(
            context[0],
            item["context_id"],
            {
                "request_id": request_id or str(uuid4()),
                "expected_context_version": item["context_version"],
                "note_text": text,
            },
        )
    )


def submit(context, item):
    return asyncio.run(
        submit_learning_understanding(
            context[0],
            item["context_id"],
            context_version=item["context_version"],
            note_sha256=item["note_sha256"],
        )
    )


def read_submission(context, item, submission):
    return asyncio.run(
        read_learning_submissions(
            context[0],
            item["context_id"],
            submission_id=submission["submission_id"],
        )
    )


def test_create_explicit_source_context_never_manufactures_engineering_or_mastery(context):
    path, _, entry, backend = context
    assert not path.exists()
    item = create(context)
    assert item["source_kind"] == "wiki_entry" and item["state"] == "reading"
    assert item["context_version"] == 1 and item["note_text"] == ""
    assert item["entry"]["body"] == entry["body"]
    assert item["content_hash"] == entry["content_hash"]
    assert not {"card_id", "owner_id", "path", "completed_at", "score"} & set(item)
    assert backend.outbox.records() == []
    with GrowthLocalRepository(path) as ledger:
        for table in (
            "projects",
            "opportunities",
            "growth_tasks",
            "knowledge_cards",
            "card_learning_progress",
        ):
            assert ledger.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        columns = {
            row["name"]
            for row in ledger.connection.execute("PRAGMA table_info(wiki_entry_learning_contexts)")
        }
        assert not {"body", "source_text", "owner_id", "token", "card_id"} & columns
        assert ledger.connection.execute("PRAGMA user_version").fetchone()[0] == 33
        assert not ledger.connection.execute("PRAGMA foreign_key_check").fetchall()


def test_concurrent_create_restart_unique_revision_and_bound_request(context):
    path, payload, _, _ = context

    async def double():
        return await asyncio.gather(
            create_learning_context(path, payload), create_learning_context(path, payload)
        )

    first, second = asyncio.run(double())
    assert first == second == create(context)
    assert (
        asyncio.run(
            create_learning_context(
                path,
                {
                    **payload,
                    "request_id": str(uuid4()),
                },
            )
        )["context_id"]
        == first["context_id"]
    )
    items = asyncio.run(list_learning_contexts(path))
    assert len(items["items"]) == 1 and items["has_more"] is False
    assert not {"entry", "note_text", "owner_id", "authority_key"} & set(items["items"][0])
    with pytest.raises(BackendError) as failure:
        asyncio.run(create_learning_context(path, {**payload, "expected_content_hash": "a" * 64}))
    assert failure.value.code == "CONTENT_CONFLICT"


def test_original_note_cas_idempotent_replay_and_noop_leave_source_unchanged(context, system):
    original = create(context)
    key = str(uuid4())
    changed = save(context, original, request_id=key)
    assert changed["context_version"] == 2 and changed["note_text"] == TEXT
    assert changed["note_sha256"] == note_hash(TEXT)
    assert save(context, original, request_id=key) == changed
    same = save(context, changed)
    assert same["context_version"] == 2
    with pytest.raises(BackendError) as failure:
        save(context, original, "new words")
    assert failure.value.code == "CONTEXT_VERSION_STALE"
    with pytest.raises(BackendError) as failure:
        save(context, original, "changed payload", request_id=key)
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"
    domain, owner, _, _ = system
    assert domain.get_entry(context[2]["entry_id"], actor=owner)["body"] == context[2]["body"]
    assert context[3].outbox.records() == []


def test_two_simultaneous_note_writes_only_one_cas_wins(context):
    item = create(context)

    async def race():
        return await asyncio.gather(
            *[
                save_learning_note(
                    context[0],
                    item["context_id"],
                    {
                        "request_id": str(uuid4()),
                        "expected_context_version": 1,
                        "note_text": text,
                    },
                )
                for text in ("first", "second")
            ],
            return_exceptions=True,
        )

    results = asyncio.run(race())
    assert sum(isinstance(result, dict) for result in results) == 1
    errors = [result for result in results if isinstance(result, BackendError)]
    assert len(errors) == 1 and errors[0].code == "CONTEXT_VERSION_STALE"


def test_history_stays_exact_when_formal_head_changes(context, system):
    item = create(context)
    domain, owner, writer, _ = system
    old = context[2]
    change = domain.propose_change(
        {
            "entry_id": old["entry_id"],
            "base_revision": 1,
            "title": old["title"],
            "body": old["body"] + "后来的正式版本。\n",
            "evidence_refs": old["evidence_refs"],
        },
        actor=owner,
        idempotency_key="next-source",
    )
    reviewer = Actor(
        owner.owner_id, "next-reviewer", "agent", frozenset({"knowledge:read", "knowledge:review"})
    )
    domain.record_review(
        change["change_id"],
        {
            "decision": "approve",
            "expected_patch_hash": change["patch_hash"],
        },
        actor=reviewer,
        idempotency_key="next-review",
    )
    new = domain.apply_change(change["change_id"], actor=writer, idempotency_key="next-write")
    restored = asyncio.run(read_learning_context(context[0], item["context_id"]))
    assert restored["entry"]["body"] == old["body"] and restored["revision"] == 1
    newer = asyncio.run(
        create_learning_context(
            context[0],
            {
                **context[1],
                "request_id": str(uuid4()),
                "revision": 2,
                "expected_content_hash": new["content_hash"],
            },
        )
    )
    assert newer["context_id"] != item["context_id"]
    listed = asyncio.run(list_learning_contexts(context[0], limit=1))
    assert len(listed["items"]) == 1 and listed["has_more"]


def test_ungraded_original_words_queue_private_once_without_two_question_gate(context, system):
    item = save(context, create(context))
    first = submit(context, item)
    assert first["delivery_state"] == "acknowledged" and first["task"]["state"] == "queued"
    assert first["context_id"] == item["context_id"] and first["context_version"] == 2
    assert first["user_text"] == TEXT and first["change"] is None and "card_id" not in first
    assert submit(context, item)["submission_id"] == first["submission_id"]
    domain, owner, _, backend = system
    outcome = domain.get_learning_outcome(first["outcome_id"], actor=owner)["payload"]
    assert outcome["result"] == {"practice_kind": "open_response", "graded": False}
    meta = outcome["metadata"]
    assert meta["source_entry_id"] == item["entry_id"]
    assert meta["suggested_entry_id"] != item["entry_id"]
    assert meta["evidence_classification"] == "personal"
    assert "personal" in meta["classification_labels"]
    assert not {"card_id", "opportunity_id", "completed_at", "score"} & set(meta)
    assert len(backend.outbox.records()) == 1
    assert len(domain.list_events(actor=owner, types=["learning.outcome.submitted.v1"])) == 2
    assert len(domain.list_entries(actor=owner)) == 1


@pytest.mark.parametrize("version,hash_value", [(1, note_hash(TEXT)), (2, note_hash(TEXT.strip()))])
def test_stale_snapshot_never_enqueues(context, version, hash_value):
    item = save(context, create(context))
    with pytest.raises(BackendError) as failure:
        asyncio.run(
            submit_learning_understanding(
                context[0], item["context_id"], context_version=version, note_sha256=hash_value
            )
        )
    assert failure.value.code == "UNDERSTANDING_STALE"
    assert context[3].outbox.records() == []


@pytest.mark.parametrize("text", ["", " \r\n"])
def test_empty_original_words_cannot_submit(context, text):
    item = save(context, create(context), text)
    with pytest.raises(BackendError) as failure:
        submit(context, item)
    assert failure.value.code == "INVALID_INPUT" and context[3].outbox.records() == []


def test_pending_snapshot_manual_retry_after_edit_and_get_never_delivers(context, system):
    item = save(context, create(context))
    backend = context[3]
    original = backend.client
    methods = []

    async def fail_only_post(request):
        methods.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("untrusted-private-exception")
        return await original.send(request)

    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(fail_only_post))
    first = submit(context, item)
    assert first["delivery_state"] == "pending" and first["error"]["retryable"] is True
    assert "untrusted-private-exception" not in json.dumps(first)
    changed = save(context, item, "新版本本人理解")
    before = methods.count("POST")
    restored = read_submission(context, changed, first)
    assert restored["user_text"] == TEXT and methods.count("POST") == before
    asyncio.run(backend.client.aclose())
    backend.client = original
    delivered = submit(context, item)
    assert delivered["submission_id"] == first["submission_id"]
    assert delivered["user_text"] == TEXT and delivered["delivery_state"] == "acknowledged"
    assert (
        asyncio.run(read_learning_context(context[0], item["context_id"]))["note_text"]
        == "新版本本人理解"
    )
    assert len(backend.outbox.records()) == 1


def test_remote_authorization_revocation_hides_original_words_and_prevents_writes(context):
    item = save(context, create(context))
    first = submit(context, item)
    backend = context[3]
    backend.token_provider = lambda: "synthetic-unauthorized-token"
    for operation in (
        lambda: asyncio.run(read_learning_context(context[0], item["context_id"])),
        lambda: asyncio.run(list_learning_contexts(context[0])),
        lambda: read_submission(context, item, first),
        lambda: save(context, item, "should not save"),
        lambda: submit(context, item),
    ):
        with pytest.raises(BackendError) as failure:
            operation()
        assert failure.value.status_code == 401
    assert len(backend.outbox.records()) == 1


def test_missing_service_does_not_initialize_ledger_or_fallback(context):
    configure_desktop_backend(None, device_id="stable-device")
    with pytest.raises(BackendError) as failure:
        create(context)
    assert failure.value.code == "WAITING_CONFIGURATION"
    assert not context[0].exists() and context[3].outbox.records() == []


def test_note_changed_during_remote_authorization_is_rechecked_before_snapshot(
    context, monkeypatch
):
    item = save(context, create(context))
    backend = context[3]
    original = backend.read_entry_revision

    async def concurrent_edit(entry_id, revision):
        result = await original(entry_id, revision)
        with GrowthLocalRepository(context[0]) as ledger, ledger.connection as db:
            db.execute(
                "UPDATE wiki_entry_learning_contexts SET note_text=?,note_sha256=?,"
                "context_version=context_version+1",
                ("new", note_hash("new")),
            )
        return result

    monkeypatch.setattr(backend, "read_entry_revision", concurrent_edit)
    with pytest.raises(BackendError) as failure:
        submit(context, item)
    assert failure.value.code == "UNDERSTANDING_STALE"
    assert backend.outbox.records() == []


@pytest.mark.parametrize("title_collision", [False, True])
def test_fixture_generation_review_writer_create_separate_private_understanding(
    context, system, title_collision
):
    item = save(context, create(context))
    first = submit(context, item)
    domain, owner, _, _ = system

    class TitleCollisionFixture(EvidenceFixture):
        async def run(self, task):
            result = await super().run(task)
            if title_collision and task.purpose == "generate":
                return await FixtureModelRunner(
                    {
                        task.schema_name: {
                            **result.output,
                            "title": context[2]["title"],
                        }
                    },
                    development=True,
                ).run(task)
            return result

    model = TitleCollisionFixture()
    workflow = KnowledgeWorkflow(
        domain,
        model,
        WorkflowSettings(
            owner_id=owner.owner_id,
            allowed_provider_ids=("fixture",),
            allow_fixture=True,
            auto_commit_enabled=True,
        ),
    )
    # make_entry creates an unrelated source outcome task; only run our task.
    for pending in domain.list_tasks(actor=owner)["items"]:
        if pending["task_id"] != first["task_id"] and pending["state"] == "queued":
            domain.control_task(
                pending["task_id"],
                "cancel",
                pending["revision"],
                actor=owner,
                idempotency_key="isolate-" + pending["task_id"],
            )
    assert asyncio.run(workflow.run_once())
    done = read_submission(context, item, first)
    assert done["change"]["state"] == "committed", done
    assert done["change"]["committed_revision"] == 1
    assert done["change"]["entry_id"] != item["entry_id"]
    assert done["change"]["candidate_markdown"] == TEXT
    assert done["change"]["original_markdown"] == ""
    formal = domain.get_entry(done["change"]["entry_id"], actor=owner)
    assert formal["visibility"] == "private" and formal["publication_eligible"] is False
    assert domain.get_entry(item["entry_id"], actor=owner)["body"] == context[2]["body"]
    assert [call.purpose for call in model.calls] == ["generate", "review"]
    assert model.calls[0].payload["existing_catalogue"] == []
    assert model.calls[0].payload["existing_entry"] is None
    assert [part["text"] for part in model.calls[0].payload["evidence"]] == [TEXT]
    assert asyncio.run(read_learning_context(context[0], item["context_id"]))["state"] == "reading"
    # A later saved explanation updates only this separate understanding target.
    changed = save(context, item, TEXT + "另一次本人补充，仍不更改来源。")
    next_submission = submit(context, changed)
    assert asyncio.run(workflow.run_once())
    next_view = read_submission(context, changed, next_submission)
    assert next_view["change"]["entry_id"] == done["change"]["entry_id"]
    assert next_view["change"]["original_markdown"] == TEXT
    assert next_view["change"]["committed_revision"] == 2
    assert domain.get_entry(item["entry_id"], actor=owner)["body"] == context[2]["body"]


def test_disabled_model_reports_blocked_not_written(context, system):
    item = save(context, create(context))
    first = submit(context, item)
    domain, owner, _, _ = system
    for pending in domain.list_tasks(actor=owner)["items"]:
        if pending["task_id"] != first["task_id"] and pending["state"] == "queued":
            domain.control_task(
                pending["task_id"],
                "cancel",
                pending["revision"],
                actor=owner,
                idempotency_key="isolate-" + pending["task_id"],
            )
    workflow = KnowledgeWorkflow(
        domain, DisabledModelRunner(), WorkflowSettings(owner_id=owner.owner_id)
    )
    assert asyncio.run(workflow.run_once())
    done = read_submission(context, item, first)
    assert done["task"]["state"] == "blocked" and done["change"] is None
    assert len(domain.list_entries(actor=owner)) == 1


def test_http_auth_payload_validation_and_safe_receipts(context):
    token = "synthetic-local-viewer-token"
    client = TestClient(
        create_app(ServeState(state_dir=context[0].parent, token=token)),
        base_url="http://localhost:8765",
    )
    headers = {"Origin": "http://localhost:8765", "X-AhaDiff-Token": token}
    assert client.get(BASE).status_code == 401
    assert (
        client.post(BASE, json=context[1], headers={"Origin": headers["Origin"]}).status_code == 401
    )
    for extra in (
        {"owner_id": "other"},
        {"revision": True},
        {"revision": 0},
        {"body": "not allowed"},
        {"expected_content_hash": "A" * 64},
    ):
        assert client.post(BASE, json={**context[1], **extra}, headers=headers).status_code == 422
    result = client.post(BASE, json=context[1], headers=headers)
    assert result.status_code == 200
    item = result.json()
    for suffix in ("?limit=1&limit=2", "?offset=-1", "?limit=101", "?owner_id=other"):
        assert client.get(BASE + suffix, headers=headers).status_code == 422
    note_url = BASE + f"/{item['context_id']}/note"
    body = {"request_id": str(uuid4()), "expected_context_version": 1, "note_text": TEXT}
    note = client.post(note_url, json=body, headers=headers)
    assert note.status_code == 200 and note.json()["context_version"] == 2
    submit_url = BASE + f"/{item['context_id']}/understanding/submissions"
    response = client.post(
        submit_url, json={"context_version": 2, "note_sha256": note_hash(TEXT)}, headers=headers
    )
    assert response.status_code == 202 and response.json()["change"] is None
    assert client.get(submit_url, headers=headers).json()["items"][0] == response.json()
    assert client.get(BASE + f"/{uuid4()}", headers=headers).status_code == 404


def test_private_labels_are_not_downgraded_by_public_desktop_configuration(context):
    configure_model_bridge(
        None, ModelBridgeConfiguration(enabled=True, input_classification="public")
    )
    item = save(context, create(context))
    submit(context, item)
    metadata = context[3].outbox.records()[0]["payload"]["metadata"]
    assert metadata["evidence_classification"] == "personal"
    assert set(metadata["classification_labels"]) == {"public", "personal"}


@pytest.mark.parametrize(
    "patch",
    [
        {"request_id": "not-a-uuid"},
        {"entry_id": "../secret"},
        {"entry_id": "valid\n"},
        {"revision": True},
        {"revision": 2**53},
        {"expected_content_hash": "a" * 63 + "\n"},
        {"owner_id": "injected"},
    ],
)
def test_invalid_create_never_initializes_ledger(context, patch):
    with pytest.raises(BackendError) as failure:
        asyncio.run(create_learning_context(context[0], {**context[1], **patch}))
    assert failure.value.code == "INVALID_INPUT"
    assert not context[0].exists()


def test_corrupt_local_words_never_escape(context):
    item = save(context, create(context))
    with GrowthLocalRepository(context[0]) as ledger, ledger.connection as db:
        db.execute("UPDATE wiki_entry_learning_contexts SET note_text='unbound secret'")
    with pytest.raises(BackendError) as failure:
        submit(context, item)
    assert failure.value.code == "CONTEXT_CORRUPT"
    assert context[3].outbox.records() == []


@pytest.mark.parametrize("text", ["x" * 20001, "\ud800"])
def test_invalid_note_preserves_prior_words_and_version(context, text):
    item = save(context, create(context))
    with pytest.raises(BackendError) as failure:
        save(context, item, text)
    assert failure.value.code == "INVALID_INPUT"
    assert asyncio.run(read_learning_context(context[0], item["context_id"])) == item


def test_device_rebinding_cannot_read_prior_context_or_snapshot(context):
    item = save(context, create(context))
    configure_desktop_backend(context[3], device_id="different-device")
    assert asyncio.run(list_learning_contexts(context[0])) == {"items": [], "has_more": False}
    with pytest.raises(BackendError) as failure:
        asyncio.run(read_learning_context(context[0], item["context_id"]))
    assert failure.value.code == "NOT_FOUND"


def test_forced_source_target_is_rejected_before_any_model_call(context, system):
    from ahadiff.knowledge_platform.generation.service import GenerationService

    item = save(context, create(context))
    submitted = submit(context, item)
    domain, owner, _, _ = system
    fixture = EvidenceFixture()
    generator = GenerationService(
        domain, fixture, allowed_provider_ids=("fixture",), allow_fixture=True
    )
    actor = Actor(
        owner.owner_id, "generator", "agent", frozenset({"knowledge:read", "knowledge:write"})
    )
    result = asyncio.run(
        generator.generate(
            [{"learning_outcome_id": submitted["outcome_id"]}],
            task_id="synthetic-target-attack",
            actor=actor,
            idempotency_key="synthetic-target-attack",
            entry_id=item["entry_id"],
        )
    )
    assert result["reason"] == "INDEPENDENT_UNDERSTANDING_TARGET_REQUIRED"
    assert fixture.calls == []
