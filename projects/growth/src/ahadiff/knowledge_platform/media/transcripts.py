"""Bounded subtitle extraction and explicitly configured, offline speech recognition.

Transport always uses SafeFetcher, not a downloader embedded in an ASR library.
Caption files and decoded speech are evidence, never proof of full visual coverage.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..intake.contracts import ExtractedDocument, IntakeError
from ..portable import portable_child_environment, require_portable_path
from .fetch import FetchPolicy, FetchResponse, SafeFetcher

_MAX_MEDIA_BYTES = 8 * 1024 * 1024
TIMESTAMP_UNRELIABLE_WARNING = "语音模型时间戳超出实际音频范围，已停用时间定位；原始时间仅供诊断。"
_CAPTION_TYPES = ("text/vtt", "application/x-subrip", "text/plain", "application/octet-stream")
_MEDIA_TYPES = (
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/flac",
    "audio/aac",
    "video/mp4",
    "video/webm",
    "audio/webm",
    "application/octet-stream",
)


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    segments: tuple[dict[str, Any], ...]
    engine: str
    duration_seconds: float | None = None
    language: str | None = None
    timing_valid: bool | None = None
    raw_model_segments: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class MediaExtraction:
    document: ExtractedDocument
    artifacts: tuple[dict[str, Any], ...] = ()


def parse_captions(content: bytes, *, url: str, content_type: str) -> Transcript:
    """Use maintained webvtt-py for VTT/SRT; do not write a replacement parser."""
    if len(content) > 512 * 1024:
        raise IntakeError("CAPTIONS_TOO_LARGE", "字幕超过大小限制。")
    try:
        import webvtt
    except ImportError:
        raise IntakeError("CAPTIONS_NOT_CONFIGURED", "未安装字幕解析依赖。") from None
    suffix = Path(urlsplit(url).path).suffix.lower()
    format_name = "srt" if suffix == ".srt" or content_type == "application/x-subrip" else "vtt"
    try:
        parsed = webvtt.from_buffer(io.BytesIO(content), format=format_name)
        segments = []
        last_start = -1.0
        characters = 0
        for cue in parsed:
            start = cue.start_in_seconds + cue.start_time.milliseconds / 1000
            end = cue.end_in_seconds + cue.end_time.milliseconds / 1000
            text = cue.text.strip()
            if start < last_start or end < start or end > 6 * 3600:
                raise ValueError("Invalid or unbounded caption timeline")
            last_start = start
            if not text:
                continue
            characters += len(text)
            if len(segments) >= 5000 or characters > 100_000:
                raise ValueError("Caption output exceeds limit")
            segments.append({"start": start, "end": end, "text": text})
    except Exception:
        raise IntakeError("CAPTIONS_INVALID", "字幕无法可靠解析，请补充有效字幕。") from None
    if not segments:
        raise IntakeError("CAPTIONS_EMPTY", "字幕没有可读取的文本。")
    return Transcript(
        "\n".join(segment["text"] for segment in segments),
        tuple(segments),
        "webvtt-py",
        duration_seconds=segments[-1]["end"],
    )


class LocalWhisperASR:
    """Run installed faster-whisper in a killable child with local model files only."""

    def __init__(self, model_path: Path, *, timeout_seconds: float = 90, max_duration: int = 300):
        if not 1 <= timeout_seconds <= 300 or not 1 <= max_duration <= 600:
            raise ValueError("ASR timeout/duration exceed supported bounds")
        self.model_path = Path(model_path)
        self.timeout_seconds, self.max_duration = timeout_seconds, max_duration

    def check_ready(self) -> None:
        require_portable_path(self.model_path)
        if not self.model_path.is_absolute() or not self.model_path.is_dir():
            raise IntakeError("ASR_NOT_CONFIGURED", "请配置已有的本地语音模型目录。")
        # faster-whisper can fetch a tokenizer even when local_files_only=True
        # if tokenizer.json is absent. Require it before spawning the worker.
        if any(
            not (self.model_path / name).is_file()
            for name in (
                "model.bin",
                "config.json",
                "tokenizer.json",
            )
        ):
            raise IntakeError("ASR_NOT_CONFIGURED", "本地语音模型或 tokenizer 文件不完整。")
        if (
            importlib.util.find_spec("faster_whisper") is None
            or importlib.util.find_spec("av") is None
        ):
            raise IntakeError("ASR_NOT_CONFIGURED", "未安装本地语音识别依赖。")

    async def transcribe(self, content: bytes) -> Transcript:
        self.check_ready()
        if not content or len(content) > _MAX_MEDIA_BYTES:
            raise IntakeError("MEDIA_TOO_LARGE", "媒体为空或超过下载限制。")
        # No inherited credentials, proxies or telemetry configuration are needed.
        env = {
            name: os.environ[name]
            for name in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP")
            if name in os.environ
        }
        env.update(portable_child_environment())
        env.update(
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1",
            DO_NOT_TRACK="1",
            TOKENIZERS_PARALLELISM="false",
            OMP_NUM_THREADS="2",
            OPENBLAS_NUM_THREADS="1",
            # Disable UCX's optional diagnostic VFS thread/socket; ASR does not
            # need distributed-computing diagnostics or any local service socket.
            UCX_VFS_ENABLE="n",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).with_name("_asr_worker.py")),
            str(self.model_path),
            str(self.max_duration),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            limit=512 * 1024,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(content), self.timeout_seconds)
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise
            raise IntakeError("ASR_TIMEOUT", "本地语音识别超过时间限制。") from None
        if len(output) > 512 * 1024:
            raise IntakeError("ASR_OUTPUT_LIMIT", "语音识别输出超过限制。")
        try:
            result = json.loads(output)
        except (ValueError, UnicodeDecodeError):
            raise IntakeError("ASR_FAILED", "本地语音识别未返回可验证结果。") from None
        if process.returncode != 0 or result.get("status") != "succeeded":
            code = result.get("code")
            if code not in {
                "MEDIA_INVALID",
                "MEDIA_DURATION_LIMIT",
                "ASR_NO_SPEECH",
                "ASR_NOT_CONFIGURED",
            }:
                code = "ASR_FAILED"
            raise IntakeError(code, "媒体无法完成本地转写，请检查配置或补充字幕。")
        return Transcript(
            result["text"],
            tuple(result["segments"]),
            "faster-whisper",
            result["duration_seconds"],
            result.get("language"),
            result["timing_valid"],
            tuple(result["raw_model_segments"]),
        )


def _artifact(response: FetchResponse, kind: str, requested_url: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "url": response.url,
        "requested_url": requested_url,
        "content_type": response.content_type,
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "bytes": len(response.content),
        "raw_base64": base64.b64encode(response.content).decode("ascii"),
    }


class MediaTranscriptProcessor:
    """Try declared subtitle tracks first; ASR only when explicitly configured."""

    def __init__(
        self,
        *,
        asr: LocalWhisperASR | None = None,
        caption_fetcher: SafeFetcher | None = None,
        media_fetcher: SafeFetcher | None = None,
    ):
        self.asr = asr
        self.caption_fetcher = caption_fetcher or SafeFetcher(
            FetchPolicy(
                max_bytes=512 * 1024,
                total_timeout=20,
                allowed_types=_CAPTION_TYPES,
            )
        )
        self.media_fetcher = media_fetcher or SafeFetcher(
            FetchPolicy(
                max_bytes=_MAX_MEDIA_BYTES,
                total_timeout=20,
                allowed_types=_MEDIA_TYPES,
            )
        )

    async def enrich(
        self, document: ExtractedDocument, *, cached_artifacts: tuple[dict[str, Any], ...] = ()
    ) -> MediaExtraction:
        if document.body_text and document.metadata.get("text_basis") in {
            "article_body",
            "post_body",
            "user_supplied_text",
            "downloaded_captions",
            "local_asr",
        }:
            return MediaExtraction(document)
        captions = [item for item in document.media if item.kind == "captions"][:2]
        media = [item for item in document.media if item.kind in {"audio", "video"}]
        if not captions and not media:
            return MediaExtraction(document)
        errors: list[str] = []
        for reference in captions:
            try:
                if importlib.util.find_spec("webvtt") is None:
                    raise IntakeError("CAPTIONS_NOT_CONFIGURED", "未安装字幕解析依赖。")
                response = await self.caption_fetcher.fetch(reference.url)
                transcript = parse_captions(
                    response.content, url=response.url, content_type=response.content_type
                )
                return self._result(
                    document,
                    transcript,
                    _artifact(response, "captions", reference.url),
                    captions=True,
                )
            except IntakeError as error:
                errors.append(error.code)
        if not media:
            return self._unavailable(document, errors or ["MEDIA_UNAVAILABLE"])
        if self.asr is None:
            return self._unavailable(document, errors + ["ASR_NOT_CONFIGURED"])
        try:
            self.asr.check_ready()  # Do not download a media file when the model is unavailable.
            previous = next(
                (
                    item
                    for item in cached_artifacts
                    if item.get("requested_url", item.get("url")) == media[0].url
                    and item.get("kind") == media[0].kind
                ),
                None,
            )
            if previous is not None:
                try:
                    content = base64.b64decode(previous["raw_base64"], validate=True)
                    if (
                        len(content) > _MAX_MEDIA_BYTES
                        or hashlib.sha256(content).hexdigest() != previous["sha256"]
                    ):
                        raise ValueError("Invalid cached media")
                    response = FetchResponse(
                        previous["url"], 200, {"content-type": previous["content_type"]}, content
                    )
                except (KeyError, ValueError):
                    raise IntakeError("CACHE_INTEGRITY", "缓存媒体完整性校验失败。") from None
            else:
                response = await self.media_fetcher.fetch(media[0].url)
            artifact = _artifact(response, media[0].kind, media[0].url)
            try:
                transcript = await self.asr.transcribe(response.content)
            except IntakeError as error:
                return MediaExtraction(
                    self._unavailable(document, errors + [error.code]).document, (artifact,)
                )
            return self._result(document, transcript, artifact, captions=False)
        except IntakeError as error:
            return self._unavailable(document, errors + [error.code])

    @staticmethod
    def _unavailable(document: ExtractedDocument, errors: list[str]) -> MediaExtraction:
        return MediaExtraction(
            replace(
                document,
                metadata={
                    **document.metadata,
                    "asr_state": "waiting_configuration"
                    if "ASR_NOT_CONFIGURED" in errors
                    else "failed",
                    "media_errors": errors,
                },
                warnings=tuple(
                    dict.fromkeys(
                        (*document.warnings, "未完成媒体转写；不会把作品文案当作视频正文。")
                    )
                ),
            )
        )

    @staticmethod
    def _result(
        document: ExtractedDocument,
        transcript: Transcript,
        artifact: dict[str, Any],
        *,
        captions: bool,
    ) -> MediaExtraction:
        # Subtitle timeline and ASR audio do not establish full audiovisual coverage.
        # Keep partial until a separate completeness assessment, even for real ASR.
        enriched = replace(
            document,
            state="partial",
            body_text=transcript.text,
            body_complete=False,
            metadata={
                **document.metadata,
                "text_basis": "downloaded_captions" if captions else "local_asr",
                "asr_state": "not_needed" if captions else "succeeded",
                "captions_state": "parsed" if captions else "not_available",
                "transcript": {
                    "engine": transcript.engine,
                    "segments": transcript.segments,
                    "duration_seconds": transcript.duration_seconds,
                    "language": transcript.language,
                    "source_sha256": artifact["sha256"],
                    "visual_coverage": "not_processed",
                    **(
                        {"timing_valid": transcript.timing_valid}
                        if transcript.timing_valid is not None
                        else {}
                    ),
                    **(
                        {
                            "timing_diagnostics": {
                                "code": "timestamp_unreliable",
                                "raw_model_segments": transcript.raw_model_segments,
                                "usable_for_location": False,
                            }
                        }
                        if transcript.timing_valid is False
                        else {}
                    ),
                },
            },
            warnings=("仅依据下载的字幕或音轨，未识别全部视频画面；转写可能有误。",)
            + ((TIMESTAMP_UNRELIABLE_WARNING,) if transcript.timing_valid is False else ()),
        )
        return MediaExtraction(enriched, (artifact,))
