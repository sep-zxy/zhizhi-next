"""Read-only verification of a pinned Git delivery candidate; no install/build/login."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (not relative or path.is_absolute() or ".." in path.parts or "\\" in relative
            or ":" in relative or str(path) != relative
            or any(ord(c) < 32 for c in relative)
            or any(part.casefold() == ".git" for part in path.parts)):
        raise ValueError("Unsafe delivery path")
    target = root.joinpath(*path.parts)
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("Delivery links or junctions are forbidden")
        if ancestor == root:
            break
    return target


def verify(root: Path, expected_commit: str | None = None) -> dict:
    root = root.absolute()
    if expected_commit is not None:
        if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
            raise ValueError("Use a full verified delivery commit")
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        if actual != expected_commit:
            raise ValueError("Wrong Git delivery commit")
    pointer = json.loads(safe(root, "current.json").read_text(encoding="utf-8"))
    if (pointer.get("schema_version") != 1 or pointer.get("final_delivery") is not False
            or pointer.get("status") != "candidate_for_windows_acceptance"
            or not re.fullmatch(r"[0-9a-f]{40}", pointer.get("source_commit", ""))):
        raise ValueError("Invalid candidate pointer provenance")
    release_id = pointer["release_id"]
    if not re.fullmatch(r"[0-9a-f]{12}-[0-9]+-[0-9]+", release_id):
        raise ValueError("Invalid immutable release identifier")
    release = safe(root, "releases/" + release_id)
    manifest_path = safe(release, "release.json")
    if digest(manifest_path) != pointer["release_manifest_sha256"]:
        raise ValueError("Release manifest does not match current pointer")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("final_delivery") is not False
            or manifest.get("status") != "candidate_for_windows_acceptance"
            or manifest.get("windows_e2e") != "not_run"
            or manifest["source_commit"] != pointer["source_commit"]
            or manifest["release_id"] != release_id
            or not release_id.startswith(manifest["source_commit"][:12] + "-")):
        raise ValueError("Release provenance mismatch")
    expected = manifest["files"]
    for name, sha in expected.items():
        if not re.fullmatch("[0-9a-f]{64}", sha) or digest(safe(release, name)) != sha:
            raise ValueError("Release integrity failed: " + name)
    observed = set()
    for path in release.rglob("*"):
        relative = path.relative_to(release).as_posix()
        safe(release, relative)
        if path.is_file():
            observed.add(relative)
    if observed != set(expected) | {"release.json"}:
        raise ValueError("Unexpected, missing, or runtime-created file in pristine candidate")
    # Verify the distribution's own immutable manifest as an independent layer.
    distribution_path = safe(release, "provenance/distribution.json")
    if digest(distribution_path) != manifest["distribution_sha256"]:
        raise ValueError("Nested distribution seal differs from release provenance")
    distribution = json.loads(distribution_path.read_text())
    if distribution.get("schema_version") != 1 or distribution.get("target") != "windows-x64":
        raise ValueError("Invalid nested distribution target")
    for name, sha in distribution["files"].items():
        if name not in expected or digest(safe(release, name)) != sha:
            raise ValueError("Nested distribution integrity failed")
    return {
        "status": "git_candidate_integrity_passed", "release_id": release_id,
        "source_commit": manifest["source_commit"], "files": len(expected),
        "github_run_url": manifest["github_run_url"], "windows_e2e": "not_established",
        "final_delivery": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.expected_commit), ensure_ascii=False))
