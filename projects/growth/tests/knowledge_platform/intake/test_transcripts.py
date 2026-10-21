"""Real subtitle parser and decoder, synthetic transport; optional real ASR is separate."""

import asyncio
import base64
import hashlib
import io
import json
import math
import os
import struct
import wave
from pathlib import Path

import pytest

from ahadiff.knowledge_platform.intake.contracts import (
    ExtractedDocument,
    IntakeError,
    MediaReference,
)
from ahadiff.knowledge_platform.intake.service import IntakeService
from ahadiff.knowledge_platform.media.fetch import FetchResponse
from ahadiff.knowledge_platform.media.transcripts import (
    LocalWhisperASR,
    MediaTranscriptProcessor,
    Transcript,
    parse_captions,
)

VTT = (
    b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nSynthetic caption one.\n\n"
    b"00:00:02.000 --> 00:00:03.000\nSecond caption.\n"
)


def video_document(*, captions=True):
    return ExtractedDocument(
        "https://example.com/video",
        "web",
        "test",
        "1",
        "partial",
        description="Description is not the transcript.",
        media=((MediaReference("https://example.com/track.vtt", "captions"),) if captions else ())
        + (MediaReference("https://example.com/clip.wav", "audio"),),
        metadata={"text_basis": "description_only", "asr_state": "not_run"},
    )


class NetworkFixture:
    def __init__(self, content, content_type):
        self.content, self.content_type, self.calls = content, content_type, []

    async def fetch(self, url):
        self.calls.append(url)
        return FetchResponse(url, 200, {"content-type": self.content_type}, self.content)


@pytest.mark.parametrize(
    "content,url,mime",
    [
        (VTT, "https://example.com/track.vtt", "text/vtt"),
        (
            b"1\n00:00:00,000 --> 00:00:01,500\nSynthetic SRT.\n",
            "https://example.com/track.srt",
            "text/plain",
        ),
    ],
)
def test_installed_subtitle_library_parses_real_formats(content, url, mime):
    pytest.importorskip("webvtt")
    result = parse_captions(content, url=url, content_type=mime)
    assert result.text.startswith("Synthetic") and result.segments
    assert result.engine == "webvtt-py"
    if url.endswith(".srt"):
        assert result.segments[-1]["end"] == 1.5


def test_empty_malformed_and_excessive_captions_are_not_transcripts():
    pytest.importorskip("webvtt")
    for content in (b"not captions", b"WEBVTT\n\n", VTT * 10_000):
        with pytest.raises(IntakeError):
            parse_captions(content, url="https://example.com/test.vtt", content_type="text/vtt")


def test_downloaded_captions_take_precedence_without_asr_or_false_completeness():
    pytest.importorskip("webvtt")
    captions, media = NetworkFixture(VTT, "text/vtt"), NetworkFixture(b"never", "audio/wav")
    result = asyncio.run(
        MediaTranscriptProcessor(caption_fetcher=captions, media_fetcher=media).enrich(
            video_document()
        )
    )
    assert "Synthetic caption one" in result.document.body_text
    assert result.document.body_complete is False
    assert result.document.metadata["asr_state"] == "not_needed"
    assert result.document.metadata["transcript"]["visual_coverage"] == "not_processed"
    assert base64.b64decode(result.artifacts[0]["raw_base64"]) == VTT
    assert media.calls == []


def test_unconfigured_asr_does_not_fetch_media_or_fabricate_text(tmp_path):
    media = NetworkFixture(b"never", "audio/wav")
    processor = MediaTranscriptProcessor(
        asr=LocalWhisperASR(tmp_path / "missing"), media_fetcher=media
    )
    result = asyncio.run(processor.enrich(video_document(captions=False)))
    assert result.document.body_text == "" and media.calls == []
    assert result.document.metadata["asr_state"] == "waiting_configuration"
    assert "ASR_NOT_CONFIGURED" in result.document.metadata["media_errors"]


def test_local_model_requires_tokenizer_before_backend_import_or_download(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"synthetic-not-a-model")
    (tmp_path / "config.json").write_text("{}", encoding="utf8")
    with pytest.raises(IntakeError) as error:
        LocalWhisperASR(tmp_path).check_ready()
    assert error.value.code == "ASR_NOT_CONFIGURED"


