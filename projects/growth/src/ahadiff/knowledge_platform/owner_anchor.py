"""Reuse one existing DPAPI identity in place; new runs never replenish old grants.

Only the fixed approved acceptance tree is addressable. The registry and run
reservations contain no credentials. They are append-only, not an OS sandbox
against a process already controlling the same Windows account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import owner_identity as owner

REFERENCE = "zhizhi-owner-anchor-reference-v1"
RELEASE = re.compile(r"[0-9a-f]{12}-[0-9]+-[0-9]+")


def registry_path() -> Path:
    approved = Path(owner.APPROVED_ROOT)
    return owner._safe(approved, approved / "data/owner-identity-anchor.json")


def configuration_lock():
    """Serialize first bootstrap and fixed-anchor selection across releases."""
    import portalocker

    path = registry_path().with_name("owner-identity-configuration.lock")
    owner._safe(Path(owner.APPROVED_ROOT), path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return portalocker.Lock(path, mode="a", timeout=0,
                           flags=portalocker.LOCK_EX | portalocker.LOCK_NB)


def runtime_binding(root: Path) -> dict[str, str] | None:
    annotation = run_annotation(root)
    if not annotation:
        return None
    from .owner_acceptance import _json

    release = _json(owner._safe(root, root / "release.json"))
    return {"release_id": release["release_id"], "source_commit": release["source_commit"]}


def _release_root(release: str) -> Path:
    if not isinstance(release, str) or not RELEASE.fullmatch(release):
        raise owner.OwnerIdentityError("OWNER_ANCHOR_RELEASE_INVALID")
    approved = Path(owner.APPROVED_ROOT)
    return owner._safe(approved, approved / "git-delivery/releases" / release)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _anchor(record: dict[str, Any]) -> tuple[Path, owner.ProtectedOwnerIdentity]:
    if set(record) != {"version", "anchor_release", "identity_sha256"} or type(record["version"]) is not int or record["version"] != 1:
        raise owner.OwnerIdentityError("OWNER_ANCHOR_INVALID")
    root = _release_root(record["anchor_release"])
    path = owner._safe(root, root / "data/owner-acceptance/identity.json")
    document = owner._read(path)
    if _digest(path) != record["identity_sha256"]:
        raise owner.OwnerIdentityError("OWNER_ANCHOR_CHANGED")
    # Explicitly decode only the original protected envelope; never follow a
    # reference recursively or accept a copied/rebound package_root payload.
    identity = owner._decode_legacy_identity(root, document)
    owner._validate_profile(root, identity)
    return root, identity


def _runs(record: dict[str, Any], identity: owner.ProtectedOwnerIdentity):
    from .acceptance_model import ScopeReceipts

    approved = Path(owner.APPROVED_ROOT)
    return ScopeReceipts(approved, owner._safe(approved, approved / "data/owner-runs"), record, identity.token)


def load_reference(root: Path, document: dict[str, Any]) -> owner.ProtectedOwnerIdentity:
    record = owner._read(registry_path())
    _, identity = _anchor(record)
    expected = {"format": REFERENCE, "anchor": record, "run_release": root.name}
    if document != expected or _release_root(root.name) != root:
        raise owner.OwnerIdentityError("OWNER_REFERENCE_MISMATCH")
    run = _runs(record, identity).read("run:" + root.name)
    if (run is None or run.get("kind") != "explicit_new_acceptance_run"
            or run.get("anchor") != record
            or run.get("release_sha256") != _digest(owner._safe(root, root / "release.json"))):
        raise owner.OwnerIdentityError("OWNER_RUN_RESERVATION_REQUIRED")
    return identity


def scope_receipts(root: Path, name: str, binding: dict[str, Any], token: str):
    """New run ledgers survive release-local state loss; legacy ledgers stay put."""
    from .acceptance_model import ScopeReceipts

    if name not in {"acceptance-receipts", "pi-receipts"}:
        raise owner.OwnerIdentityError("OWNER_LEDGER_KIND_INVALID")
    document = owner._read(owner._safe(root, root / "data/owner-acceptance/identity.json"))
    if document.get("format") != REFERENCE:
        return ScopeReceipts(root, root / "data/owner-acceptance" / name, binding, token)
    load_reference(root, document)
    approved = Path(owner.APPROVED_ROOT)
    directory = owner._safe(approved, approved / "data/owner-runs" / root.name / name)
    if not directory.is_dir():
        raise owner.OwnerIdentityError("OWNER_RUN_LEDGER_MISSING")
    return ScopeReceipts(approved, directory, binding, token)


def run_annotation(root: Path) -> dict[str, Any]:
    document = owner._read(owner._safe(root, root / "data/owner-acceptance/identity.json"))
    if document.get("format") != REFERENCE:
        return {}
    load_reference(root, document)
    return {"acceptance_run": {"kind": "explicit_new_acceptance_run",
            "release": root.name, "anchor": document["anchor"],
            "old_budget_replenished": False}}


def reuse_owner_identity(*, approved: bool, new_run: bool, anchor_release: str | None = None) -> str:
    if approved is not True or new_run is not True:
        raise owner.OwnerIdentityError("OWNER_REUSE_EXPLICIT_NEW_RUN_REQUIRED")
    owner._acceptance_root()
    with configuration_lock():
        return _reuse_owner_identity_locked(approved=approved, new_run=new_run,
                                           anchor_release=anchor_release)


def _reuse_owner_identity_locked(*, approved: bool, new_run: bool, anchor_release: str | None = None) -> str:
    from .owner_acceptance import _json, verified_binding

    if approved is not True or new_run is not True:
        raise owner.OwnerIdentityError("OWNER_REUSE_EXPLICIT_NEW_RUN_REQUIRED")
    root = owner._acceptance_root()
    release = _json(owner._safe(root, root / "release.json"))
    binding = verified_binding(root, release.get("source_commit"), release.get("release_id"))
    registry = registry_path()
    if registry.exists():
        record = owner._read(registry)
        if anchor_release is not None and anchor_release != record.get("anchor_release"):
            raise owner.OwnerIdentityError("OWNER_FIXED_ANCHOR_CONFLICT")
    else:
        anchor_root = _release_root(anchor_release)
        anchor_manifest = _json(owner._safe(anchor_root, anchor_root / "release.json"))
        verified_binding(anchor_root, anchor_manifest.get("source_commit"), anchor_manifest.get("release_id"))
        record = {"version": 1, "anchor_release": anchor_release,
                  "identity_sha256": _digest(owner._safe(anchor_root, anchor_root / "data/owner-acceptance/identity.json"))}
    anchor_root, identity = _anchor(record)
    if anchor_root == root:
        raise owner.OwnerIdentityError("OWNER_NEW_RUN_REQUIRES_DISTINCT_RELEASE")
    directory = owner._safe(root, root / "data/owner-acceptance")
    reference = {"format": REFERENCE, "anchor": record, "run_release": root.name}
    runs = _runs(record, identity)
    reservation = {"kind": "explicit_new_acceptance_run", "anchor": record,
                   "binding": binding, "release_sha256": _digest(root / "release.json"),
                   "previous_anchor_run": record["anchor_release"],
                   "budget_created": False, "old_budget_replenished": False}
    if directory.exists():
        if runs.read("run:" + root.name) != reservation:
            raise owner.OwnerIdentityError("OWNER_RUN_CONFLICT")
        owner.load_owner_identity(directory / "identity.json")
        return "already_reused"
    # Reserve before creating state. An interrupted/removed run stays consumed
    # as an initialization slot; never silently recreate an empty attempt ledger.
    if runs.read("run:" + root.name) is not None:
        raise owner.OwnerIdentityError("OWNER_RUN_ALREADY_RESERVED")
    if not registry.exists():
        registry.parent.mkdir(parents=True, exist_ok=True)
        with registry.open("xb") as stream:
            stream.write(owner._canonical(record) + b"\n")
    if owner._read(registry) != record:
        raise owner.OwnerIdentityError("OWNER_FIXED_ANCHOR_CONFLICT")
    if not runs.put("run:" + root.name, reservation):
        raise owner.OwnerIdentityError("OWNER_RUN_ALREADY_RESERVED")
    ledger_root = owner._safe(Path(owner.APPROVED_ROOT), Path(owner.APPROVED_ROOT) / "data/owner-runs" / root.name)
    ledger_root.mkdir()
    for name in ("acceptance-receipts", "pi-receipts"):
        owner._safe(Path(owner.APPROVED_ROOT), ledger_root / name).mkdir()
    directory.mkdir(mode=0o700)
    documents = {
        "identity.json": reference,
        "launch.json": owner._launch(identity.port),
        "desktop.json": {"service_url": identity.origin, "identity_file": "identity.json",
                         "identity_format": owner.FORMAT, "device_id": identity.device_id,
                         "input_classification": "personal", "providers": [], "model": {"enabled": False}},
    }
    for name, value in documents.items():
        with owner._safe(root, directory / name).open("xb") as stream:
            stream.write(owner._canonical(value) + b"\n")
    for name in ("platform", "desktop"):
        owner._safe(root, directory / name).mkdir()
    owner.load_owner_identity(directory / "identity.json")
    return "identity_reused_new_run_not_activated"


def main() -> None:
    parser = argparse.ArgumentParser(description="固定既有身份原地复用；显式创建新的独立验收运行，不恢复旧额度")
    parser.add_argument("--approve-owner-identity-reuse", action="store_true", required=True)
    parser.add_argument("--new-acceptance-run", action="store_true", required=True)
    parser.add_argument("--anchor-release")
    args = parser.parse_args()
    try:
        status = reuse_owner_identity(approved=args.approve_owner_identity_reuse,
                                      new_run=args.new_acceptance_run, anchor_release=args.anchor_release)
        print(json.dumps({"status": status, "credentials_created": False,
                          "budget_created": False, "live_e2e": "not_run"}))
    except Exception:
        print(json.dumps({"status": "blocked", "error_code": "OWNER_REUSE_FAILED_CLOSED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
