"""Preview/keep boundary, private immutable RAW and cross-channel deduplication."""

from __future__ import annotations

import json

import pytest

from ahadiff.knowledge_platform.errors import DomainError


def preview(domain, owner, key="one"):
    intake = domain.create_intake(
        {"text": "temporary-private-body", "channel": "http"}, actor=owner, idempotency_key=key
    )
    return domain.update_intake(
        intake["intake_id"],
        {"state": "preview_ready", "preview": {"text": "temporary-private-body"}},
        actor=owner,
        idempotency_key=key + ":preview",
    )


def test_no_keep_means_no_permanent_source_or_object(domain, owner):
    item = preview(domain, owner)
    with domain.store.reading() as connection:
        assert (
            connection.execute("SELECT count(*) FROM kp_records WHERE kind='source'").fetchone()[0]
            == 0
        )
    assert list(domain.object_path.rglob("*")) == []
    assert item["source_id"] is None


def test_keep_duplicate_has_one_source_and_generate_task(domain, owner):
    item = preview(domain, owner)
    descriptor = {"raw_text": "actual frozen text", "preview_revision": item["preview_revision"]}
    first = domain.retain_source(item["intake_id"], descriptor, actor=owner, idempotency_key="keep")
    second = domain.retain_source(
        item["intake_id"], descriptor, actor=owner, idempotency_key="keep-again"
    )
    assert first == second
    assert first["visibility"] == "private"
    task = domain.read_task(first["task_id"], actor=owner)
    assert task["kind"] == "knowledge.generate"
    assert task["payload"]["evidence_refs"] == [{"source_id": first["source_id"]}]
    assert (
        domain.read_evidence({"source_id": first["source_id"]}, actor=owner)["text"]
        == "actual frozen text"
    )
    with domain.store.reading() as connection:
        assert (
            connection.execute("SELECT count(*) FROM kp_records WHERE kind='source'").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM kp_records WHERE kind='task'").fetchone()[0]
            == 1
        )


def test_agent_cannot_keep_and_preview_revision_must_match(domain, owner, generator):
    item = preview(domain, owner)
    with pytest.raises(DomainError) as error:
        domain.retain_source(
            item["intake_id"],
            {"raw_text": "text", "preview_revision": 1},
            actor=generator,
            idempotency_key="agent",
        )
    assert error.value.code == "FORBIDDEN"
    with pytest.raises(DomainError) as error:
        domain.retain_source(
            item["intake_id"],
            {"raw_text": "text", "preview_revision": 0},
            actor=owner,
            idempotency_key="stale",
        )
    assert error.value.code == "BASE_STALE"


def test_stale_preview_cannot_overwrite_current(domain, owner):
    item = preview(domain, owner)
    updated = domain.update_intake(
        item["intake_id"],
        {"preview": {"text": "new"}, "expected_preview_revision": 1},
        actor=owner,
        idempotency_key="preview2",
    )
    assert updated["preview_revision"] == 2
    with pytest.raises(DomainError) as error:
        domain.update_intake(
            item["intake_id"],
            {"preview": {"text": "late"}, "expected_preview_revision": 1},
            actor=owner,
            idempotency_key="late",
        )
    assert error.value.code == "BASE_STALE"


def test_discard_scrubs_content_even_from_replay_receipts(domain, owner):
    item = preview(domain, owner)
    domain.update_intake(
        item["intake_id"], {"state": "discarded"}, actor=owner, idempotency_key="discard"
    )
    with domain.store.reading() as connection:
        rows = connection.execute("SELECT response FROM kp_receipts").fetchall()
    assert "temporary-private-body" not in json.dumps([row[0] for row in rows])
    replay = domain.create_intake(
        {"text": "temporary-private-body", "channel": "http"}, actor=owner, idempotency_key="one"
    )
    assert replay["state"] == "discarded" and replay["preview"] is None


def test_raw_path_cannot_escape_controlled_cache(domain, owner, tmp_path):
    item = preview(domain, owner)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not read", encoding="utf-8")
    with pytest.raises(DomainError) as error:
        domain.retain_source(
            item["intake_id"],
            {"raw_path": str(outside), "preview_revision": 1},
            actor=owner,
            idempotency_key="outside",
        )
    assert error.value.code == "FORBIDDEN"
    link = domain.cache_path / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(DomainError):
        domain.retain_source(
            item["intake_id"],
            {"raw_path": str(link), "preview_revision": 1},
            actor=owner,
            idempotency_key="link",
        )


def test_content_identity_reuses_first_raw_and_task(domain, owner):
    extraction = {
        "platform": "douyin",
        "source_url": "https://example.invalid/video/1",
        "body_text": "stable content",
        "description": "stable description",
    }
    results = []
    for number in range(2):
        item = preview(domain, owner, str(number))
        results.append(
            domain.retain_source(
                item["intake_id"],
                {
                    "raw_text": json.dumps({"captured_at": number, "body": "stable content"}),
                    "preview_revision": 1,
                    "content_identity": "same-semantic-id",
                    "metadata": {"extraction": extraction},
                },
                actor=owner,
                idempotency_key=f"keep:{number}",
            )
        )
    assert results[0]["source_id"] == results[1]["source_id"]
    assert results[0]["task_id"] == results[1]["task_id"]
    assert (
        domain.read_evidence({"source_id": results[0]["source_id"]}, actor=owner)["text"]
        == "stable content"
    )


def test_channel_replay_owner_key_and_cursor_monotonic(domain, owner):
    payload = {"channel": "telegram", "external_message_id": "42", "text": "message"}
    first = domain.create_intake(payload, actor=owner, idempotency_key="first")
    assert (
        domain.create_intake(payload, actor=owner, idempotency_key="replay")["intake_id"]
        == first["intake_id"]
    )
    assert domain.get_channel_state("telegram", actor=owner) == {}
    assert domain.set_channel_state(
        "telegram", {"offset": 43}, actor=owner, idempotency_key="offset"
    ) == {"offset": 43}
    with pytest.raises(DomainError) as error:
        domain.set_channel_state(
            "telegram", {"offset": 42}, actor=owner, idempotency_key="old-offset"
        )
    assert error.value.code == "BASE_STALE"


def test_received_without_cache_is_expired_and_queued_preview_cancelled(domain, owner, source):
    intake = domain.create_intake(
        {"text": "never-fetched-private"}, actor=owner, idempotency_key="not-fetched"
    )
    task = domain.create_task(
        "intake.preview",
        {"intake_id": intake["intake_id"]},
        actor=owner,
        idempotency_key="preview-job",
    )
    domain.update_intake(
        intake["intake_id"],
        {"expires_at": "2000-01-01T08:00:00+08:00", "task_id": task["task_id"]},
        actor=owner,
        idempotency_key="expire-time",
    )
    assert [item["intake_id"] for item in domain.list_expired_intakes(actor=owner)] == [
        intake["intake_id"]
    ]
    result = domain.expire_intakes(actor=owner)
    assert result[0]["state"] == "expired" and result[0]["payload"] == {}
    assert domain.read_task(task["task_id"], actor=owner)["state"] == "cancelled"
    assert domain.get_source(source["source_id"], actor=owner)["source_id"] == source["source_id"]
    assert domain.list_expired_intakes(actor=owner) == []
    with domain.store.reading() as connection:
        all_records = [row[0] for row in connection.execute("SELECT data FROM kp_records")]
        all_receipts = [row[0] for row in connection.execute("SELECT response FROM kp_receipts")]
    assert "never-fetched-private" not in json.dumps(all_records + all_receipts)
