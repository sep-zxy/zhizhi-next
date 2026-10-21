"""Synthetic public HTTP bodies, actual parser/cache/domain; never a live publication."""

import asyncio
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from starlette.applications import Starlette

from ahadiff.knowledge_platform.extractors.pages import PublicPageExtractor
from ahadiff.knowledge_platform.intake import create_intake_routes
from ahadiff.knowledge_platform.intake.contracts import IntakeError
from ahadiff.knowledge_platform.intake.normalize import normalize_share
from ahadiff.knowledge_platform.intake.publication import status_projection, status_text
from ahadiff.knowledge_platform.intake.service import IntakeService
from ahadiff.knowledge_platform.media.external_document import external_document_manifest
from ahadiff.knowledge_platform.media.fetch import FetchResponse, SafeFetcher

BODY = "Synthetic technical knowledge: a queue processes one item at a time."
HTML = f"<article>{BODY}</article>".encode()
URL = "https://example.com/articles/queues"
NOTE = "Synthetic private note: my account is fixture-user-17 and my personal plan is hidden."


def manifest_for(*, url=URL, content=HTML, headers=None, labels=frozenset({"personal"}), **patch):
    response = FetchResponse(url, 200, headers or {"content-type": "text/html"}, content)
    document = PublicPageExtractor().extract(
        normalize_share(url=url), url=url, content=content, content_type=response.content_type
    )
    return external_document_manifest(
        response, replace(document, **patch), fetcher=SafeFetcher(), envelope_labels=labels
    )


def test_manifest_is_exact_body_not_a_privacy_or_copyright_approval():
    manifest = manifest_for()
    assert manifest["body_text"] == BODY
    assert manifest["content_hash"] == hashlib.sha256(BODY.encode()).hexdigest()
    assert manifest["privacy_assessment"] == "not_assessed"
    assert manifest["context_separated"] is True
    assert manifest["authenticated"] is False
    assert manifest["publicly_accessible"] is True
    assert manifest["content_kind"] == "external_article"
    assert manifest["observed_restrictions"] == []
    assert not {"rights", "classification", "publication_eligible", "title", "author"} & set(
        manifest
    )


@pytest.mark.parametrize(
    "url",
    [
        URL + "?id=17",
        URL + "?unrecognized_capability=synthetic",
        "https://example.com/private/article",
        "https://example.com/%70rivate/article",
        "https://example.com/password/synthetic",
        "https://example.com/share/A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
        "http://example.com/articles/queues",
    ],
)
def test_capability_or_ambiguous_provenance_is_not_brokered(url):
    assert manifest_for(url=url) is None


@pytest.mark.parametrize(
    "content,headers",
    [
        (HTML, {"content-type": "text/html", "x-robots-tag": "noai"}),
        (HTML, {"content-type": "text/html", "cache-control": "private, max-age=0"}),
        (HTML + b'<input type="password">', None),
        (HTML + b'<meta name="robots" content="noindex,nosnippet">', None),
        (HTML + b'<script type="application/ld+json">{"isAccessibleForFree":false}</script>', None),
        (b"<title>Metadata only</title>", None),
    ],
)
def test_explicit_restrictions_and_missing_body_do_not_create_candidate(content, headers):
    assert manifest_for(content=content, headers=headers) is None


@pytest.mark.parametrize("classification", ["employer", "third_party_private", "secret", "unknown"])
def test_protected_envelope_cannot_be_downgraded_by_public_fetch(classification):
    assert manifest_for(labels=frozenset({"personal", classification})) is None


def test_metadata_or_partial_transcript_does_not_become_external_article():
    assert manifest_for(state="partial", body_complete=False) is None
    assert manifest_for(metadata={"text_basis": "local_asr"}) is None
    assert manifest_for(warnings=("Synthetic incomplete source warning",)) is None
    assert manifest_for(body_text="x" * 100_001) is None
    assert manifest_for(body_text="Synthetic undecodable \ufffd source") is None


