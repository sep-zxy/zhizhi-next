"""Package a separate run-only acceptance entry without changing the adapter patch."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(repo, verified_package, output):
    sys.dont_write_bytecode = True
    if output.exists():
        raise ValueError("Use a fresh output directory")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repo):
        raise ValueError("Commit tool sources before packaging")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    source = repo / "deploy/windows-portable/codex-smoke"
    spec = importlib.util.spec_from_file_location("smoke_integrity", source / "smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Only the read-only checksum function runs in cloud, never main/execute_adapter.
    count = module.verify_package(verified_package)
    tool = output / "zhizhi-codex-adapter-smoke"
    tool.mkdir(parents=True)
    for name in ("smoke.py", "README.md"):
        shutil.copy2(source / name, tool / name)
    (tool / "run.cmd").write_bytes(
        (source / "run.cmd").read_text().replace("\n", "\r\n").encode("ascii")
    )
    manifest = {
        "tool_source_commit": commit,
        "adapter_source_commit": module.ADAPTER_COMMIT,
        "required_patch_sha256": module.PATCH_SHA256,
        "required_distribution_sha256": module.DISTRIBUTION_SHA256,
        "cloud_checks": {
            "test_codex_smoke": "13 offline unit tests passed",
            "actual_patched_package_immutable_files": count,
            "real_auth_or_model_call": False,
            "windows_smoke": "not_run",
        },
        "files": {path.name: sha(path) for path in sorted(tool.iterdir())},
    }
    (tool / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    archive = output / "zhizhi-codex-adapter-smoke.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for path in sorted(tool.iterdir()):
            target.write(path, path.name)
    artifact = {
        "file": str(archive), "sha256": sha(archive),
        "bytes": archive.stat().st_size, **manifest,
    }
    (output / "artifact.json").write_text(
        json.dumps(artifact, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--verified-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.repo.resolve(), args.verified_package.resolve(), args.output.resolve())
