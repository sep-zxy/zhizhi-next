"""Run-only, reversible immutable-file patch; no build, installation or login."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

EXPECTED_ROOT = Path(r"C:\Code\zhizhi-e2e-20261020")
MANIFEST = "provenance/distribution.json"


def sha(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def safe(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe patch path")
    target = root.joinpath(*path.parts)
    for ancestor in (target, *target.parents):
        if ancestor.is_symlink() or (
            hasattr(ancestor, "is_junction") and ancestor.is_junction()
        ):
            raise ValueError("Links/junctions are forbidden")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes root")
    return target


def permitted(relative):
    return relative.startswith("app/backend/") or relative in {
        MANIFEST,
        "provenance/assembly.json",
        "provenance/codex-cli-adapter.json",
    }


def verify(root, record):
    for relative, digest in record["files"].items():
        path = safe(root, relative)
        if not path.is_file() or sha(path) != digest:
            raise ValueError("Immutable file mismatch: " + relative)
    for prefix in ("app", "runtime"):
        for path in safe(root, prefix).rglob("*"):
            relative = path.relative_to(root).as_posix()
            safe(root, relative)
            if path.is_file() and relative not in record["files"]:
                raise ValueError("Unlisted immutable code: " + relative)
    # Only assembly-owned component manifests, not upstream npm files with this name.
    for prefix in ("app/backend", "app/pi", "app/wiki"):
        component_path = safe(root, prefix + "/component.json")
        if component_path.is_file():
            component = json.loads(component_path.read_text(encoding="utf-8"))
            expected = component["files"]
            for relative, digest in expected.items():
                if sha(safe(root, prefix + "/" + relative)) != digest:
                    raise ValueError("Component mismatch: " + prefix + "/" + relative)
            for path in component_path.parent.rglob("*"):
                relative = path.relative_to(component_path.parent).as_posix()
                if (
                    path.is_file()
                    and relative != "component.json"
                    and relative not in expected
                ):
                    raise ValueError(
                        "Unlisted component file: " + prefix + "/" + relative
                    )


def load_patch(patch_root):
    plan = json.loads((patch_root / "patch.json").read_text(encoding="utf-8"))
    if plan["schema_version"] != 1 or plan["target"] != "windows-x64":
        raise ValueError("Unsupported patch contract")
    if set(plan["changes"]) != set(plan["before"]) | set(plan["after"]):
        raise ValueError("Incomplete patch file list")
    for relative in plan["changes"]:
        if not permitted(relative):
            raise ValueError("Patch may not change state/config/runtime: " + relative)
    for directory in ("before", "after"):
        expected = plan[directory]
        for relative, digest in expected.items():
            path = safe(patch_root, directory + "/" + relative)
            if not path.is_file() or sha(path) != digest:
                raise ValueError("Patch payload checksum mismatch")
        for path in (patch_root / directory).rglob("*"):
            relative = path.relative_to(patch_root / directory).as_posix()
            safe(patch_root, directory + "/" + relative)
            if path.is_file() and relative not in expected:
                raise ValueError("Unlisted patch payload")
    return plan


def acquire_launcher_lock(root):
    # Do not create/change state files. A previously launched package has this lock.
    lock_file = safe(root, "data/launcher.lock")
    if not lock_file.is_file():
        raise ValueError("Launcher lock missing; mutation refused without mutual exclusion")
    import msvcrt

    lock = lock_file.open("r+b")
    try:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except BaseException:
        lock.close()
        raise
    return lock


def apply(root, patch_root, action):
    root, patch_root = root.absolute(), patch_root.absolute()
    if os.name == "nt" and os.path.normcase(str(root)) != os.path.normcase(
        str(EXPECTED_ROOT)
    ):
        raise ValueError("Only the approved portable directory may be patched")
    # Acquire before preflight as well as writes, not only after verification.
    lock = (
        acquire_launcher_lock(root)
        if os.name == "nt" and action in {"apply", "rollback"}
        else None
    )
    try:
        return apply_locked(root, patch_root, action)
    finally:
        if lock is not None:
            lock.close()


def apply_locked(root, patch_root, action):
    safe(root, MANIFEST)
    plan = load_patch(patch_root)
    before_manifest = json.loads(
        (patch_root / "before" / MANIFEST).read_text(encoding="utf-8")
    )
    after_manifest = json.loads(
        (patch_root / "after" / MANIFEST).read_text(encoding="utf-8")
    )
    if action in {"check", "apply"}:
        if sha(root / MANIFEST) != plan["before"][MANIFEST]:
            raise ValueError(
                "This patch requires the exact original distribution version"
            )
        verify(root, before_manifest)
        for relative in set(plan["after"]) - set(plan["before"]):
            if safe(root, relative).exists():
                raise ValueError("New patch file already exists")
        if action == "check":
            return {
                "status": "base_verified",
                "base_zip_sha256": plan["base_zip_sha256"],
            }
        direction, target_manifest = "after", after_manifest
    elif action == "rollback":
        # Permit a partially applied transaction, but never overwrite unrelated edits.
        for relative, digest in before_manifest["files"].items():
            if relative not in plan["changes"]:
                if sha(safe(root, relative)) != digest:
                    raise ValueError(
                        "Unrelated immutable file changed; rollback stopped"
                    )
        for relative in plan["changes"]:
            path = safe(root, relative)
            current = sha(path) if path.is_file() else None
            if current not in {
                plan["before"].get(relative),
                plan["after"].get(relative),
            }:
                raise ValueError("Unexpected file state; rollback stopped: " + relative)
        direction, target_manifest = "before", before_manifest
    else:
        raise ValueError("Unknown action")
    # Distribution manifest last: interruption leaves startup fail-closed.
    ordered = sorted(set(plan["changes"]) - {MANIFEST}) + [MANIFEST]
    for relative in ordered:
        destination = safe(root, relative)
        if relative not in plan[direction]:
            if destination.exists():
                destination.unlink()
            continue
        payload = safe(patch_root, direction + "/" + relative).read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".codex-patch-tmp")
        if temporary.exists():
            raise ValueError("Unfinished staging file; inspect before retrying")
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
    verify(root, target_manifest)
    return {
        "status": "applied" if action == "apply" else "rolled_back",
        "source_commit": plan["source_commit"],
        "windows_e2e": "not_run",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "apply", "rollback"])
    parser.add_argument("--root", type=Path, default=EXPECTED_ROOT)
    args = parser.parse_args()
    try:
        result = apply(args.root, Path(__file__).resolve().parent, args.action)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
