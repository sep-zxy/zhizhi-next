"""Explicit exact-revision attachment using synthetic facts and real ASGI/SQLite/Git."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from starlette.testclient import TestClient
from test_real_domain_bridge import make_entry
from test_real_domain_bridge import system as _real_system

from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.knowledge_platform.learning.backend import BackendError
from ahadiff.knowledge_platform.learning.desktop import configure_desktop_backend
from ahadiff.knowledge_platform.learning.entry_context import (
    attach_context,
    preview_context,
    read_contexts,
)
from ahadiff.serve import ServeState, create_app


@pytest.fixture(name="system")
def reused_domain_system(tmp_path, monkeypatch):
    yield from _real_system.__wrapped__(tmp_path, monkeypatch)


def seed_card(path):
    """Isolated synthetic relational fixture with all real foreign keys enabled."""
    project, feature, snapshot, analysis, opportunity, topic, concept, card = [
        str(uuid4()) for _ in range(8)
    ]
    stamp = "2026-10-21T00:00:00+00:00"
    with GrowthLocalRepository(path) as ledger:
        with ledger.connection as db:
            db.execute("INSERT INTO projects VALUES (?,?,?)", (project, "合成项目", stamp))
            db.execute(
                "INSERT INTO features VALUES (?,?,?,?,?,?,?)",
                (feature, project, "合成片段", "main", "a" * 40, "active", stamp),
            )
            db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                (snapshot, feature, "a" * 40, "b" * 40, "c" * 64, "d" * 64, "{}", "v1", "", stamp),
            )
            db.execute(
                "INSERT INTO analysis_runs(analysis_id,snapshot_id,request_id,input_fingerprint,"
                "mode,status,upstream_run_id,error,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    analysis,
                    snapshot,
                    str(uuid4()),
                    "e" * 64,
                    "dry_run",
                    "completed",
                    None,
                    None,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO opportunity_batches VALUES (?,?,?,?,?,?,?)",
                (analysis, "f" * 64, "replay", None, None, None, stamp),
            )
            db.execute(
                "INSERT INTO opportunities(opportunity_id,analysis_id,ordinal,title,reason,"
                "learning_goal,source_refs_json,estimated_minutes,uncertainties_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    opportunity,
                    analysis,
                    0,
                    "事件溯源",
                    "合成证据",
                    "理解版本",
                    "[]",
                    4,
                    "[]",
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO growth_topics VALUES (?,?,?,?)", (topic, "合成主题", "active", stamp)
            )
            db.execute(
                "INSERT INTO growth_tasks(task_id,opportunity_id,topic_id,question,followups_json,"
                "estimated_minutes,source_refs_json,progress,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    card,
                    opportunity,
                    topic,
                    "如何理解？",
                    "[]",
                    4,
                    "[]",
                    "in_progress",
                    stamp,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO knowledge_concepts VALUES (?,?,?,?,?)",
                (concept, "既有学习卡", concept, stamp, stamp),
            )
            db.execute(
                "INSERT INTO knowledge_cards(card_id,learning_goal,back_answer,back_explanation,"
                "card_version,created_at,updated_at,concept_id) VALUES (?,?,?,?,?,?,?,?)",
                (card, "理解版本", "合成解释", "合成边界", 1, stamp, stamp, concept),
            )
            db.execute(
                "INSERT INTO card_learning_progress(card_id,card_version,stage,option_drafts_json,"
                "note_text,updated_at) VALUES (?,?,?,?,?,?)",
                (card, 1, "front", "{}", "本人原话不会被正式正文替换", stamp),
            )
        assert not ledger.connection.execute("PRAGMA foreign_key_check").fetchall()
    return card


@pytest.fixture
def context(system, tmp_path):
    domain, owner, writer, backend = system
    entry = make_entry(domain, owner, writer)
    path = tmp_path / "desktop/growth.sqlite"
    card_id = seed_card(path)
    payload = {
        "request_id": str(uuid4()),
        "entry_id": entry["entry_id"],
        "revision": entry["revision"],
        "expected_content_hash": entry["content_hash"],
        "target_card_id": card_id,
        "expected_card_version": 1,
    }
    return path, payload, entry, backend


def test_read_only_preview_has_no_association_or_new_card_and_safe_projection(context):
    path, payload, entry, _ = context
    view = asyncio.run(preview_context(path, entry["entry_id"], 1))
    assert view["entry"]["body"] == entry["body"]
    assert view["entry"]["data_classification"] == "personal"
    assert view["cards"] == [
        {"card_id": payload["target_card_id"], "card_version": 1, "title": "既有学习卡"}
    ]
    assert not ({"owner_id", "path", "commit_sha", "metadata"} & set(view["entry"]))
    with GrowthLocalRepository(path) as ledger:
        assert (
            ledger.connection.execute("SELECT count(*) FROM knowledge_entry_contexts").fetchone()[0]
            == 0
        )
        assert ledger.connection.execute("SELECT count(*) FROM knowledge_cards").fetchone()[0] == 1


def test_double_submit_restart_and_new_request_return_one_reference_not_completion(context):
    path, payload, entry, backend = context

    async def submit_twice():
        return await asyncio.gather(attach_context(path, payload), attach_context(path, payload))

    first, second = asyncio.run(submit_twice())
    assert first == second and first["state"] == "linked"
    assert asyncio.run(attach_context(path, {**payload, "request_id": str(uuid4())})) == first
    view = asyncio.run(read_contexts(path, payload["target_card_id"]))
    assert view["items"][0]["context_id"] == first["context_id"]
    assert view["items"][0]["entry"]["body"] == entry["body"]
    assert backend.outbox.records() == []
    with GrowthLocalRepository(path) as ledger:
        assert (
            ledger.connection.execute("SELECT count(*) FROM knowledge_entry_contexts").fetchone()[0]
            == 1
        )
        progress = ledger.connection.execute("SELECT * FROM card_learning_progress").fetchone()
        assert progress["stage"] == "front" and progress["completed_at"] is None
        assert progress["note_text"] == "本人原话不会被正式正文替换"
        columns = {
            row["name"]
            for row in ledger.connection.execute("PRAGMA table_info(knowledge_entry_contexts)")
        }
        assert not columns & {"body", "title", "note_text", "owner_id", "token"}


@pytest.mark.parametrize(
    "change,code",
    [
        ({"expected_card_version": 2}, "CARD_VERSION_STALE"),
        ({"expected_content_hash": "0" * 64}, "CONTENT_CONFLICT"),
        ({"target_card_id": str(uuid4())}, "NOT_FOUND"),
    ],
)
def test_stale_hash_version_and_missing_card_never_write(context, change, code):
    path, payload, _, _ = context
    with pytest.raises(BackendError) as failure:
        asyncio.run(attach_context(path, {**payload, **change}))
    assert failure.value.code == code
    with GrowthLocalRepository(path) as ledger:
        assert (
            ledger.connection.execute("SELECT count(*) FROM knowledge_entry_contexts").fetchone()[0]
            == 0
        )


def test_changed_request_conflicts_but_exact_replay_survives_card_revision(context):
    path, payload, _, _ = context
    first = asyncio.run(attach_context(path, payload))
    with GrowthLocalRepository(path) as ledger, ledger.connection:
        ledger.connection.execute("UPDATE knowledge_cards SET card_version=2")
    assert asyncio.run(attach_context(path, payload)) == first
    with pytest.raises(BackendError) as failure:
        asyncio.run(attach_context(path, {**payload, "expected_card_version": 2}))
    assert failure.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "failure", ["wrong_revision", "wrong_owner", "bad_hash", "deleted", "denied"]
)
def test_exact_revision_and_owner_are_rechecked_no_cached_body_on_failure(
    context, monkeypatch, failure
):
    path, payload, entry, backend = context
    asyncio.run(attach_context(path, payload))

    async def replacement(entry_id, revision):
        assert revision == 1 and entry_id == entry["entry_id"]
        if failure == "denied":
            raise BackendError("FORBIDDEN", "raw-private-error", False, 403)
        value = dict(entry)
        if failure == "wrong_revision":
            value["revision"] = 2
        if failure == "wrong_owner":
            value["owner_id"] = "another-owner"
        if failure == "bad_hash":
            value["content_hash"] = "0" * 64
        if failure == "deleted":
            value["deleted"] = True
        return {"data": value}

    monkeypatch.setattr(backend, "read_entry_revision", replacement)
    if failure == "wrong_owner":
        assert asyncio.run(read_contexts(path, payload["target_card_id"]))["items"] == []
    else:
        with pytest.raises(BackendError):
            asyncio.run(read_contexts(path, payload["target_card_id"]))


def test_no_service_or_auth_does_not_fabricate_context(context):
    path, payload, _, backend = context
    configure_desktop_backend(None, device_id="stable-device")
    with pytest.raises(BackendError) as failure:
        asyncio.run(attach_context(path, payload))
    assert failure.value.code == "WAITING_CONFIGURATION"
    configure_desktop_backend(backend, device_id="stable-device")
    old = backend.token_provider
    backend.token_provider = lambda: "synthetic-invalid-token-not-authorized"
    try:
        with pytest.raises(BackendError) as failure:
            asyncio.run(preview_context(path, payload["entry_id"], 1))
        assert failure.value.status_code == 401
    finally:
        backend.token_provider = old


def test_http_auth_exact_payload_conflict_and_fixed_error(context):
    path, payload, entry, _ = context
    token = "synthetic-local-viewer-token"
    client = TestClient(
        create_app(ServeState(state_dir=path.parent, token=token)), base_url="http://localhost:8765"
    )
    headers = {"Origin": "http://localhost:8765", "X-AhaDiff-Token": token}
    base = "/api/growth/local/knowledge"
    query = f"?entry_id={entry['entry_id']}&revision=1"
    assert client.get(base + "/entry-context" + query).status_code == 401
    assert (
        client.post(
            base + "/entry-contexts", json=payload, headers={"Origin": headers["Origin"]}
        ).status_code
        == 401
    )
    for extra in [
        {"owner_id": "other"},
        {"body": "not allowed"},
        {"revision": True},
        {"expected_card_version": True},
    ]:
        assert (
            client.post(
                base + "/entry-contexts", headers=headers, json={**payload, **extra}
            ).status_code
            == 422
        )
    for invalid in ["?entry_id=x", query + "&revision=2", query + "&latest=true"]:
        assert client.get(base + "/entry-context" + invalid, headers=headers).status_code == 422
    assert client.get(base + "/entry-context" + query, headers=headers).status_code == 200
    linked = client.post(base + "/entry-contexts", headers=headers, json=payload)
    assert linked.status_code == 200 and linked.json()["state"] == "linked"
    result = client.get(
        base + f"/cards/{payload['target_card_id']}/entry-contexts", headers=headers
    )
    assert result.status_code == 200 and result.json()["items"][0]["entry"]["revision"] == 1
    changed = client.post(
        base + "/entry-contexts", headers=headers, json={**payload, "expected_card_version": 2}
    )
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_empty_cards_stays_empty_and_does_not_create_a_card(context, tmp_path):
    _, _, entry, _ = context
    path = tmp_path / "empty.sqlite"
    assert asyncio.run(preview_context(path, entry["entry_id"], 1))["cards"] == []
    with GrowthLocalRepository(path) as ledger:
        assert ledger.connection.execute("SELECT count(*) FROM knowledge_cards").fetchone()[0] == 0


def test_real_formal_head_advances_but_link_preserves_historical_revision(context, system):
    from ahadiff.knowledge_platform.auth import Actor

    path, payload, entry, _ = context
    domain, owner, writer, _ = system
    asyncio.run(attach_context(path, payload))
    change = domain.propose_change(
        {
            "entry_id": entry["entry_id"],
            "base_revision": 1,
            "title": entry["title"],
            "body": entry["body"] + "版本迁移也需要单独审查。\n",
            "evidence_refs": entry["evidence_refs"],
        },
        actor=owner,
        idempotency_key="new-revision",
    )
    reviewer = Actor(
        owner.owner_id,
        "independent-revision-reviewer",
        "agent",
        frozenset({"knowledge:read", "knowledge:review"}),
    )
    domain.record_review(
        change["change_id"],
        {"decision": "approve", "expected_patch_hash": change["patch_hash"]},
        actor=reviewer,
        idempotency_key="new-review",
    )
    new = domain.apply_change(change["change_id"], actor=writer, idempotency_key="new-apply")
    assert new["revision"] == 2 and new["body"] != entry["body"]
    read = asyncio.run(read_contexts(path, payload["target_card_id"]))
    assert read["items"][0]["entry"]["revision"] == 1
    assert read["items"][0]["entry"]["body"] == entry["body"]
    assert asyncio.run(preview_context(path, entry["entry_id"], 2))["entry"]["body"] == new["body"]


def test_archived_or_dismissed_target_is_not_silently_reactivated(context):
    path, payload, entry, _ = context
    with GrowthLocalRepository(path) as ledger, ledger.connection:
        ledger.connection.execute("UPDATE growth_topics SET status='archived'")
    assert asyncio.run(preview_context(path, entry["entry_id"], 1))["cards"] == []
    with pytest.raises(BackendError) as failure:
        asyncio.run(attach_context(path, payload))
    assert failure.value.code == "NOT_FOUND"


def test_configuration_rebinding_during_remote_read_never_attaches(context, monkeypatch):
    path, payload, _, backend = context
    original = backend.read_entry_revision

    async def rebound(entry_id, revision):
        value = await original(entry_id, revision)
        configure_desktop_backend(backend, device_id="another-device")
        return value

    monkeypatch.setattr(backend, "read_entry_revision", rebound)
    with pytest.raises(BackendError) as failure:
        asyncio.run(attach_context(path, payload))
    assert failure.value.code == "CONFIGURATION_CHANGED"
    with GrowthLocalRepository(path) as ledger:
        assert (
            ledger.connection.execute("SELECT count(*) FROM knowledge_entry_contexts").fetchone()[0]
            == 0
        )
