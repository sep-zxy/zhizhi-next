"""Offline Windows acceptance probe; no downloads, installer or account access."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import importlib
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path

REQUIRED_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")


def verify_files(root: Path, records: list[dict]) -> list[str]:
    failures = []
    for record in records:
        # Wheels are cloud staging inputs, not required after installation.
        if not record["path"].startswith("data/models/"):
            continue
        path = root / record["path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            failures.append(record["path"])
    return failures


def probe(root: Path, manifest: Path, sample: Path | None = None) -> dict:
    result = {
        "status": "BLOCKED",
        "platform": sys.platform,
        "machine": platform.machine(),
        "python": platform.python_version(),
        "scope": "Windows offline ASR runtime; not real-channel E2E",
    }
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        return result | {"code": "WINDOWS_X64_REQUIRED"}
    data = json.loads(manifest.read_text(encoding="utf8"))
    failures = verify_files(root, data["files"])
    if failures:
        return result | {"code": "MODEL_HASH_MISMATCH", "files": failures}
    missing = []
    for name in REQUIRED_DLLS:
        try:
            ctypes.WinDLL(name)
        except OSError:
            missing.append(name)
    if missing:
        return result | {
            "code": "VC_RUNTIME_MISSING",
            "missing_dlls": missing,
            "action": "STOP. No automatic installation. Request approval for system installation.",
        }
    try:
        versions = {}
        for name in ("av", "numpy", "ctranslate2", "onnxruntime", "tokenizers", "faster_whisper"):
            module = importlib.import_module(name)
            versions[name] = getattr(module, "__version__", "unknown")
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str((root / data["model_path"]).resolve()),
            device="cpu",
            compute_type="int8",
            local_files_only=True,
            use_auth_token=False,
            cpu_threads=2,
        )
        del model
        result.update(versions=versions)
        if sample is not None:
            content = sample.read_bytes()
            expected = "ecc0b6057327c4e4ef89fd34e8555ea9ed6ca347666201c918f4fe1c72b9f6dd"
            if hashlib.sha256(content).hexdigest() != expected:
                return result | {"code": "SYNTHETIC_SAMPLE_HASH_MISMATCH"}
            from ahadiff.knowledge_platform.media.transcripts import LocalWhisperASR

            transcript = asyncio.run(
                LocalWhisperASR(
                    (root / data["model_path"]).resolve(), timeout_seconds=120
                ).transcribe(content)
            )
            text = transcript.text.lower()
            if not all(
                phrase in text
                for phrase in ("quick brown fox", "lazy dog", "local speech recognition test")
            ):
                return result | {
                    "code": "SYNTHETIC_ASR_TEXT_MISMATCH",
                    "transcript": asdict(transcript),
                }
            result.update(
                transcript=asdict(transcript),
                sample_kind="synthetic speech; not natural speech or real-platform E2E",
            )
        return result | {
            "status": "PASS",
            "code": "ASR_SYNTHETIC_SMOKE_OK" if sample else "ASR_MODEL_LOAD_OK",
        }
    except Exception as error:
        return result | {
            "code": "ASR_RUNTIME_ERROR",
            "error_type": type(error).__name__,
            "detail": str(error),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sample", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import os

    os.environ.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1"
    )
    result = probe(args.root, args.manifest, args.sample)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
