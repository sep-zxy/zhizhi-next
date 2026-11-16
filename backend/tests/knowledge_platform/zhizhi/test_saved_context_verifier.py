"""Read-only synthetic cross-process acceptance verification, without model calls."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_wiki_free_learning import TEXT, create, save, submit
from test_wiki_free_learning import context as _context
from test_wiki_free_learning import real_domain_system as _system

from ahadiff.growth.local import GrowthLocalRepository
from ahadiff.knowledge_platform.learning.entry_context import context_authority_key
from ahadiff.knowledge_platform.learning.wiki_learning import verify_saved_learning_outcome


@pytest.fixture(name="system")
def real_system(tmp_path, monkeypatch):
    yield from _system.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture(name="context")
def real_context(system, tmp_path):
    return _context.__wrapped__(system, tmp_path)


@pytest.fixture
def snapshot(context, system):
    item = save(context, create(context))
    submit(context, item)
    facts = context[3].outbox.records()[0]["payload"]
    return (
        item,
        facts,
        {
            "backend_url": context[3].base_url,
            "device_id": "stable-device",
            "owner_id": system[1].owner_id,
        },
    )


def test_current_and_historic_saved_note_proven_without_any_mutation(context, snapshot):
    item, facts, binding = snapshot
    before = context[0].read_bytes()
    assert verify_saved_learning_outcome(context[0], facts, **binding)
    assert context[0].read_bytes() == before
    save(context, item, "之后保存的另一版本人原话")
    updated = context[0].read_bytes()
    assert verify_saved_learning_outcome(context[0], facts, **binding)
    assert context[0].read_bytes() == updated
    assert facts["user_text"] == TEXT
    assert facts["metadata"]["authority_key"] == context_authority_key(
        **{
            "base_url": binding["backend_url"],
            "device_id": binding["device_id"],
            "owner_id": binding["owner_id"],
        }
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_id", "00000000-0000-0000-0000-000000000000"),
        ("context_version", 99),
        ("note_sha256", "a" * 64),
        ("authority_key", "b" * 64),
        ("source_entry_id", "other"),
        ("source_revision", 2),
        ("source_revision", True),
        ("source_content_hash", "c" * 64),
        ("suggested_entry_id", "other"),
        ("submission_kind", "open_understanding"),
        ("source_kind", "engineering"),
    ],
)
def test_forged_metadata_does_not_prove_a_context(context, snapshot, field, value):
    _, facts, binding = snapshot
    changed = copy.deepcopy(facts)
    changed["metadata"][field] = value
    assert not verify_saved_learning_outcome(context[0], changed, **binding)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_text", "fabricated words"),
        ("device_id", "other"),
        ("session_id", "other"),
        ("entry_id", "source"),
        ("version", True),
        ("version", 2),
        ("result", {"practice_kind": "open_response", "graded": 0}),
        ("result", {"practice_kind": "open_response", "graded": False, "score": 1}),
    ],
)
def test_forged_facts_are_rejected(context, snapshot, field, value):
    _, facts, binding = snapshot
    assert not verify_saved_learning_outcome(context[0], {**facts, field: value}, **binding)


@pytest.mark.parametrize("field", ["backend_url", "device_id", "owner_id"])
def test_server_binding_cannot_be_substituted(context, snapshot, field):
    _, facts, binding = snapshot
    assert not verify_saved_learning_outcome(context[0], facts, **{**binding, field: "other"})


def test_missing_database_never_creates_parent_or_initializes_schema(tmp_path):
    path = tmp_path / "missing" / "growth.sqlite"
    assert not verify_saved_learning_outcome(
        path, {}, backend_url="http://127.0.0.1:8766", device_id="synthetic", owner_id="synthetic"
    )
    assert not path.parent.exists()


def test_corrupt_or_future_schema_is_denied_without_repair(context, snapshot):
    _, facts, binding = snapshot
    with GrowthLocalRepository(context[0]) as ledger:
        ledger.connection.execute("PRAGMA user_version=34")
    before = context[0].read_bytes()
    assert not verify_saved_learning_outcome(context[0], facts, **binding)
    assert context[0].read_bytes() == before


def test_redirected_database_parent_and_sidecar_are_denied(context, snapshot, monkeypatch):
    _, facts, binding = snapshot
    cls = type(context[0])
    original = cls.lstat
    for redirected in (context[0], context[0].parent, context[0].with_name("growth.sqlite-wal")):

        def redirected_info(self, target=redirected):
            if self == target:
                return SimpleNamespace(st_mode=0o100600, st_file_attributes=0x400)
            return original(self)

        with monkeypatch.context() as patch:
            patch.setattr(cls, "lstat", redirected_info)
            assert not verify_saved_learning_outcome(context[0], facts, **binding)


def test_missing_historical_receipt_cannot_be_replaced_by_metadata(context, snapshot):
    item, facts, binding = snapshot
    save(context, item, "new words")
    with GrowthLocalRepository(context[0]) as ledger, ledger.connection as db:
        db.execute("DELETE FROM wiki_entry_learning_requests WHERE operation='note'")
    assert not verify_saved_learning_outcome(context[0], facts, **binding)
