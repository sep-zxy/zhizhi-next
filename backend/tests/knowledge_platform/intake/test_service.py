"""Intake-to-domain fixture integration; not external-channel or model E2E."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ahadiff.knowledge_platform.intake.contracts import IntakeError
from ahadiff.knowledge_platform.intake.service import IntakeService


def ready(service, actors, text="Synthetic owner-supplied evidence."):
    user, worker, _ = actors
    receipt = service.receive({"text": text}, actor=user, idempotency_key="receive")
    asyncio.run(service.process_next(actor=worker))
    return service.domain.read_intake(receipt["intake_id"], actor=user), receipt


def test_preview_does_not_retain_or_queue_knowledge(service, actors):
    record, receipt = ready(service, actors)
    assert record["state"] == "preview_ready"
    assert record["source_id"] is None
    assert record["preview"]["summary"] is None
    assert record["preview"]["document"]["body_text"] == "Synthetic owner-supplied evidence."
    assert (
        service.domain.claim_task("knowledge", actor=actors[1], kinds=["knowledge.generate"])
        is None
    )
    assert not list(service.domain.object_path.rglob("*.json"))
    assert receipt["task_id"]


def test_repeated_receipt_and_keep_are_stable(service, actors):
    record, receipt = ready(service, actors)
    again = service.receive(
        {"text": "Synthetic owner-supplied evidence."}, actor=actors[0], idempotency_key="receive"
    )
    assert again["intake_id"] == receipt["intake_id"]
    assert again["task_id"] == receipt["task_id"]
    first = service.keep(
        record["intake_id"],
        preview_revision=record["preview_revision"],
        actor=actors[0],
        idempotency_key="keep",
    )
    second = service.keep(
        record["intake_id"],
        preview_revision=record["preview_revision"],
        actor=actors[0],
        idempotency_key="keep-again",
    )
    assert first["source_id"] == second["source_id"]
    assert first["task_id"] == second["task_id"]
    assert first["visibility"] == "private"
    evidence = service.domain.read_evidence({"source_id": first["source_id"]}, actor=actors[0])
    assert "Synthetic owner-supplied evidence." in evidence["text"]
    assert not list(service.cache.root.rglob("*.json"))


def test_agent_cannot_keep_even_with_broad_scopes(service, actors):
    record, _ = ready(service, actors)
    with pytest.raises(IntakeError) as error:
        service.keep(
            record["intake_id"], preview_revision=1, actor=actors[2], idempotency_key="agent-keep"
        )
    assert error.value.status_code == 403


def test_stale_preview_is_not_retained(service, actors):
    record, _ = ready(service, actors)
    with pytest.raises(IntakeError) as error:
        service.keep(
            record["intake_id"], preview_revision=100, actor=actors[0], idempotency_key="stale-keep"
        )
    assert error.value.code == "BASE_STALE"


def test_discard_removes_only_cache_and_never_creates_source(service, actors):
    record, _ = ready(service, actors)
    digest = record["preview"]["cache_ref"]["sha256"]
    path = service.cache.path(record["intake_id"], digest)
    discarded = service.discard(record["intake_id"], actor=actors[0], idempotency_key="discard")
    assert discarded["state"] == "discarded"
    assert discarded["preview"] is None
    assert not path.exists()
    assert discarded["source_id"] is None


def test_expired_cache_cannot_be_kept(service, actors):
    record, _ = ready(service, actors)
    service.domain.update_intake(
        record["intake_id"],
        {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        actor=actors[0],
        idempotency_key="set-expiry",
    )
    with pytest.raises(IntakeError) as error:
        service.keep(
            record["intake_id"], preview_revision=1, actor=actors[0], idempotency_key="keep-expired"
        )
    assert error.value.code == "INTAKE_EXPIRED"
    assert service.domain.read_intake(record["intake_id"], actor=actors[0])["state"] == "expired"


def test_parser_denial_is_explicit_and_does_not_call_model(service, actors):
    class DeniedFetcher:
        async def fetch(self, url):
            raise IntakeError("NEEDS_AUTH", "登录限制。")

    class ForbiddenModel:
        async def run(self, task):
            raise AssertionError("Missing source text must not be summarized")

    service = IntakeService(
        service.domain, ForbiddenModel(), fetcher=DeniedFetcher(), allowed_provider_ids=("fixture",)
    )
    receipt = service.receive(
        {"url": "https://www.douyin.com/video/123"}, actor=actors[0], idempotency_key="denied"
    )
    task = asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert task["state"] == "waiting_auth"
    assert record["parse_status"] == "needs_auth"
    assert record["preview"]["summary"] is None
    assert record["source_id"] is None


def test_restart_reads_persisted_queue_not_memory(service, actors):
    receipt = service.receive(
        {"text": "Resume after restart."}, actor=actors[0], idempotency_key="restart"
    )
    restarted = IntakeService(service.domain)
    asyncio.run(restarted.process_next(actor=actors[1]))
    assert (
        restarted.domain.read_intake(receipt["intake_id"], actor=actors[0])["state"]
        == "preview_ready"
    )


def test_cannot_spoof_owner_or_approval(service, actors):
    for payload in ({"text": "x", "owner_id": "victim"}, {"text": "x", "user_approved": True}):
        with pytest.raises(IntakeError) as error:
            service.receive(payload, actor=actors[0], idempotency_key="spoof")
        assert error.value.status_code == 403


def test_unhandled_attachment_is_needs_input_not_fabricated_text(service, actors):
    receipt = service.receive(
        {},
        actor=actors[0],
        channel="telegram:fixture",
        external_message_id="123",
        idempotency_key="unsupported",
        unsupported_reason="请补充正文。",
    )
    task = asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert task["state"] == "waiting_input"
    assert record["preview"]["document"]["body_text"] == ""
    assert record["parse_status"] == "needs_input"


def test_repeated_share_reuses_one_source_and_processing_task(service, actors):
    sources = []
    for index in range(2):
        receipt = service.receive(
            {"text": "Same evidence, independently shared."},
            actor=actors[0],
            idempotency_key=f"share-{index}",
        )
        asyncio.run(service.process_next(actor=actors[1]))
        sources.append(
            service.keep(
                receipt["intake_id"],
                preview_revision=1,
                actor=actors[0],
                idempotency_key=f"keep-{index}",
            )
        )
    assert sources[0]["source_id"] == sources[1]["source_id"]
    assert sources[0]["task_id"] == sources[1]["task_id"]
    assert not list(service.cache.root.rglob("*.json"))


def test_concurrent_keep_remains_one_durable_source(service, actors):
    from concurrent.futures import ThreadPoolExecutor

    record, _ = ready(service, actors)

    def keep(index):
        return service.keep(
            record["intake_id"],
            preview_revision=1,
            actor=actors[0],
            idempotency_key=f"concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = list(pool.map(keep, range(4)))
    assert len({item["source_id"] for item in sources}) == 1
    assert len({item["task_id"] for item in sources}) == 1
    assert service.cache.intake_ids() == []
    evidence = service.domain.read_evidence(
        {"source_id": sources[0]["source_id"]}, actor=actors[0]
    )
    assert "Synthetic owner-supplied evidence." in evidence["text"]


def test_quota_resume_reuses_captured_body_not_network(service, actors):
    from ahadiff.knowledge_platform.media.fetch import FetchResponse
    from ahadiff.knowledge_platform.model import ModelResult

    class NetworkFixture:
        calls = 0

        async def fetch(self, url):
            self.calls += 1
            return FetchResponse(
                url, 200, {"content-type": "text/html"}, b"<article>Synthetic source.</article>"
            )

    class QuotaThenReadyFixture:
        calls = 0

        async def run(self, task):
            self.calls += 1
            return ModelResult(
                task_id=task.task_id,
                status="waiting_quota" if self.calls == 1 else "succeeded",
                output=None if self.calls == 1 else {"summary": "Synthetic summary."},
                fixture=True,
            )

    network, model = NetworkFixture(), QuotaThenReadyFixture()
    service = IntakeService(
        service.domain, model, fetcher=network, allowed_provider_ids=("fixture",)
    )
    receipt = service.receive(
        {"url": "https://example.com/fixture"}, actor=actors[0], idempotency_key="quota"
    )
    first = asyncio.run(service.process_next(actor=actors[1]))
    assert first["state"] == "waiting_quota"
    before = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    service.domain.update_task(
        first["task_id"], {"state": "queued"}, actor=actors[0], idempotency_key="quota-resolved"
    )
    second = asyncio.run(service.process_next(actor=actors[1]))
    after = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert second["state"] == "succeeded"
    assert after["preview"]["summary"] == "Synthetic summary."
    assert after["preview"]["model_fixture"] is True
    assert after["preview_revision"] == before["preview_revision"] + 1
    assert after["preview"]["cache_ref"] == before["preview"]["cache_ref"]
    assert after["source_id"] is None
    assert network.calls == 1
    assert model.calls == 2


@pytest.mark.parametrize("status,output", [("succeeded", {"summary": ""}), ("cancelled", None)])
def test_non_summary_model_outcome_is_not_success(service, actors, status, output):
    from ahadiff.knowledge_platform.model import ModelResult

    class InvalidModelFixture:
        async def run(self, task):
            return ModelResult(task_id=task.task_id, status=status, output=output, fixture=True)

    service = IntakeService(
        service.domain, InvalidModelFixture(), allowed_provider_ids=("fixture",)
    )
    service.receive({"text": "Synthetic source."}, actor=actors[0], idempotency_key="invalid")
    assert asyncio.run(service.process_next(actor=actors[1]))["state"] == "waiting_input"


def test_requeued_incomplete_preview_cannot_be_marked_success(service, actors):
    receipt = service.receive(
        {}, actor=actors[0], idempotency_key="empty", unsupported_reason="No readable text."
    )
    first = asyncio.run(service.process_next(actor=actors[1]))
    service.domain.update_task(
        receipt["task_id"], {"state": "queued"}, actor=actors[0], idempotency_key="retry-empty"
    )
    second = asyncio.run(service.process_next(actor=actors[1]))
    assert first["state"] == second["state"] == "waiting_input"


def test_discard_clears_original_text_from_receipt_replays(service, actors):
    marker = "Synthetic-private-marker-not-a-real-secret"
    record, _ = ready(service, actors, text=marker)
    service.discard(record["intake_id"], actor=actors[0], idempotency_key="discard-private")
    replay = service.domain.create_intake(
        record["payload"], actor=actors[0], idempotency_key="receive"
    )
    assert replay["state"] == "discarded"
    with service.domain.store.reading() as connection:
        for table, field in (("kp_records", "data"), ("kp_receipts", "response")):
            rows = connection.execute(f"SELECT {field} FROM {table}").fetchall()
            assert all(marker not in row[field] for row in rows)


def test_cleanup_expires_unprocessed_receipt_without_cache(service, actors):
    receipt = service.receive(
        {"text": "Unprocessed synthetic source."}, actor=actors[0], idempotency_key="unprocessed"
    )
    service.domain.update_intake(
        receipt["intake_id"],
        {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        actor=actors[0],
        idempotency_key="unprocessed-expiry",
    )
    assert service.cache.intake_ids() == []
    assert service.cleanup_expired(actor=actors[1]) == 1
    assert service.domain.read_intake(receipt["intake_id"], actor=actors[0])["state"] == "expired"
    assert service.domain.read_task(receipt["task_id"], actor=actors[0])["state"] == "cancelled"
    assert service.cleanup_expired(actor=actors[1]) == 0


def test_worker_expiring_own_claim_returns_cancelled_not_error(service, actors):
    receipt = service.receive(
        {"text": "Expired synthetic source."}, actor=actors[0], idempotency_key="worker-expired"
    )
    service.domain.update_intake(
        receipt["intake_id"],
        {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        actor=actors[0],
        idempotency_key="worker-expiry",
    )
    assert asyncio.run(service.process_next(actor=actors[1]))["state"] == "cancelled"


@pytest.mark.parametrize("classification", ["secret", "unknown", "employer"])
def test_cleanup_sensitive_cached_intake_without_reading_body(service, actors, classification):
    from ahadiff.knowledge_platform.errors import DomainError

    receipt = service.receive(
        {"text": "Synthetic protected expiry marker.", "data_classification": classification},
        actor=actors[0],
        idempotency_key="protected-expiry",
    )
    service.cache.put(receipt["intake_id"], {"body": "Synthetic cached marker."})
    service.domain.update_intake(
        receipt["intake_id"],
        {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        actor=actors[0],
        idempotency_key="protected-expiry-time",
    )
    with pytest.raises(DomainError, match="classification|分类"):
        service.domain.read_intake(receipt["intake_id"], actor=actors[1])
    assert service.cleanup_expired(actor=actors[1]) == 1
    assert service.cache.intake_ids() == []
    # Reconcile an orphan created before a hypothetical cleanup crash without
    # making protected content readable to the maintenance actor.
    service.cache.put(receipt["intake_id"], {"body": "Synthetic orphan marker."})
    assert service.cleanup_expired(actor=actors[1]) == 1
    assert service.cache.intake_ids() == []


def test_declared_classification_cannot_weaken_deployment_default(service, actors):
    service = IntakeService(service.domain, input_classification="employer")
    receipt = service.receive(
        {"text": "Synthetic protected material.", "data_classification": "public"},
        actor=actors[0],
        idempotency_key="classified",
    )
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert record["data_classification"] == "employer"


def test_mixed_protected_classifications_are_not_collapsed_to_permitted_one(service, actors):
    from ahadiff.knowledge_platform.errors import DomainError

    service = IntakeService(service.domain, input_classification="employer")
    receipt = service.receive(
        {"text": "Synthetic mixed material.", "data_classification": "third_party_private"},
        actor=actors[0],
        idempotency_key="mixed",
    )
    assert (
        service.domain.read_intake(receipt["intake_id"], actor=actors[0])["data_classification"]
        == "secret"
    )
    assert asyncio.run(service.process_next(actor=actors[1])) is None
    with pytest.raises(DomainError) as error:
        service.domain.read_intake(receipt["intake_id"], actor=actors[1])
    assert error.value.code == "CLASSIFICATION_FORBIDDEN"


def test_preview_model_receives_authoritative_evidence_classification(service, actors):
    from ahadiff.knowledge_platform.auth import Actor
    from ahadiff.knowledge_platform.model import ModelResult

    class CapturingFixture:
        classifications = []

        async def run(self, task):
            self.classifications.append(task.input_classification)
            return ModelResult(task_id=task.task_id, status="blocked", fixture=True)

    runner = CapturingFixture()
    service = IntakeService(service.domain, runner, allowed_provider_ids=("fixture",))
    service.receive(
        {"text": "Synthetic employer material.", "data_classification": "employer"},
        actor=actors[0],
        idempotency_key="employer",
    )
    privileged_worker = Actor(
        actors[1].owner_id,
        "classified-worker",
        "service",
        actors[1].scopes,
        allowed_classifications=frozenset({"public", "personal", "employer"}),
    )
    assert asyncio.run(service.process_next(actor=privileged_worker))["state"] == "waiting_input"
    assert runner.classifications == ["employer"]
