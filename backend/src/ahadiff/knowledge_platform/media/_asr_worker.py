"""Isolated local-file speech worker. No URL, model download or remote API support.

Invoked by transcripts.LocalWhisperASR with bounded media bytes on stdin. This is
resource containment, not a claim that a subprocess replaces a hardened sandbox.
"""

from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path
from typing import Any


class MediaFailure(Exception):
    def __init__(self, code: str):
        self.code = code


def decode_media(content: bytes, max_duration: int) -> Any:
    """Use installed PyAV with no network protocols and bounded resampled audio."""
    import av
    import numpy as np

    frames: list[Any] = []
    sample_count = 0
    try:
        with av.open(
            io.BytesIO(content),
            mode="r",
            options={
                "protocol_whitelist": "pipe",
                "format_whitelist": "wav,mp3,flac,ogg,mov,matroska,webm,aac",
            },
        ) as container:
            if not container.streams.audio:
                raise MediaFailure("MEDIA_INVALID")
            stream = container.streams.audio[0]
            if (
                stream.duration is not None
                and stream.time_base is not None
                and float(stream.duration * stream.time_base) > max_duration
            ):
                raise MediaFailure("MEDIA_DURATION_LIMIT")
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            for count, frame in enumerate(container.decode(stream), start=1):
                if count > 100_000:
                    raise MediaFailure("MEDIA_DURATION_LIMIT")
                for result in resampler.resample(frame):
                    array = result.to_ndarray().reshape(-1)
                    sample_count += len(array)
                    if sample_count > max_duration * 16000:
                        raise MediaFailure("MEDIA_DURATION_LIMIT")
                    frames.append(array)
            for result in resampler.resample(None):
                array = result.to_ndarray().reshape(-1)
                sample_count += len(array)
                if sample_count > max_duration * 16000:
                    raise MediaFailure("MEDIA_DURATION_LIMIT")
                frames.append(array)
    except MediaFailure:
        raise
    except Exception:
        raise MediaFailure("MEDIA_INVALID") from None
    if not frames or sample_count == 0:
        raise MediaFailure("ASR_NO_SPEECH")
    audio = np.concatenate(frames).astype(np.float32, copy=False)
    if not np.isfinite(audio).all() or float(np.max(np.abs(audio))) < 1e-5:
        raise MediaFailure("ASR_NO_SPEECH")
    return audio


def timing_metadata(segments: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    """Check decoded-audio bounds, not alignment accuracy; never silently clamp."""
    valid = all(0 <= item["start"] <= item["end"] <= duration for item in segments)
    return {
        "segments": segments if valid else [],
        "timing_valid": valid,
        "raw_model_segments": [] if valid else segments,
    }


def run(model_path: Path, max_duration: int, content: bytes) -> dict[str, Any]:
    if not model_path.is_absolute() or any(
        not (model_path / name).is_file()
        for name in (
            "model.bin",
            "config.json",
            "tokenizer.json",
        )
    ):
        raise MediaFailure("ASR_NOT_CONFIGURED")
    audio = decode_media(content, max_duration)
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
        local_files_only=True,
        use_auth_token=False,
    )
    generated, info = model.transcribe(
        audio,
        task="transcribe",
        beam_size=1,
        best_of=1,
        temperature=0,
        vad_filter=False,
        condition_on_previous_text=False,
        word_timestamps=False,
    )
    segments = []
    length = 0
    for segment in generated:
        text = segment.text.strip()
        start, end = float(segment.start), float(segment.end)
        if not text or segment.no_speech_prob > 0.6:
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise MediaFailure("ASR_FAILED")
        if end > max_duration + 1 or len(segments) >= 5000:
            raise MediaFailure("MEDIA_DURATION_LIMIT")
        length += len(text)
        if length > 100_000:
            raise MediaFailure("ASR_FAILED")
        segments.append({"start": start, "end": end, "text": text})
    if not segments:
        raise MediaFailure("ASR_NO_SPEECH")
    return {
        "status": "succeeded",
        "text": "\n".join(item["text"] for item in segments),
        **timing_metadata(segments, len(audio) / 16000),
        "duration_seconds": len(audio) / 16000,
        "language": info.language,
    }


def main() -> None:
    try:
        if sys.platform != "win32":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
            resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        content = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if not content or len(content) > 8 * 1024 * 1024:
            raise MediaFailure("MEDIA_INVALID")
        result = run(Path(sys.argv[1]), int(sys.argv[2]), content)
        output = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(output) > 512 * 1024:
            raise MediaFailure("ASR_FAILED")
        sys.stdout.buffer.write(output)
    except MediaFailure as error:
        print(json.dumps({"status": "failed", "code": error.code}))
        raise SystemExit(2) from None
    except Exception:
        print(json.dumps({"status": "failed", "code": "ASR_FAILED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