def test_anonymous_body_is_not_preclassified_as_nonpersonal():
    body = b"<article>Synthetic public biography contains a personal medical anecdote.</article>"
    manifest = manifest_for(content=body)
    assert manifest is not None
    assert manifest["privacy_assessment"] == "not_assessed"
    assert "data_classification" not in manifest and "publication_eligible" not in manifest


def test_capability_on_redirect_hop_cannot_be_hidden_by_clean_final_url():
    response = FetchResponse(
        URL,
        200,
        {"content-type": "text/html"},
        HTML,
        redirects=("https://example.com/start?token=synthetic-secret",),
    )
    document = PublicPageExtractor().extract(
        normalize_share(url=URL), url=URL, content=HTML, content_type="text/html"
    )
    assert (
        external_document_manifest(
            response, document, fetcher=SafeFetcher(), envelope_labels=frozenset({"personal"})
        )
        is None
    )


def transport():
    async def resolver(host, port):
        return ["93.184.216.34"]

    async def request(url, address, *, policy, timeout):
        return FetchResponse(url, 200, {"content-type": "text/html"}, HTML)

    return SafeFetcher(resolver=resolver, transport=request)


def test_private_note_never_reaches_parser_or_actual_domain_document_broker(service, actors):
    class InspectParser(PublicPageExtractor):
        def extract(self, share, **kwargs):
            assert share.original_text == ""
            assert NOTE not in repr(share)
            return super().extract(share, **kwargs)

    service = IntakeService(
        service.domain, fetcher=transport(), extractors={"web": InspectParser()}
    )
    receipt = service.receive(
        {"url": URL, "text": NOTE}, actor=actors[0], idempotency_key="separated-envelope"
    )
    asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert record["payload"]["text"] == NOTE
    assert "external_document_manifest" not in record["preview"]
    bundle = service.cache.read(receipt["intake_id"], record["preview"]["cache_ref"]["sha256"])
    assert bundle["origin"]["original_text"] == NOTE
    assert bundle["external_document_manifest"]["body_text"] == BODY
    assert NOTE not in json.dumps(bundle["external_document_manifest"])
    source = service.keep(
        receipt["intake_id"], preview_revision=1, actor=actors[0], idempotency_key="keep-separated"
    )
    assert source["external_document_id"]
    assert source["rights"] == "unknown" and source["visibility"] == "private"
    clean = service.domain.read_external_document(source["source_id"], actor=actors[1])
    assert clean is not None
    serialized = json.dumps(clean, ensure_ascii=False)
    assert BODY in serialized and NOTE not in serialized
    assert "fixture-user-17" not in serialized
    assert not {"origin", "channel", "account", "sender", "route", "raw_path"} & set(clean)
    assert (
        service.publication_status(receipt["intake_id"], actor=actors[0])["eligibility"]
        == "pending_review"
    )
    task = service.domain.claim_task(
        "derivative-fixture", actor=actors[1], kinds=["knowledge.generate"]
    )
    service.domain.update_task(
        task["task_id"],
        {
            "state": "partial",
            "lease_token": task["lease_token"],
            "result": {
                "stage": "generate",
                "status": "needs_evidence",
                "publication_block_reason": "DERIVATIVE_COPY_ALARM",
            },
        },
        actor=actors[1],
        idempotency_key="derivative-copy-fixture",
    )
    result = service.publication_status(receipt["intake_id"], actor=actors[0])
    assert result["eligibility"] == "blocked"
    assert result["reason_codes"] == ["DERIVATIVE_COPY_ALARM"]
    assert NOTE not in status_text(result)
    again = service.keep(
        receipt["intake_id"], preview_revision=1, actor=actors[0], idempotency_key="keep-repeated"
    )
    assert again["external_document_id"] == source["external_document_id"]
    assert service.domain.writer.head() is None