def test_asr_timeout_kills_worker_and_omits_credentials(monkeypatch, tmp_path):
    class ProcessFixture:
        killed = False
        waited = False

        async def communicate(self, _):
            await asyncio.sleep(100)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True
            return -9

    process = ProcessFixture()
    monkeypatch.setenv("SYNTHETIC_SECRET_TOKEN", "fixture-only")

    async def spawn(*args, **kwargs):
        assert "-I" in args
        assert "-B" in args
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["env"]["UCX_VFS_ENABLE"] == "n"
        assert "SYNTHETIC_SECRET_TOKEN" not in kwargs["env"]
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    backend = LocalWhisperASR(tmp_path)
    monkeypatch.setattr(backend, "check_ready", lambda: None)
    backend.timeout_seconds = 0.01
    with pytest.raises(IntakeError) as error:
        asyncio.run(backend.transcribe(b"fixture-only"))
    assert error.value.code == "ASR_TIMEOUT" and process.killed and process.waited


def test_caption_fetch_uses_same_private_address_rejection():
    from ahadiff.knowledge_platform.media.fetch import FetchPolicy, SafeFetcher

    async def private_resolver(host, port):
        return ["127.0.0.1"]

    async def forbidden_transport(*args, **kwargs):
        raise AssertionError("Private address reached transport")

    fetcher = SafeFetcher(
        FetchPolicy(allowed_types=("text/vtt",)),
        resolver=private_resolver,
        transport=forbidden_transport,
    )
    processor = MediaTranscriptProcessor(caption_fetcher=fetcher)
    result = asyncio.run(processor.enrich(video_document()))
    assert result.document.body_text == ""
    if __import__("importlib.util").util.find_spec("webvtt") is not None:
        assert "UNSAFE_URL" in result.document.metadata["media_errors"]


def test_caption_journey_retains_actual_downloaded_bytes_only_after_keep(service, actors):
    pytest.importorskip("webvtt")
    page = NetworkFixture(
        b'<video src="clip.wav"><track kind="captions" src="track.vtt"></video>', "text/html"
    )
    captions = NetworkFixture(VTT, "text/vtt")
    service = IntakeService(
        service.domain,
        fetcher=page,
        media_processor=MediaTranscriptProcessor(caption_fetcher=captions),
    )
    receipt = service.receive(
        {"url": "https://example.com/post"}, actor=actors[0], idempotency_key="captions"
    )
    asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert record["source_id"] is None and record["state"] == "partial"
    bundle = service.cache.read(receipt["intake_id"], record["preview"]["cache_ref"]["sha256"])
    assert base64.b64decode(bundle["media_artifacts"][0]["raw_base64"]) == VTT
    retained = service.keep(
        receipt["intake_id"],
        preview_revision=record["preview_revision"],
        actor=actors[0],
        idempotency_key="keep-captions",
    )
    assert retained["source_id"] and service.cache.intake_ids() == []


def test_asr_wait_resume_reuses_downloaded_media_instead_of_network(service, actors):
    class AsrFixture:
        calls = 0

        def check_ready(self):
            return None

        async def transcribe(self, content):
            self.calls += 1
            if self.calls == 1:
                raise IntakeError("ASR_TIMEOUT", "Synthetic timeout")
            return Transcript(
                "Synthetic ASR fixture only.",
                ({"start": 0, "end": 1, "text": "Synthetic ASR fixture only."},),
                "fixture",
            )

    page = NetworkFixture(b'<audio src="clip.wav"></audio>', "text/html")
    media = NetworkFixture(b"synthetic-not-real-media", "audio/wav")
    asr = AsrFixture()
    service = IntakeService(
        service.domain,
        fetcher=page,
        media_processor=MediaTranscriptProcessor(asr=asr, media_fetcher=media),
    )
    receipt = service.receive(
        {"url": "https://example.com/post"}, actor=actors[0], idempotency_key="asr-retry"
    )
    first = asyncio.run(service.process_next(actor=actors[1]))
    assert first["state"] == "waiting_input"
    service.domain.update_task(
        receipt["task_id"], {"state": "queued"}, actor=actors[0], idempotency_key="asr-retry-queue"
    )
    asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    assert record["preview"]["document"]["body_text"] == "Synthetic ASR fixture only."
    assert record["preview"]["document"]["metadata"]["asr_state"] == "succeeded"
    assert len(media.calls) == len(page.calls) == 1


