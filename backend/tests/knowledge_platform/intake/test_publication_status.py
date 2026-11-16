"""Actual domain policy remains authoritative; public HTTP transport is a fixture."""

import asyncio

import pytest

from ahadiff.knowledge_platform.intake.contracts import IntakeError
from ahadiff.knowledge_platform.intake.service import IntakeService
from ahadiff.knowledge_platform.media.fetch import FetchResponse, SafeFetcher


def public_transport(content=b"<article>Synthetic public body.</article>", *, status=200):
    async def resolver(host, port):
        return ["93.184.216.34"]

    async def transport(url, address, *, policy, timeout):
        return FetchResponse(url, status, {"content-type": "text/html"}, content)

    return SafeFetcher(resolver=resolver, transport=transport)


def prepare(service, actors, payload):
    receipt = service.receive(payload, actor=actors[0], idempotency_key="publication-intake")
    asyncio.run(service.process_next(actor=actors[1]))
    return receipt, service.domain.read_intake(receipt["intake_id"], actor=actors[0])


def test_anonymous_body_acquisition_never_reclassifies_or_licenses_raw(service, actors):
    service = IntakeService(service.domain, fetcher=public_transport())
    receipt, record = prepare(
        service,
        actors,
        {"url": "https://example.com/article", "text": "Synthetic private share context."},
    )
    facts = record["preview"]["acquisition"]
    assert facts["publicly_accessible"] is True and facts["authenticated"] is False
    assert facts["canonical_url"] == "https://example.com/article"
    assert facts["grants_publication_rights"] is False
    assert record["data_classification"] == "personal"
    assert receipt["publication"]["publication_intent"] == "public"
    assert receipt["publication"]["publication_eligible"] is False
    source = service.keep(
        record["intake_id"], preview_revision=1, actor=actors[0], idempotency_key="keep"
    )
    assert source["visibility"] == "private" and source["rights"] == "unknown"
    assert source["descriptor"]["metadata"]["acquisition"] == facts
    status = service.publication_status(record["intake_id"], actor=actors[0])
    assert status["eligibility"] == "pending_review"
    assert source["external_document_id"]
    assert status["raw_visibility"] == "private" and status["raw_rights"] == "unknown"
    assert "DERIVATIVE_REVIEW_PENDING" in status["reason_codes"]


@pytest.mark.parametrize(
    "content,status,url",
    [
        (b"<title>Only metadata</title>", 200, "https://example.com/post"),
        (b"<article>Login response body.</article>", 403, "https://example.com/post"),
        (
            b"<article>Capability URL body.</article>",
            200,
            "https://example.com/post?token=synthetic",
        ),
        (
            b"<article>Capability URL body.</article>",
            200,
            "https://example.com/post?passcode=synthetic",
        ),
    ],
)
def test_unverified_or_tokenized_body_is_not_marked_public(service, actors, content, status, url):
    service = IntakeService(service.domain, fetcher=public_transport(content, status=status))
    _, record = prepare(service, actors, {"url": url})
    facts = record["preview"]["acquisition"]
    assert facts["publicly_accessible"] is None
    assert "synthetic" not in (facts["canonical_url"] or "")


def test_pasted_content_and_forged_acquisition_cannot_claim_public_fetch(service, actors):
    with pytest.raises(IntakeError) as error:
        service.receive(
            {"text": "Synthetic text.", "acquisition": {"publicly_accessible": True}},
            actor=actors[0],
            idempotency_key="spoof-facts",
        )
    assert error.value.code == "FORBIDDEN"
    _, record = prepare(service, actors, {"text": "Synthetic pasted public article."})
    assert record["preview"]["acquisition"]["publicly_accessible"] is None
    assert record["preview"]["acquisition"]["authenticated"] is None


def test_public_classification_still_cannot_override_actual_domain_rights_gate(service, actors):
    service = IntakeService(
        service.domain, fetcher=public_transport(), input_classification="public"
    )
    _, record = prepare(service, actors, {"url": "https://example.com/article"})
    source = service.keep(
        record["intake_id"],
        preview_revision=1,
        actor=actors[0],
        idempotency_key="keep-public-intent",
    )
    change = service.domain.propose_change(
        {
            "title": "Synthetic proposed summary",
            "body": "Synthetic public body.",
            "visibility": "public",
            "publication_intent": "public",
            "evidence_refs": [{"source_id": source["source_id"]}],
            "metadata": {"publication_block_reason": "SOURCE_RIGHTS_UNVERIFIED"},
        },
        actor=actors[2],
        idempotency_key="unlicensed-proposal",
    )
    assert change["publication_eligible"] is False
    task = service.domain.claim_task(
        "publication-test", actor=actors[1], kinds=["knowledge.generate"]
    )
    service.domain.update_task(
        task["task_id"],
        {
            "state": "waiting_review",
            "lease_token": task["lease_token"],
            "result": {"change_id": change["change_id"]},
        },
        actor=actors[1],
        idempotency_key="bound-proposal",
    )
    result = service.publication_status(record["intake_id"], actor=actors[0])
    assert result["publication_intent"] == "public"
    assert result["authority_id"] == change["change_id"]
    assert result["reason_codes"] == ["SOURCE_RIGHTS_UNVERIFIED"]
    assert result["publication_eligible"] is False
    assert service.domain.writer.head() is None
