"""Cloud-only construction of a small, reversible patch for the exact shipped ZIP."""

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

BASE_ZIP_SHA256 = "f326f38b3c5ef8df821fa3b8d8f8c48d8bfa8bc18fbb12d1e74931ef91a473d0"
BACKEND_FILES = [
    "model/__init__.py",
    "model/config.py",
    "model/contracts.py",
    "model/validation.py",
    "model/codex_runner.py",
    "model/_codex_job.py",
    "workflow.py",
]


def sha(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build(repo, base_zip, out):
    sys.dont_write_bytecode = True
    if sha(base_zip) != BASE_ZIP_SHA256:
        raise ValueError("Wrong base archive; refusing to patch a different release")
    if out.exists():
        raise ValueError("Use a fresh output directory")
    out.mkdir(parents=True)
    base = out / "verification-base"
    patched = out / "verification-applied"
    patch = out / "zhizhi-codex-cli-patch"
    patch.mkdir()
    # The exact pinned archive is trusted; ZIP paths are still validated before extraction.
    with zipfile.ZipFile(base_zip) as archive:
        for info in archive.infolist():
            parts = Path(info.filename).parts
            if (
                Path(info.filename).is_absolute()
                or ".." in parts
                or "\\" in info.filename
            ):
                raise ValueError("Unsafe original ZIP entry")
        archive.extractall(base)
    shutil.copytree(base, patched)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repo, text=True
    )
    if dirty:
        raise ValueError("Commit source before packaging")
    for relative in BACKEND_FILES:
        source = repo / "projects/growth/src/ahadiff/knowledge_platform" / relative
        destination = patched / "app/backend/ahadiff/knowledge_platform" / relative
        shutil.copy2(source, destination)
    backend = patched / "app/backend"
    component = {
        "name": "zhizhi-backend",
        "version": "1.4.0+codex-cli.1",
        "source_commit": source_commit,
        "entrypoints": {"service": "ahadiff.knowledge_platform"},
        "licenses": ["LICENSE"],
        "runtime_requirements": {
            "python": "bundled CPython 3.13.16",
            "new_dependencies": [],
        },
        "known_limits": [
            "Default remains Pi; Codex requires explicit opt-in and existing login",
            "Codex actual model is unknown in JSONL; requested model is separately audited",
            "Windows adapter E2E not run; tool-event detection is not rollback",
        ],
        "files": {
            p.relative_to(backend).as_posix(): sha(p)
            for p in sorted(backend.rglob("*"))
            if p.is_file() and p.name != "component.json"
        },
    }
    dump(backend / "component.json", component)
    assembly_path = patched / "provenance/assembly.json"
    assembly = json.loads(assembly_path.read_text())
    assembly["backend_source_commit"] = source_commit
    assembly["components"]["backend"] = {
        k: v for k, v in component.items() if k != "files"
    }
    assembly["patch"] = {
        "name": "codex-cli-adapter",
        "source_commit": source_commit,
        "base_zip_sha256": BASE_ZIP_SHA256,
        "windows_tested": False,
    }
    dump(assembly_path, assembly)
    dump(
        patched / "provenance/codex-cli-adapter.json",
        {
            "source_commit": source_commit,
            "base_zip_sha256": BASE_ZIP_SHA256,
            "cloud_verification": "fixture protocol/unit/integration only; see source validation report",
            "real_model_call_in_cloud": False,
            "windows_adapter_e2e": "not_run",
            "runtime_dependency_changes": [],
            "actual_model_attestation": "unavailable in exec JSONL",
        },
    )
    before_dist = base / "provenance/distribution.json"
    record = json.loads(before_dist.read_text())
    for path in sorted(patched.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(patched).as_posix()
        if (
            relative == "provenance/distribution.json"
            or relative == "config/launch.json"
        ):
            continue
        record["files"][relative] = sha(path)
    dump(patched / "provenance/distribution.json", record)
    changes = []
    before, after = {}, {}
    for path in sorted(patched.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(patched).as_posix()
        previous = base / relative
        if previous.is_file() and sha(path) == sha(previous):
            continue
        changes.append(relative)
        for directory, source, mapping in (
            ("before", previous, before),
            ("after", path, after),
        ):
            if source.is_file():
                destination = patch / directory / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                mapping[relative] = sha(destination)
    dump(
        patch / "patch.json",
        {
            "schema_version": 1,
            "target": "windows-x64",
            "source_commit": source_commit,
            "base_zip_sha256": BASE_ZIP_SHA256,
            "changes": changes,
            "before": before,
            "after": after,
            "mutable_paths_touched": [],
            "runtime_dependency_changes": [],
        },
    )
    shutil.copy2(
        repo / "deploy/windows-portable/patch/apply_patch.py", patch / "apply_patch.py"
    )
    for action in ("check", "apply", "rollback"):
        (patch / (action + ".cmd")).write_bytes(
            (
                '@echo off\r\nsetlocal\r\n"C:\\Code\\zhizhi-e2e-20261020\\runtime\\python\\python.exe" '
                '-B -s -X utf8 "%~dp0apply_patch.py" '
                + action
                + "\r\nexit /b %ERRORLEVEL%\r\n"
            ).encode("ascii")
        )
    shutil.copy2(
        repo / "docs/implementation/CODEX_CLI_ADAPTER.md", patch / "ADAPTER-CONTRACT.md"
    )
    shutil.copy2(
        repo / "docs/implementation/CODEX_CLI_VALIDATION.md", patch / "CLOUD-TESTS.md"
    )
    shutil.copy2(repo / "deploy/windows-portable/patch/README.md", patch / "README.md")
    shutil.copy2(
        repo / "deploy/windows-portable/patch/model-codex.example.json",
        patch / "model-codex.example.json",
    )
    spec = importlib.util.spec_from_file_location(
        "codex_patch", patch / "apply_patch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Actually apply to a fresh copy, verify every immutable/nested manifest, rollback and reapply.
    actual = out / "verification-actual"
    shutil.copytree(base, actual)
    check = mod.apply(actual, patch, "check")
    applied = mod.apply(actual, patch, "apply")
    expected = {
        p.relative_to(patched).as_posix(): sha(p)
        for p in patched.rglob("*")
        if p.is_file()
    }
    observed = {
        p.relative_to(actual).as_posix(): sha(p)
        for p in actual.rglob("*")
        if p.is_file()
    }
    if expected != observed:
        raise ValueError("Applied bytes differ from cloud assembly")
    rollback = mod.apply(actual, patch, "rollback")
    original = {
        p.relative_to(base).as_posix(): sha(p) for p in base.rglob("*") if p.is_file()
    }
    if original != {
        p.relative_to(actual).as_posix(): sha(p)
        for p in actual.rglob("*")
        if p.is_file()
    }:
        raise ValueError("Rollback did not restore original bytes")
    mod.apply(actual, patch, "apply")
    dump(
        out / "verification.json",
        {
            "source_commit": source_commit,
            "check": check,
            "apply": applied,
            "rollback": rollback,
            "immutable_files": len(record["files"]),
            "changed_files": changes,
            "exact_diff_verified": True,
            "rollback_exact": True,
            "config_state_unchanged": True,
            "windows_e2e": "not_run",
        },
    )
    archive = out / "zhizhi-codex-cli-patch.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for path in sorted(patch.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(patch).as_posix())
    if archive.stat().st_size >= 20_000_000:
        raise ValueError("Patch exceeds delivery size limit")
    dump(
        out / "artifact.json",
        {
            "patch": str(archive),
            "sha256": sha(archive),
            "bytes": archive.stat().st_size,
            "source_commit": source_commit,
        },
    )
    print((out / "artifact.json").read_text())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--base-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.repo.resolve(), args.base_zip.resolve(), args.output.resolve())
