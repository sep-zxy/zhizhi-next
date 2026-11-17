"""Real private temporary file-byte lifecycle, with synthetic files only."""

import asyncio
import base64
import hashlib
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


def payload(content=b"# Synthetic source\r\n\nActual file bytes.\n", **changes):
    return {
        "expected_preview_revision": 0,
        "filename": "synthetic.md",
        "content_type": "text/markdown",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "private_note": "Private synthetic annotation.",
        **changes,
    }


def received(service, actors):
    receipt = service.receive(
        {"text": "Original synthetic receipt."}, actor=actors[0], idempotency_key="receive"
    )
    return service.domain.read_intake(receipt["intake_id"], actor=actors[0])


def attach(service, actors, item, data=None, key="upload"):
    return service.attach_text(
        item["intake_id"], data or payload(), actor=actors[0], idempotency_key=key
    )


def test_actual_original_utf8_bytes_are_temporary_until_independent_keep(service, actors):
    content = b"\xef\xbb\xbf# UTF-8\r\n" + "合成正文。\n".encode()
    data = payload(content)
    item = received(service, actors)
    before_expiry = item["expires_at"]
    added = attach(service, actors, item, data)
    assert added["source_id"] is None and added["data_classification"] == "personal"
    assert added["expires_at"] == before_expiry and added["preview_revision"] == 1
    assert not list(service.domain.object_path.rglob("*.json"))
    document = added["preview"]["document"]
    assert document["body_text"].encode("utf-8") == content
    assert document["metadata"]["text_basis"] == "user_uploaded_text"
    metadata = added["preview"]["supplements"][0]["attachment"]
    assert metadata == {
        "filename": "synthetic.md",
        "content_type": "text/markdown",
        "byte_count": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    assert "content_base64" not in json.dumps(added["preview"])
    assert "Private synthetic annotation." not in json.dumps(document)
    assert attach(service, actors, item, data) == added
    cache = service.cache.read(item["intake_id"], added["preview"]["cache_ref"]["sha256"])
    assert base64.b64decode(cache["supplements"][0]["attachment"]["content_base64"]) == content
    source = service.keep(
        item["intake_id"], preview_revision=1, actor=actors[0], idempotency_key="keep"
    )
    raw = json.loads((service.domain.object_path / source["object_path"]).read_text())
    assert base64.b64decode(raw["supplements"][0]["attachment"]["content_base64"]) == content
    assert (
        service.domain.read_evidence({"source_id": source["source_id"]}, actor=actors[0])[
            "text"
        ].encode("utf-8")
        == content
    )
    assert not list(service.cache.root.rglob("*.json"))


@pytest.mark.parametrize(
    "changes",
    [
        {"filename": "../unsafe.txt"},
        {"filename": "C:\\private\\note.txt"},
        {"filename": "unsafe\u202etxt.md"},
        {"filename": "archive.zip"},
        {"filename": ".secret.txt"},
        {"filename": "note.txt", "content_type": "application/octet-stream"},
        {"filename": "note.txt", "content_type": "text/markdown"},
        {"content_type": "text/plain; charset=utf-8"},
        {"content_base64": "!!invalid!!"},
        {"content_base64": "Zg==\n"},
        {"content_base64": "Zh=="},
        {"raw_path": "/arbitrary/path"},
        {"data_classification": "public"},
        {"owner_id": "other"},
        {"expected_preview_revision": True},
        {"private_note": "x" * 10001},
    ],
)
def test_attachment_rejects_paths_types_encoding_scope_and_limits(service, actors, changes):
    item = received(service, actors)
    with pytest.raises((IntakeError, DomainError)):
        attach(service, actors, item, payload(**changes))
    assert service.domain.read_intake(item["intake_id"], actor=actors[0]) == item
    assert not list(service.cache.root.rglob("*.json"))


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"x" * 100001,
        b"\xff\xfeBinary",
        b"\x00binary",
        b"PK\x03\x04synthetic",
        b"%PDF-1.7 synthetic",
        b"MZsynthetic",
        b"\xef\xbb\xbf  \n",
    ],
    ids=["empty", "oversized", "non-utf8", "binary", "zip", "pdf", "executable", "bom-whitespace"],
)
def test_attachment_rejects_binary_archive_non_utf8_empty_and_large_bytes(service, actors, content):
    item = received(service, actors)
    with pytest.raises(IntakeError):
        attach(service, actors, item, payload(content))
    assert not list(service.cache.root.rglob("*.json"))


