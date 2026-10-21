"""Real ASGI receipts preserve the full domain labels without exposing raw storage."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
from starlette.applications import Starlette

from ahadiff.knowledge_platform.auth import classification_labels, classify_record
from ahadiff.knowledge_platform.intake import create_intake_routes

PREFIX = "/api/knowledge/v1/intakes"


@asynccontextmanager
async def client_for(service, actor):
    app = Starlette(routes=create_intake_routes(service.domain, intake_service=service))

    async def trusted_test_context(scope, receive, send):
        # Synthetic in-process trusted context, not a caller-selected HTTP identity.
        scope.setdefault("state", {}).update(actor=actor, trace_id="synthetic-envelope-trace")
        await app(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(trusted_test_context), base_url="http://testserver"
    ) as client:
        yield client


def assert_envelope(response, record=None, *, labels=None, status=200):
    assert response.status_code == status, response.text
    value = response.json()
    expected = classification_labels(record) if labels is None else frozenset(labels)
    assert value["data_classification"] == classify_record({"classification_labels": expected})
    assert value["classification_labels"] == sorted(expected)
    assert value["trace_id"] == "synthetic-envelope-trace"
    assert value["task_id"] == value["data"].get("task_id")
    for field in ("owner_id", "payload", "metadata", "input_hash", "external_message_id"):
        assert field not in value["data"]
    for field in ("cache_ref", "object_path", "raw_path", "content_base64"):
        assert f'"{field}"' not in response.text
    return value["data"]


@pytest.mark.parametrize("declared", ["public", "personal", "employer"])
def test_receive_replay_and_read_return_authoritative_safe_classified_receipt(
    service, actors, declared
):
    async def run():
        async with client_for(service, actors[0]) as client:
            body = {"text": "Synthetic source only.", "data_classification": declared}
            first = await client.post(PREFIX, json=body, headers={"Idempotency-Key": "receive"})
            record = service.domain.read_intake(first.json()["data"]["intake_id"], actor=actors[0])
            receipt = assert_envelope(first, record, status=202)
            assert receipt["receipt_id"] == receipt["intake_id"]
            assert receipt["publication"]["publication_eligible"] is False
            assert receipt["classification_labels"] == sorted(classification_labels(record))
            assert set(receipt["classification_labels"]) == {service.input_classification, declared}
            assert "Synthetic source only." not in first.text  # raw payload is not a receipt
            replay = await client.post(PREFIX, json=body, headers={"Idempotency-Key": "receive"})
            assert replay.json() == first.json()
            assert_envelope(await client.get(PREFIX + "/" + receipt["intake_id"]), record)

    asyncio.run(run())


def test_supplement_attachment_keep_and_discard_have_full_classified_envelopes(service, actors):
    async def run():
        async with client_for(service, actors[0]) as client:
            receipt = (
                await client.post(
                    PREFIX,
                    json={"text": "Original synthetic source."},
                    headers={"Idempotency-Key": "receive"},
                )
            ).json()["data"]
            path = PREFIX + "/" + receipt["intake_id"]
            pasted = await client.post(
                path + "/supplement",
                json={
                    "expected_preview_revision": 0,
                    "text": "Replacement synthetic source.",
                    "private_note": "Separate synthetic annotation.",
                },
                headers={"Idempotency-Key": "paste"},
            )
            data = assert_envelope(pasted, labels={"personal"})
            assert "Separate synthetic annotation." not in json.dumps(data["preview"]["document"])
            attached = await client.post(
                path + "/text-attachment",
                json={
                    "expected_preview_revision": 1,
                    "filename": "synthetic.txt",
                    "content_type": "text/plain",
                    "content_base64": base64.b64encode(b"Synthetic file bytes.").decode(),
                },
                headers={"Idempotency-Key": "attach"},
            )
            assert_envelope(attached, labels={"personal"})
            kept = await client.post(
                path + "/decision",
                json={"decision": "keep", "preview_revision": 2},
                headers={"Idempotency-Key": "keep"},
            )
            source = service.domain.get_source(kept.json()["data"]["source_id"], actor=actors[0])
            assert_envelope(kept, source)
            assert "descriptor" not in kept.json()["data"]
            assert kept.json()["data"]["state"] == "retained"
            other = (
                await client.post(
                    PREFIX,
                    json={"text": "Disposable synthetic source."},
                    headers={"Idempotency-Key": "other"},
                )
            ).json()["data"]
            discarded = await client.post(
                PREFIX + "/" + other["intake_id"] + "/decision",
                json={"decision": "discard"},
                headers={"Idempotency-Key": "discard"},
            )
            assert_envelope(discarded, labels={"personal"})

    asyncio.run(run())


def test_omitted_metadata_mixed_labels_survive_projection_and_list_filtering(service, actors):
    owner = actors[0]
    private = service.domain.create_intake(
        {
            "text": "Synthetic mixed source.",
            "data_classification": "public",
            "metadata": {"classification_labels": ["employer", "third_party_private"]},
        },
        actor=owner,
        idempotency_key="mixed",
    )
    public = service.domain.create_intake(
        {"text": "Synthetic public source.", "data_classification": "public"},
        actor=owner,
        idempotency_key="public",
    )
    public_reader = replace(actors[2], allowed_classifications=frozenset({"public"}))
    partial_reader = replace(actors[2], allowed_classifications=frozenset({"public", "employer"}))

    async def run():
        async with client_for(service, owner) as client:
            detail = await client.get(PREFIX + "/" + private["intake_id"])
            assert_envelope(detail, labels={"public", "employer", "third_party_private"})
            assert "metadata" not in detail.json()["data"]
            listing = await client.get(PREFIX)
            assert_envelope(listing, labels={"public", "employer", "third_party_private"})
            assert len(listing.json()["data"]["items"]) == 2
        for reader in (public_reader, partial_reader):
            async with client_for(service, reader) as client:
                denied = await client.get(PREFIX + "/" + private["intake_id"])
                assert denied.status_code == 403
                assert denied.json()["error"]["code"] == "CLASSIFICATION_FORBIDDEN"
                assert "data" not in denied.json() and "classification_labels" not in denied.json()
                visible = assert_envelope(await client.get(PREFIX), labels={"public"})
                assert [item["intake_id"] for item in visible["items"]] == [public["intake_id"]]
                empty = assert_envelope(await client.get(PREFIX + "?offset=1"), labels={"public"})
                assert empty == {"items": [], "has_more": False}

    asyncio.run(run())


@pytest.mark.parametrize("classification", ["secret", "unknown"])
def test_machine_cannot_read_secret_or_unknown_after_projection(service, actors, classification):
    record = service.domain.create_intake(
        {"text": "Synthetic forbidden source.", "data_classification": classification},
        actor=actors[0],
        idempotency_key="forbidden",
    )

    async def run():
        async with client_for(service, actors[0]) as client:
            assert_envelope(
                await client.get(PREFIX + "/" + record["intake_id"]), labels={classification}
            )
        async with client_for(service, actors[2]) as client:
            assert (await client.get(PREFIX + "/" + record["intake_id"])).status_code == 403
            assert_envelope(await client.get(PREFIX), labels={"public"})

    asyncio.run(run())


@pytest.mark.parametrize("identity", ["absent", "write_only", "other_owner"])
def test_envelope_does_not_bypass_identity_scope_or_ownership(service, actors, identity):
    item = service.receive(
        {"text": "Synthetic private source."}, actor=actors[0], idempotency_key="receive"
    )
    actor = {
        "absent": None,
        "write_only": replace(actors[2], scopes=frozenset({"knowledge:write"})),
        "other_owner": replace(actors[2], owner_id="other-synthetic-owner"),
    }[identity]

    async def run():
        async with client_for(service, actor) as client:
            response = await client.get(PREFIX + "/" + item["intake_id"])
            assert (
                response.status_code
                == {"absent": 401, "write_only": 403, "other_owner": 404}[identity]
            )
            assert "data" not in response.json() and "classification_labels" not in response.json()
            if identity != "other_owner":
                capabilities = await client.get(PREFIX + "/capabilities")
                assert capabilities.status_code == response.status_code

    asyncio.run(run())


def test_public_only_reader_can_read_empty_page_and_capability_metadata(service, actors):
    actor = replace(actors[2], allowed_classifications=frozenset({"public"}))

    async def run():
        async with client_for(service, actor) as client:
            assert_envelope(await client.get(PREFIX), labels={"public"})
            assert_envelope(await client.get(PREFIX + "/capabilities"), labels={"public"})

    asyncio.run(run())


def storage_snapshot(service):
    with service.domain.store.reading() as connection:
        ledger = tuple(connection.iterdump())
    files = {}
    for name, root in (("cache", service.cache.root), ("objects", service.domain.object_path)):
        files[name] = {
            str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")
        }
    return ledger, files


def write_request(service, owner, operation):
    if operation == "receive":
        return PREFIX, {"text": "Denied synthetic source."}
    receipt = service.receive(
        {"text": "Existing synthetic source."}, actor=owner, idempotency_key="seed"
    )
    service.supplement(
        receipt["intake_id"],
        {"expected_preview_revision": 0, "text": "Existing preview."},
        actor=owner,
        idempotency_key="seed-preview",
    )
    path = PREFIX + "/" + receipt["intake_id"]
    if operation == "supplement":
        return path + "/supplement", {"expected_preview_revision": 1, "text": "Denied replacement."}
    if operation == "attachment":
        return path + "/text-attachment", {
            "expected_preview_revision": 1,
            "filename": "synthetic.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode(b"Denied synthetic file bytes.").decode(),
        }
    return path + "/decision", {"decision": operation, "preview_revision": 1}


@pytest.mark.parametrize("operation", ["receive", "supplement", "attachment", "keep", "discard"])
def test_write_only_http_is_denied_without_any_ledger_cache_or_version_change(
    service, actors, operation
):
    path, body = write_request(service, actors[0], operation)
    actor = replace(actors[0], scopes=frozenset({"knowledge:write"}))
    before = storage_snapshot(service)

    async def run():
        async with client_for(service, actor) as client:
            for _ in range(2):
                response = await client.post(path, json=body, headers={"Idempotency-Key": "denied"})
                assert response.status_code == 403
                assert response.json()["error"]["code"] == "FORBIDDEN"
                # Includes all record revisions, tasks/leases, receipt/unique/event
                # rows, cache bytes/directories and retained objects, not just counts.
                assert storage_snapshot(service) == before

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["receive", "supplement", "attachment", "keep", "discard"])
def test_read_scope_is_checked_before_reading_any_post_body(
    service, actors, operation, monkeypatch
):
    from ahadiff.knowledge_platform.intake import routes

    path, _ = write_request(service, actors[0], operation)
    actor = replace(actors[0], scopes=frozenset({"knowledge:write"}))
    before = storage_snapshot(service)

    async def must_not_parse(_request):
        raise AssertionError("Unauthorized intake request reached body parsing")

    monkeypatch.setattr(routes, "_body", must_not_parse)

    async def run():
        async with client_for(service, actor) as client:
            response = await client.post(path, content=b"not-json")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"
            assert storage_snapshot(service) == before

    asyncio.run(run())
