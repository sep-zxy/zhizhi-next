"""Actual isolated domain/cache/ASGI supplement contract; no network/model E2E."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware

from ahadiff.knowledge_platform.auth import DomainAuthenticationMiddleware, TokenAuthenticator
from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.intake import create_intake_routes
from ahadiff.knowledge_platform.intake.contracts import IntakeError


def receive(service, actors, *, key="receive", ready=True, classification="public"):
    receipt = service.receive(
        {"text": "Original synthetic text.", "data_classification": classification},
        actor=actors[0],
        idempotency_key=key,
    )
    if ready:
        asyncio.run(service.process_next(actor=actors[1]))
    return service.domain.read_intake(receipt["intake_id"], actor=actors[0])


def supplement(
    service,
    actors,
    item,
    *,
    key="supplement",
    text="Added synthetic text.",
    note="Private synthetic note.",
):
    return service.supplement(
        item["intake_id"],
        {"expected_preview_revision": item["preview_revision"], "text": text, "private_note": note},
        actor=actors[0],
        idempotency_key=key,
    )


def test_supplement_keeps_original_separates_note_and_never_retains_before_keep(service, actors):
    item = receive(service, actors)
    added = supplement(service, actors, item)
    assert added["state"] == "partial" and added["source_id"] is None
    assert added["preview_revision"] == item["preview_revision"] + 1
    assert added["data_classification"] == "personal"
    preview = added["preview"]
    assert preview["document"]["body_text"] == "Added synthetic text."
    assert preview["document"]["body_complete"] is False
    assert preview["original_document"]["body_text"] == "Original synthetic text."
    assert preview["private_note"] == "Private synthetic note."
    assert "Private synthetic note." not in json.dumps(preview["document"])
    assert preview["summary"] is None
    assert not list(service.domain.object_path.rglob("*.json"))
    replay = supplement(service, actors, item)
    assert replay == added
    with pytest.raises(IntakeError) as error:
        service.keep(
            item["intake_id"],
            preview_revision=item["preview_revision"],
            actor=actors[0],
            idempotency_key="stale-keep",
        )
    assert error.value.code == "BASE_STALE"
    source = service.keep(
        item["intake_id"],
        preview_revision=added["preview_revision"],
        actor=actors[0],
        idempotency_key="keep",
    )
    evidence = service.domain.read_evidence({"source_id": source["source_id"]}, actor=actors[0])
    assert evidence["text"] == "Added synthetic text."
    raw = json.loads((service.domain.object_path / source["object_path"]).read_text())
    assert raw["original_bundle"]["document"]["body_text"] == "Original synthetic text."
    assert raw["supplements"][0]["private_note"] == "Private synthetic note."
    assert source["data_classification"] == "personal"
    assert not list(service.cache.root.rglob("*.json"))


def test_supplement_does_not_lose_notes_through_body_semantic_dedup(service, actors):
    sources = []
    for index in range(2):
        item = receive(service, actors, key=f"receive-{index}")
        added = supplement(
            service, actors, item, key=f"supplement-{index}", note=f"Private note {index}"
        )
        sources.append(
            service.keep(
                item["intake_id"],
                preview_revision=added["preview_revision"],
                actor=actors[0],
                idempotency_key=f"keep-{index}",
            )
        )
    assert sources[0]["source_id"] != sources[1]["source_id"]
    for index, source in enumerate(sources):
        raw = json.loads((service.domain.object_path / source["object_path"]).read_text())
        assert raw["supplements"][0]["private_note"] == f"Private note {index}"


def test_supplement_zero_preview_cancels_queued_parser_without_network(service, actors):
    item = receive(service, actors, ready=False)
    assert item["preview_revision"] == 0
    added = supplement(service, actors, item)
    assert added["preview_revision"] == 1
    assert service.domain.read_task(item["task_id"], actor=actors[0])["state"] == "cancelled"
    assert asyncio.run(service.process_next(actor=actors[1])) is None
    assert supplement(service, actors, item) == added


def test_supplement_preserves_stronger_classification_and_history(service, actors):
    item = receive(service, actors, classification="secret", ready=False)
    first = supplement(service, actors, item)
    second = supplement(
        service, actors, first, key="second", text="Second addition", note="Second note"
    )
    assert second["data_classification"] == "secret"
    assert {"personal", "secret"} <= set(second["classification_labels"])
    assert second["preview"]["original_text"] == "Original synthetic text."
    assert len(second["preview"]["supplements"]) == 2
    with pytest.raises(DomainError) as error:
        supplement(service, actors, item, key="stale")
    assert error.value.code == "BASE_STALE"
    with pytest.raises(DomainError) as error:
        supplement(service, actors, item, text="Different request")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("condition", ["running", "expired", "discarded", "retained"])
def test_supplement_lifecycle_rejects_without_new_cache(service, actors, condition):
    item = receive(service, actors, ready=condition != "running")
    if condition == "running":
        task = service.domain.claim_task("worker", actor=actors[1])
    elif condition == "expired":
        service.domain.update_intake(
            item["intake_id"],
            {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            actor=actors[0],
            idempotency_key="expire-time",
        )
    elif condition == "discarded":
        service.discard(item["intake_id"], actor=actors[0], idempotency_key="discard")
    else:
        service.keep(
            item["intake_id"],
            preview_revision=item["preview_revision"],
            actor=actors[0],
            idempotency_key="keep",
        )
    before = list(service.cache.root.rglob("*.json"))
    with pytest.raises(DomainError):
        supplement(service, actors, item)
    assert list(service.cache.root.rglob("*.json")) == before
    if condition == "running":
        assert (
            service.domain.read_task(task["task_id"], actor=actors[0])["lease_token"]
            == task["lease_token"]
        )


@pytest.mark.parametrize(
    "extra",
    [
        {"raw_path": "/anywhere"},
        {"attachment_refs": ["x"]},
        {"data_classification": "public"},
        {"text": " "},
        {"expected_preview_revision": True},
        {"text": "x" * 100001},
    ],
)
def test_supplement_rejects_unsafe_or_unbounded_payload(service, actors, extra):
    item = receive(service, actors, ready=False)
    body = {"expected_preview_revision": 0, "text": "synthetic", **extra}
    with pytest.raises(DomainError):
        service.supplement(item["intake_id"], body, actor=actors[0], idempotency_key="invalid")
    assert not list(service.cache.root.rglob("*.json"))


def test_supplement_http_owner_only_safe_response_and_expiry_receipt_purge(service, actors):
    item = receive(service, actors, ready=False)
    app = Starlette(
        routes=create_intake_routes(service.domain, intake_service=service),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware,
                authenticator=TokenAuthenticator(
                    {
                        "inert-supplement-owner-token": actors[0],
                        "inert-supplement-agent-token": actors[2],
                    }
                ),
            )
        ],
    )
    body = {
        "expected_preview_revision": 0,
        "text": "Synthetic evidence",
        "private_note": "Synthetic private note",
    }

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            path = f"/api/knowledge/v1/intakes/{item['intake_id']}/supplement"
            assert (await client.post(path, json=body)).status_code == 401
            assert (
                await client.post(
                    path,
                    json=body,
                    headers={
                        "Authorization": "Bearer inert-supplement-agent-token",
                        "Idempotency-Key": "agent",
                    },
                )
            ).status_code == 403
            result = await client.post(
                path,
                json=body,
                headers={
                    "Authorization": "Bearer inert-supplement-owner-token",
                    "Idempotency-Key": "supplement",
                },
            )
            assert result.status_code == 200, result.text
            assert "cache_ref" not in result.text and "raw_path" not in result.text

    asyncio.run(run())
    service.discard(item["intake_id"], actor=actors[0], idempotency_key="discard")
    replay = service.supplement(
        item["intake_id"], body, actor=actors[0], idempotency_key="supplement"
    )
    assert replay["state"] == "discarded" and replay["preview"] is None


def test_supplement_rejects_invalid_unicode_without_cache(service, actors):
    item = receive(service, actors, ready=False)
    with pytest.raises(DomainError) as error:
        supplement(service, actors, item, text="\ud800")
    assert error.value.status_code == 422
    assert not list(service.cache.root.rglob("*.json"))


def test_concurrent_supplement_and_restart_replay_do_not_duplicate_cache(service, actors):
    from concurrent.futures import ThreadPoolExecutor

    from ahadiff.knowledge_platform.intake.service import IntakeService

    item = receive(service, actors, ready=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: supplement(service, actors, item), range(2)))
    assert results[0] == results[1]
    assert len(list(service.cache.root.rglob("*.json"))) == 1
    restarted = IntakeService(service.domain)
    assert supplement(restarted, actors, item) == results[0]
    assert restarted.domain.read_intake(item["intake_id"], actor=actors[0])["preview_revision"] == 1


def test_supplement_history_bound_does_not_destroy_original(service, actors):
    item = receive(service, actors, ready=False)
    for index in range(20):
        item = supplement(service, actors, item, key=f"addition-{index}", text=f"Synthetic {index}")
    with pytest.raises(IntakeError) as error:
        supplement(service, actors, item, key="too-many")
    assert error.value.code == "SUPPLEMENT_LIMIT"
    after = service.domain.read_intake(item["intake_id"], actor=actors[0])
    assert after["preview_revision"] == 20
    assert after["preview"]["original_text"] == "Original synthetic text."
    assert len(after["preview"]["supplements"]) == 20


def test_supplement_cannot_cancel_unreconciled_preview_work(service, actors):
    item = receive(service, actors, ready=False)
    with service.domain.store.transaction() as connection:
        task = service.domain.store.get(connection, "task", item["task_id"], actors[0].owner_id)
        task.update(
            state="blocked", reconciliation_required=True, checkpoint={"stage": "uncertain"}
        )
        service.domain.store.put(connection, "task", task["task_id"], task)
    before = service.domain.read_task(item["task_id"], actor=actors[0])
    with pytest.raises(DomainError) as error:
        supplement(service, actors, item)
    assert error.value.code == "RECONCILIATION_REQUIRED"
    assert service.domain.read_task(item["task_id"], actor=actors[0]) == before
    assert service.domain.read_intake(item["intake_id"], actor=actors[0]) == item
    assert not list(service.cache.root.rglob("*.json"))