def wav_tone(seconds=0.1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(
            b"".join(
                struct.pack("<h", int(5000 * math.sin(index * 0.2)))
                for index in range(int(seconds * 16000))
            )
        )
    return buffer.getvalue()


def test_actual_pyav_decoder_is_bounded_and_rejects_playlist():
    pytest.importorskip("av")
    from ahadiff.knowledge_platform.media._asr_worker import MediaFailure, decode_media

    assert len(decode_media(wav_tone(), 1)) == 1600
    with pytest.raises(MediaFailure) as duration:
        decode_media(wav_tone(2), 1)
    assert duration.value.code == "MEDIA_DURATION_LIMIT"
    with pytest.raises(MediaFailure) as playlist:
        decode_media(b"#EXTM3U\nhttp://127.0.0.1/private.wav\n", 1)
    assert playlist.value.code == "MEDIA_INVALID"


@pytest.mark.skipif(
    not os.environ.get("ZHIZHI_TEST_ASR_MODEL"),
    reason="Real offline ASR requires separately verified local model and speech",
)
def test_real_offline_asr_speech_sample():
    """Not a synthetic model or silent audio: run the actual configured local weights."""
    model = Path(os.environ["ZHIZHI_TEST_ASR_MODEL"])
    sample = Path(os.environ["ZHIZHI_TEST_ASR_SAMPLE"])
    result = asyncio.run(
        LocalWhisperASR(model, timeout_seconds=120).transcribe(sample.read_bytes())
    )
    expected = json.loads(
        os.environ.get("ZHIZHI_TEST_ASR_EXPECTED", '["my fellow americans", "your country"]')
    )
    assert isinstance(expected, list) and expected
    assert all(isinstance(term, str) and term.strip() for term in expected)
    assert all(term.casefold() in result.text.casefold() for term in expected)
    if digest := os.environ.get("ZHIZHI_TEST_ASR_SHA256"):
        assert hashlib.sha256(sample.read_bytes()).hexdigest() == digest.lower()
    assert result.engine == "faster-whisper" and result.duration_seconds > 1
    if result.timing_valid:
        assert all(segment["end"] <= result.duration_seconds for segment in result.segments)
    else:
        assert result.timing_valid is False and not result.segments and result.raw_model_segments


@pytest.mark.parametrize("end,valid", [(5.49, True), (5.0, True), (10.0, False)])
def test_asr_timing_actual_duration(end, valid):
    from ahadiff.knowledge_platform.media._asr_worker import timing_metadata
    from ahadiff.knowledge_platform.media.transcripts import TIMESTAMP_UNRELIABLE_WARNING

    original = [{"start": 0.0, "end": end, "text": "Unmodified speech."}]
    timing = timing_metadata(original, 5.49)
    assert timing["timing_valid"] is valid
    assert timing["segments"] == (original if valid else [])
    assert timing["raw_model_segments"] == ([] if valid else original)
    assert original[0]["end"] == end
    transcript = Transcript(
        "Unmodified speech.",
        tuple(timing["segments"]),
        "faster-whisper",
        5.49,
        "en",
        valid,
        tuple(timing["raw_model_segments"]),
    )
    document = MediaTranscriptProcessor._result(
        video_document(captions=False), transcript, {"sha256": "fixture"}, captions=False
    ).document
    assert document.body_text == "Unmodified speech."
    assert document.metadata["transcript"]["segments"] == transcript.segments
    assert (TIMESTAMP_UNRELIABLE_WARNING in document.warnings) is (not valid)
    assert ("timing_diagnostics" in document.metadata["transcript"]) is (not valid)


def test_unreliable_timing_preview_and_persistence(service, actors):
    from ahadiff.knowledge_platform.media.transcripts import TIMESTAMP_UNRELIABLE_WARNING

    class AsrFixture:
        def check_ready(self):
            return None

        async def transcribe(self, content):
            return Transcript(
                "Unmodified speech.",
                (),
                "fixture",
                5.49,
                "en",
                False,
                ({"start": 0.0, "end": 10.0, "text": "Unmodified speech."},),
            )

    service = IntakeService(
        service.domain,
        fetcher=NetworkFixture(b'<audio src="clip.wav"></audio>', "text/html"),
        media_processor=MediaTranscriptProcessor(
            asr=AsrFixture(), media_fetcher=NetworkFixture(wav_tone(), "audio/wav")
        ),
    )
    receipt = service.receive(
        {"url": "https://example.com/post"}, actor=actors[0], idempotency_key="timing"
    )
    asyncio.run(service.process_next(actor=actors[1]))
    record = service.domain.read_intake(receipt["intake_id"], actor=actors[0])
    document = record["preview"]["document"]
    assert document["body_text"] == "Unmodified speech."
    assert TIMESTAMP_UNRELIABLE_WARNING in document["warnings"]
    timing = document["metadata"]["transcript"]
    assert timing["segments"] == [] and timing["timing_valid"] is False
    assert timing["duration_seconds"] == 5.49
    assert timing["timing_diagnostics"]["raw_model_segments"][0]["end"] == 10.0
    assert timing["timing_diagnostics"]["usable_for_location"] is False
    retained = service.keep(
        receipt["intake_id"],
        preview_revision=record["preview_revision"],
        actor=actors[0],
        idempotency_key="keep-timing",
    )
    source = service.domain.get_source(retained["source_id"], actor=actors[0])
    assert source["descriptor"]["metadata"]["extraction"] == document
    saved = json.loads((service.domain.object_path / source["object_path"]).read_bytes())
    assert saved["document"] == document