def test_maximum_bytes_valid_and_different_file_same_key_conflicts(service, actors):
    item = received(service, actors)
    data = payload(b"x" * 100000)
    first = attach(service, actors, item, data)
    assert first["preview"]["document"]["metadata"]["attachment"]["byte_count"] == 100000
    with pytest.raises(DomainError) as error:
        attach(service, actors, item, {**data, "filename": "different.md"})
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(DomainError) as error:
        attach(service, actors, item, data, key="new-stale-key")
    assert error.value.code == "BASE_STALE"


def test_file_bytes_survive_later_pasted_supplement_but_are_not_returned(service, actors):
    item = received(service, actors)
    first = attach(service, actors, item)
    second = service.supplement(
        item["intake_id"],
        {
            "expected_preview_revision": 1,
            "text": "Later synthetic pasted source",
            "private_note": "Later note",
        },
        actor=actors[0],
        idempotency_key="paste",
    )
    assert "content_base64" not in json.dumps(second["preview"])
    bundle = service.cache.read(item["intake_id"], second["preview"]["cache_ref"]["sha256"])
    assert bundle["supplements"][0]["attachment"]["content_base64"] == payload()["content_base64"]
    assert len(bundle["supplements"]) == 2
    assert first["expires_at"] == second["expires_at"]


def test_expire_removes_file_bytes_and_scrubs_idempotent_receipt(service, actors):
    item = received(service, actors)
    attach(service, actors, item)
    service.domain.update_intake(
        item["intake_id"],
        {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        actor=actors[0],
        idempotency_key="expire-time",
    )
    service.expire(item["intake_id"], actor=actors[0])
    assert not list(service.cache.root.rglob("*.json"))
    replay = attach(service, actors, item)
    assert replay["state"] == "expired" and replay["preview"] is None
    assert "Actual file bytes" not in json.dumps(replay)


@pytest.mark.parametrize("mode", ["running", "reconcile", "retained"])
def test_file_upload_uses_same_task_and_retention_gates(service, actors, mode):
    item = received(service, actors)
    if mode == "running":
        service.domain.claim_task("fixture-worker", actor=actors[1])
    elif mode == "reconcile":
        with service.domain.store.transaction() as connection:
            task = service.domain.store.get(connection, "task", item["task_id"], actors[0].owner_id)
            task.update(state="blocked", reconciliation_required=True)
            service.domain.store.put(connection, "task", task["task_id"], task)
    else:
        attach(service, actors, item)
        service.keep(item["intake_id"], preview_revision=1, actor=actors[0], idempotency_key="keep")
    before = service.domain.read_intake(item["intake_id"], actor=actors[0])
    before_task = service.domain.read_task(before["task_id"], actor=actors[0])
    with pytest.raises(DomainError):
        attach(service, actors, item, key="blocked-upload")
    assert service.domain.read_intake(item["intake_id"], actor=actors[0]) == before
    assert service.domain.read_task(before["task_id"], actor=actors[0]) == before_task


def test_attachment_http_is_bounded_authenticated_and_never_returns_bytes(service, actors):
    item = received(service, actors)
    token = "inert-text-attachment-owner-token"
    app = Starlette(
        routes=create_intake_routes(service.domain, intake_service=service),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware, authenticator=TokenAuthenticator({token: actors[0]})
            )
        ],
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            path = f"/api/knowledge/v1/intakes/{item['intake_id']}/text-attachment"
            assert (await client.post(path, json=payload())).status_code == 401
            client.headers.update({"Authorization": "Bearer " + token, "Idempotency-Key": "upload"})
            oversized = await client.post(path, content=b"x" * (512 * 1024 + 1))
            assert oversized.status_code == 413
            result = await client.post(path, json=payload())
            assert result.status_code == 200, result.text
            assert "content_base64" not in result.text and "cache_ref" not in result.text
            assert result.json()["data"]["preview"]["document"][
                "body_text"
            ].encode() == base64.b64decode(payload()["content_base64"])

    asyncio.run(run())


def test_route_fixture_policy_disables_upload_before_decoding_or_cache_write(service, actors):
    item = received(service, actors)
    token = "inert-text-attachment-fixture-token"
    app = Starlette(
        routes=create_intake_routes(
            service.domain, intake_service=service, allow_text_attachments=False
        ),
        middleware=[
            Middleware(
                DomainAuthenticationMiddleware, authenticator=TokenAuthenticator({token: actors[0]})
            )
        ],
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                f"/api/knowledge/v1/intakes/{item['intake_id']}/text-attachment",
                json={"content_base64": "intentionally-invalid"},
                headers={"Authorization": "Bearer " + token, "Idempotency-Key": "upload"},
            )
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "LOCAL_TEST_WRITES_DISABLED"

    asyncio.run(run())
    assert not list(service.cache.root.rglob("*.json"))
    assert service.domain.read_intake(item["intake_id"], actor=actors[0]) == item
