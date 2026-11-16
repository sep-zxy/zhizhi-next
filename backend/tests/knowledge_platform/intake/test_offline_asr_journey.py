"""Fixture transport; optional real offline model, decoder and persistence."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from ahadiff.knowledge_platform.intake.service import IntakeService
from ahadiff.knowledge_platform.media.fetch import FetchResponse
from ahadiff.knowledge_platform.media.transcripts import LocalWhisperASR, MediaTranscriptProcessor


class NetworkFixture:
    def __init__(self, content, mime):
        self.content, self.mime, self.calls = content, mime, []

    async def fetch(self, url):
        self.calls.append(url)
        return FetchResponse(url, 200, {"content-type": self.mime}, self.content)


@pytest.mark.skipif(
    not os.environ.get("ZHIZHI_TEST_ASR_MODEL"),
    reason="Real offline ASR needs verified local weights and speech",
)
def test_real_offline_asr_preview_keep(service, actors):
    content = Path(os.environ["ZHIZHI_TEST_ASR_SAMPLE"]).read_bytes()
    expected = json.loads(
        os.environ.get("ZHIZHI_TEST_ASR_EXPECTED", '["my fellow americans", "your country"]')
    )
    assert isinstance(expected, list) and expected
    assert all(isinstance(term, str) and term.strip() for term in expected)
    digest = hashlib.sha256(content).hexdigest()
    if configured := os.environ.get("ZHIZHI_TEST_ASR_SHA256"):
        assert digest == configured.lower()
    page = NetworkFixture(b'<audio src="clip.wav"></audio>', "text/html")
    media = NetworkFixture(content, "audio/wav")
    service = IntakeService(
        service.domain,
        fetcher=page,
        media_processor=MediaTranscriptProcessor(
            asr=LocalWhisperASR(Path(os.environ["ZHIZHI_TEST_ASR_MODEL"]), timeout_seconds=120),
            media_fetcher=media,
        ),
    )
    receipt = service.receive(
        {"url": "https://example.com/post"}, actor=actors[0], idempotency_key="real-asr"
    )
    asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    document = record["preview"]["document"]
    assert record["state"] == "partial" and record["source_id"] is None
    assert all(term.casefold() in document["body_text"].casefold() for term in expected)
    assert document["body_complete"] is False
    metadata = document["metadata"]
    assert metadata["asr_state"] == "succeeded" and metadata["text_basis"] == "local_asr"
    assert metadata["transcript"]["engine"] == "faster-whisper"
    assert metadata["transcript"]["source_sha256"] == digest
    assert metadata["transcript"]["visual_coverage"] == "not_processed"
    timing = metadata["transcript"]
    if not timing["timing_valid"]:
        from ahadiff.knowledge_platform.media.transcripts import TIMESTAMP_UNRELIABLE_WARNING

        assert timing["segments"] == []
        assert TIMESTAMP_UNRELIABLE_WARNING in document["warnings"]
        assert timing["timing_diagnostics"]["usable_for_location"] is False
    bundle = service.cache.read(receipt["intake_id"], record["preview"]["cache_ref"]["sha256"])
    assert base64.b64decode(bundle["media_artifacts"][0]["raw_base64"]) == content
    retained = service.keep(
        receipt["intake_id"],
        preview_revision=record["preview_revision"],
        actor=actors[0],
        idempotency_key="keep-real-asr",
    )
    assert retained["source_id"] and service.cache.intake_ids() == []
    assert len(page.calls) == len(media.calls) == 1
    source = service.domain.get_source(retained["source_id"], actor=actors[0])
    assert source["visibility"] == "private" and source["rights"] == "unknown"
    persisted = (service.domain.object_path / source["object_path"]).read_bytes()
    assert hashlib.sha256(persisted).hexdigest() == source["content_sha256"]
    saved = json.loads(persisted)
    assert saved["document"] == document
    assert base64.b64decode(saved["media_artifacts"][0]["raw_base64"]) == content