@pytest.mark.parametrize("protected", ["employer", "third_party_private"])
def test_same_body_content_deduplication_cannot_reuse_clean_document_for_protected_envelope(
    service, actors, protected
):
    service = IntakeService(service.domain, fetcher=transport())
    receipts = []
    sources = []
    worker = replace(
        actors[1], allowed_classifications=frozenset({"public", "personal", protected})
    )
    for classification in ("personal", protected):
        receipt = service.receive(
            {"url": URL, "text": NOTE, "data_classification": classification},
            actor=actors[0],
            idempotency_key=f"envelope-{classification}",
        )
        asyncio.run(service.process_next(actor=worker))
        receipts.append(receipt)
        sources.append(
            service.keep(
                receipt["intake_id"],
                preview_revision=1,
                actor=actors[0],
                idempotency_key=f"keep-envelope-{classification}",
            )
        )
    assert sources[0]["external_document_id"]
    assert sources[1].get("external_document_id") is None
    assert sources[0]["source_id"] != sources[1]["source_id"]
    assert service.domain.read_external_document(sources[1]["source_id"], actor=worker) is None
    assert (
        service.publication_status(receipts[1]["intake_id"], actor=actors[0])["eligibility"]
        == "blocked"
    )


@pytest.mark.parametrize("nested", [False, True])
def test_python_receive_cannot_accept_self_reported_document_manifest(service, actors, nested):
    claim = {"external_document_manifest": manifest_for()}
    payload = {"url": URL, **({"metadata": claim} if nested else claim)}
    with pytest.raises(IntakeError) as error:
        service.receive(
            payload,
            actor=actors[0],
            idempotency_key="forged-manifest",
        )
    assert error.value.code == "FORBIDDEN"


def test_http_cannot_submit_trusted_manifest(service, actors):
    app = Starlette(routes=create_intake_routes(service.domain, intake_service=service))

    async def authorized(scope, receive, send):
        scope.setdefault("state", {})["actor"] = actors[0]
        await app(scope, receive, send)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=authorized), base_url="http://fixture"
        ) as client:
            response = await client.post(
                "/api/knowledge/v1/intakes",
                json={"url": URL, "external_document_manifest": manifest_for()},
                headers={"Idempotency-Key": "http-forged-manifest"},
            )
            assert response.status_code == 422

    asyncio.run(request())


def test_rejected_derivative_is_not_misreported_as_pending_and_no_free_text_is_echoed():
    source = {"external_document_id": "document-fixture", "rights": "unknown"}
    blocked = status_projection(source, blocked_reason="DERIVATIVE_COPY_ALARM")
    assert blocked["eligibility"] == "blocked"
    assert "不代表法律侵权认定" in status_text(blocked)
    unknown = status_projection(source, blocked_reason=NOTE)
    assert unknown["eligibility"] == "pending_review" and NOTE not in status_text(unknown)
    malformed = status_projection(source, blocked_reason={"unexpected": NOTE})
    assert malformed["eligibility"] == "pending_review" and NOTE not in status_text(malformed)
    approved = status_projection(
        source,
        authoritative={"entry_id": "entry-fixture", "publication_eligible": True},
        blocked_reason="DERIVATIVE_COPY_ALARM",
    )
    assert approved["eligibility"] == "eligible" and approved["reason_codes"] == []


@pytest.mark.parametrize("private_source", [False, True])
def test_explicit_private_intent_overrides_stale_eligible_flag_without_failure(private_source):
    source = {
        "external_document_id": "document-fixture",
        "metadata": {"publication_intent": "private" if private_source else "public"},
    }
    authority = {
        "entry_id": "entry-fixture",
        "publication_intent": "public" if private_source else "private",
        "publication_eligible": True,
    }
    result = status_projection(source, authoritative=authority)
    assert result["publication_intent"] == "private"
    assert result["eligibility"] == "not_requested"
    assert result["publication_eligible"] is False
    text = status_text(result)
    assert "保持私有" in text and "不自动公开" in text
    assert "受阻" not in text and "失败" not in text
