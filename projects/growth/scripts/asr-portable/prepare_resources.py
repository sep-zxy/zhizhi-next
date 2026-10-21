"""Cloud-only resource staging. Never installs or executes Windows binaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tomllib
import urllib.request
import zipfile
from pathlib import Path

from packaging.markers import Marker, default_environment
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename


def selected_packages(lock):
    env = default_environment() | {
        "python_version": "3.13",
        "python_full_version": "3.13.16",
        "sys_platform": "win32",
        "platform_system": "Windows",
        "platform_machine": "AMD64",
        "os_name": "nt",
        "extra": "",
    }
    packages = lock["package"]
    result = {}

    def visit(name, version=None):
        if name in result:
            return
        candidates = [
            p
            for p in packages
            if p["name"] == name and (version is None or p["version"] == version)
        ]
        candidates = [
            p
            for p in candidates
            if not p.get("resolution-markers")
            or any(Marker(m).evaluate(env) for m in p["resolution-markers"])
        ]
        if len(candidates) != 1:
            raise ValueError(f"Ambiguous lock entry: {name}: {len(candidates)}")
        p = candidates[0]
        result[name] = p
        for dep in p.get("dependencies", []):
            if not dep.get("marker") or Marker(dep["marker"]).evaluate(env):
                visit(dep["name"], dep.get("version"))

    visit("faster-whisper")
    return result


def choose_wheel(package):
    tags = list(cpython_tags((3, 13), platforms=["win_amd64"])) + list(
        compatible_tags((3, 13), interpreter="cp313", platforms=["win_amd64"])
    )
    rank = {tag: i for i, tag in enumerate(tags)}
    matches = []
    for wheel in package.get("wheels", []):
        filename = wheel["url"].rsplit("/", 1)[-1]
        accepted = parse_wheel_filename(filename)[3] & rank.keys()
        if accepted:
            matches.append((min(rank[t] for t in accepted), wheel))
    if not matches:
        raise ValueError(f"No Windows x64 CPython 3.13 wheel: {package['name']}")
    return min(matches, key=lambda item: item[0])[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--source-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    (root / "wheels").mkdir(parents=True, exist_ok=True)
    (root / "licenses").mkdir(exist_ok=True)
    records = []
    for name, p in sorted(selected_packages(tomllib.loads(args.lock.read_text())).items()):
        w = choose_wheel(p)
        dest = root / "wheels" / w["url"].rsplit("/", 1)[-1]
        if not dest.exists():
            with urllib.request.urlopen(w["url"], timeout=120) as response:
                dest.write_bytes(response.read())
        assert digest(dest) == w["hash"].removeprefix("sha256:"), dest
        records.append(
            {
                "path": str(dest.relative_to(root)),
                "bytes": dest.stat().st_size,
                "sha256": digest(dest),
                "source": w["url"],
                "package": name,
                "version": p["version"],
            }
        )
        with zipfile.ZipFile(dest) as z:
            for member in z.namelist():
                if not member.endswith("/") and (
                    ".dist-info/licenses/" in member
                    or member.rsplit("/", 1)[-1]
                    .lower()
                    .startswith(("license", "copying", "notice"))
                ):
                    target = root / "licenses" / name / member
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(member))
        print(dest.name, flush=True)
    verified = json.loads((args.source_model.parent / "verified-model.json").read_text())
    for record in verified["files"]:
        source = args.source_model / record["name"]
        assert digest(source) == record["sha256"]
        dest = root / "data/models/asr/tiny.en" / record["name"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        records.append(record | {"path": str(dest.relative_to(root))})
    for source_name in ("model-metadata.json", "verified-model.json"):
        shutil.copyfile(args.source_model.parent / source_name, root / source_name)
    license_sources = {
        "whisper-model": "https://raw.githubusercontent.com/openai/whisper/v20250625/LICENSE",
        "ctranslate2": "https://raw.githubusercontent.com/OpenNMT/CTranslate2/v4.8.3/LICENSE",
        "tokenizers": "https://raw.githubusercontent.com/huggingface/tokenizers/v0.23.3/LICENSE",
        "flatbuffers": "https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE",
    }
    for name, url in license_sources.items():
        dest = root / "licenses" / name / "UPSTREAM-LICENSE"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                dest.write_bytes(response.read())
        records.append(
            {
                "path": str(dest.relative_to(root)),
                "bytes": dest.stat().st_size,
                "sha256": digest(dest),
                "source": url,
            }
        )
    sample = args.source_model.parent / "synthetic-speech.wav"
    if digest(sample) != "ecc0b6057327c4e4ef89fd34e8555ea9ed6ca347666201c918f4fe1c72b9f6dd":
        raise ValueError("Synthetic sample integrity mismatch")
    (root / "samples").mkdir(exist_ok=True)
    shutil.copyfile(sample, root / "samples/synthetic-speech.wav")
    records.append(
        {
            "path": "samples/synthetic-speech.wav",
            "bytes": sample.stat().st_size,
            "sha256": digest(sample),
            "source": "Locally synthesized CMU Flite slt; ASR_OFFLINE_TEST.md",
        }
    )
    for path in sorted((root / "licenses").rglob("*")):
        if path.is_file() and str(path.relative_to(root)) not in {r["path"] for r in records}:
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "bytes": path.stat().st_size,
                    "sha256": digest(path),
                    "source": "extracted from verified PyPI wheel; see wheel manifest",
                }
            )
    manifest = {
        "schema_version": 1,
        "platform": "win_amd64",
        "python": "3.13.16",
        "model_revision": verified["revision"],
        "model_path": "data/models/asr/tiny.en",
        "device": "cpu",
        "compute_type": "int8",
        "windows_runtime_validation": "PENDING",
        "license_status": "license_pending",
        "license_review": "See LICENSE_REVIEW.md; third-party binary distribution review required",
        "files": records,
    }
    (root / "ASR_RESOURCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf8"
    )


if __name__ == "__main__":
    main()
