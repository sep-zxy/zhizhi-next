"""Explicit preview/activation for one real engineering input and existing login.

No credentials are created/copied; no model runs during prepare or activation.
CLI approval flags are safeguards, not consent. The full private preview must be
reviewed before the exact scope hash is approved. Default Owner launch is unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from . import owner_identity as owner
from .acceptance_model import MODEL, VERSION, ScopeReceipts
from .engineering_scope import POLICY, EngineeringScopeError, EngineeringScopeRunner
from .learning.model_bridge import ModelBridgeConfiguration, subscription_task
from .model.contracts import Classification, canonical_hash
from .owner_acceptance import _existing_cli_path, _json, verified_binding

MAX_INPUT_BYTES = 200_000
MAX_OUTPUT_BYTES = 262_144
TIMEOUT_SECONDS = 120
DESKTOP_NAME = "engineering-desktop.json"
SPEC_FIELDS = {"project_id", "binding_id", "snapshot_id", "source_ref_ids", "repository_path",
               "commit_sha", "parent_sha", "request_id", "classification"}


def descriptor():
    """The same non-network descriptor is used offline and by the Viewer."""
    from ahadiff.contracts import ProviderConfig
    return ProviderConfig(provider_class="openai", model_name=MODEL,
                          base_url="subscription://knowledge-platform", api_key_env="")


def bridge_configuration(classification: str) -> ModelBridgeConfiguration:
    return ModelBridgeConfiguration(enabled=True, allowed_provider_ids=("openai",),
                                    input_classification=cast(Classification, classification), model_name=MODEL, allow_schema_repair=False)


def model_configuration(executable: str, home: str, classification: str) -> dict[str, Any]:
    return {"enabled": True, "adapter": "codex_cli", "reuse_existing_login": True,
            "executable": [executable], "codex_home": home, "expected_version": VERSION,
            "timeout_seconds": TIMEOUT_SECONDS, "max_input_bytes": MAX_INPUT_BYTES,
            "max_output_bytes": MAX_OUTPUT_BYTES,
            "routes": [{"provider_id": "openai", "model_id": MODEL, "account_qualified": True,
                        "allowed_classifications": [classification]}]}


def _binding(root: Path) -> dict[str, Any]:
    release = _json(owner._safe(root, root / "release.json"))
    value = verified_binding(root, release.get("source_commit"), release.get("release_id"))
    # Separate policy/domain. Do not broaden or mutate the fixed material policy.
    return {key: value[key] for key in (
        "package_root", "source_commit", "release_id", "distribution_sha256"
    )} | {"policy": POLICY}


def _base(root: Path) -> dict[str, Any]:
    directory = owner._safe(root, root / "data/owner-acceptance")
    identity = owner.load_owner_identity(directory / "identity.json")
    value = _json(owner._safe(root, directory / "desktop.json"), 32768)
    expected = {"service_url": identity.origin, "identity_file": "identity.json",
                "identity_format": owner.FORMAT, "device_id": identity.device_id,
                "input_classification": "personal", "providers": [], "model": {"enabled": False}}
    if value != expected or value.get("model", {}).get("enabled") is not False:
        raise EngineeringScopeError("ENGINEERING_BASE_POLICY_CHANGED")
    return expected


def _spec(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != SPEC_FIELDS:
        raise EngineeringScopeError("ENGINEERING_SPEC_FIELDS_INVALID")
    for key in ("project_id", "binding_id", "snapshot_id", "request_id"):
        if not isinstance(value[key], str) or str(uuid.UUID(value[key])) != value[key]:
            raise EngineeringScopeError("ENGINEERING_SPEC_ID_INVALID")
    for key in ("commit_sha", "parent_sha"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{40}", value[key]):
            raise EngineeringScopeError("ENGINEERING_COMMIT_INVALID")
    refs = value["source_ref_ids"]
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 8
            or any(not isinstance(item, str) or str(uuid.UUID(item)) != item for item in refs)
            or len(set(refs)) != len(refs) or value["classification"] not in {"public", "personal"}
            or not isinstance(value["repository_path"], str)):
        raise EngineeringScopeError("ENGINEERING_SPEC_INVALID")
    return json.loads(json.dumps(value))


def _connection(root: Path):
    path = owner._safe(root, root / "data/owner-acceptance/desktop/.ahadiff/growth.sqlite")
    if not path.is_file():
        raise EngineeringScopeError("ENGINEERING_LEDGER_MISSING")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _input(root: Path, spec: dict[str, Any], *, execution: bool):
    """Reconstruct the reviewed task from real Git objects and a read-only ledger."""
    from ahadiff.growth.git_snapshot import capture_commit, repository_root, worktree_id, git_text
    from ahadiff.growth.model_opportunities import prepare_model_preview, opportunity_request
    from ahadiff.growth.local import GrowthLocalRepository

    approved = Path(owner.APPROVED_ROOT)
    if not Path(spec["repository_path"]).is_absolute():
        raise EngineeringScopeError("ENGINEERING_REPOSITORY_NOT_ABSOLUTE")
    repo = owner._safe(approved, Path(spec["repository_path"]))
    if not repo.is_relative_to(owner._safe(approved, approved / "source-samples")):
        raise EngineeringScopeError("ENGINEERING_REPOSITORY_OUTSIDE_SAMPLE_ROOT")
    if repository_root(repo) != repo:
        raise EngineeringScopeError("ENGINEERING_REPOSITORY_ROOT_MISMATCH")
    # Linked worktree Git objects may not escape the approved tree either.
    owner._safe(approved, Path(git_text(repo, "rev-parse", "--absolute-git-dir")))
    common = Path(git_text(repo, "rev-parse", "--git-common-dir"))
    common = owner._safe(approved, common if common.is_absolute() else repo / common)
    if (common / "objects/info/alternates").exists():
        raise EngineeringScopeError("ENGINEERING_ALTERNATE_OBJECTS_FORBIDDEN")
    capture = capture_commit(repo, spec["commit_sha"])
    if capture.head_sha != spec["commit_sha"] or capture.resolved_base_sha != spec["parent_sha"]:
        raise EngineeringScopeError("ENGINEERING_COMMIT_PARENT_MISMATCH")
    db = _connection(root)
    try:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT s.*, f.project_id, b.canonical_local_path, b.worktree_id "
            "FROM snapshots s JOIN features f USING(feature_id) "
            "JOIN snapshot_bindings sb USING(snapshot_id) JOIN bindings b USING(binding_id) "
            "WHERE s.snapshot_id=? AND b.binding_id=? AND b.project_id=f.project_id",
            (spec["snapshot_id"], spec["binding_id"]),
        ).fetchone()
        if (row is None or row["project_id"] != spec["project_id"]
                or Path(row["canonical_local_path"]) != repo or row["worktree_id"] != worktree_id(repo)
                or json.loads(row["capture_scope"]) != {"kind": "commit", "sha": spec["commit_sha"]}
                or any(row[key] != getattr(capture, key) for key in (
                    "resolved_base_sha", "head_sha", "effective_tree_hash", "diff_hash", "patch_text"))):
            raise EngineeringScopeError("ENGINEERING_SNAPSHOT_MISMATCH")
        refs = db.execute("SELECT * FROM source_refs WHERE snapshot_id=?", (spec["snapshot_id"],)).fetchall()
        actual = {item.relative_path: item for item in capture.changed_sources}
        if len(refs) != len(actual):
            raise EngineeringScopeError("ENGINEERING_SOURCE_SET_MISMATCH")
        for ref in refs:
            source = actual.get(ref["relative_path"])
            if (source is None or ref["blob_hash"] != source.blob_hash
                    or bytes(ref["content"]) != source.content or bool(ref["deleted"]) != source.deleted):
                raise EngineeringScopeError("ENGINEERING_SOURCE_CONTENT_CHANGED")
        policy = db.execute("SELECT * FROM project_policies WHERE project_id=?", (spec["project_id"],)).fetchone()
        if policy is None or policy["local_processing"] != 1 or policy["cloud_allowed"] != 0:
            raise EngineeringScopeError("ENGINEERING_PROJECT_POLICY_CHANGED")
        preview = prepare_model_preview(cast(GrowthLocalRepository, SimpleNamespace(connection=db)),
            spec["snapshot_id"], spec["binding_id"], "subscription", spec["source_ref_ids"],
            provider_config=descriptor())
        if len(preview.payload_text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise EngineeringScopeError("ENGINEERING_INPUT_TOO_LARGE")
        task = subscription_task(opportunity_request(preview, descriptor()),
                                 bridge_configuration(spec["classification"]))
        if execution:
            rows = db.execute(
                "SELECT a.*, m.* FROM analysis_runs a JOIN analysis_model_requests m USING(analysis_id) "
                "WHERE a.snapshot_id=? AND a.mode='live'", (spec["snapshot_id"],)).fetchall()
            if len(rows) != 1:
                raise EngineeringScopeError("ENGINEERING_SINGLE_ANALYSIS_REQUIRED")
            run = rows[0]
            attempts = db.execute("SELECT * FROM analysis_attempts WHERE analysis_id=?", (run["analysis_id"],)).fetchall()
            if (policy["model_allowed"] != 1 or run["request_id"] != spec["request_id"]
                    or run["status"] != "running" or run["supersedes_analysis_id"] is not None
                    or run["binding_id"] != spec["binding_id"] or run["provider_name"] != "subscription"
                    or run["approved_payload_hash"] != preview.approval_hash
                    or run["input_fingerprint"] != preview.approval_hash
                    or run["approved_payload_text"] != preview.payload_text
                    or run["provider_config_hash"] != preview.provider_config_hash
                    or run["selected_patch"] != preview.selected_patch
                    or json.loads(run["source_ref_ids_json"]) != list(preview.source_ref_ids)
                    or len(attempts) != 1 or attempts[0]["attempt_no"] != 1 or attempts[0]["status"] != "running"):
                raise EngineeringScopeError("ENGINEERING_ANALYSIS_NOT_APPROVED")
        return preview, task
    finally:
        db.close()


def prepare(root: Path, spec: dict[str, Any], *, codex_exe: Path, codex_home: Path,
            execution: bool = False) -> tuple[dict[str, Any], str]:
    """Rebuild the exact private scope; does not activate, login or run a model."""
    spec = _spec(spec)
    binding = _binding(root)
    _base(root)
    model = model_configuration(_existing_cli_path(codex_exe, directory=False),
                                _existing_cli_path(codex_home, directory=True), spec["classification"])
    preview, task = _input(root, spec, execution=execution)
    return {"policy": POLICY, "candidate": binding, "selection": spec, "model": model,
            "provider_config_hash": preview.provider_config_hash,
            "approved_payload_hash": preview.approval_hash,
            "payload_sha256": hashlib.sha256(preview.payload_text.encode("utf-8")).hexdigest(),
            "schema_sha256": canonical_hash(task.output_schema), "task_fingerprint": task.fingerprint,
            "max_attempts": 1, "automatic_retry": False, "output_token_target": 4000,
            "output_tokens_hard_capped": False}, preview.payload_text


def _receipts(root: Path, *, create: bool = False) -> ScopeReceipts:
    """Independent global run ledger; deleting release-local state cannot refund."""
    from . import owner_anchor as anchor
    identity = owner.load_owner_identity(owner._safe(root, root / "data/owner-acceptance/identity.json"))
    # Require a previously reused identity, never adopt a legacy unregistered root.
    document = owner._read(root / "data/owner-acceptance/identity.json")
    if document.get("format") != anchor.REFERENCE:
        raise EngineeringScopeError("ENGINEERING_REUSED_OWNER_REQUIRED")
    anchor.load_reference(root, document)
    record = owner._read(anchor.registry_path())
    registry = anchor._runs(record, identity)
    approved = Path(owner.APPROVED_ROOT)
    directory = owner._safe(approved, approved / "data/owner-runs" / root.name / "engineering-receipts")
    marker = {"kind": POLICY, "release_id": root.name}
    with anchor.configuration_lock():
        existing = registry.read("engineering-ledger:" + root.name)
        if existing is None:
            if not create:
                raise EngineeringScopeError("ENGINEERING_ACTIVATION_REQUIRED")
            if directory.exists():
                raise EngineeringScopeError("ENGINEERING_LEDGER_CONFLICT")
            if not registry.put("engineering-ledger:" + root.name, marker):
                raise EngineeringScopeError("ENGINEERING_LEDGER_CONFLICT")
            directory.mkdir()
        elif existing != marker or not directory.is_dir():
            raise EngineeringScopeError("ENGINEERING_LEDGER_MISSING_OR_CHANGED")
    return ScopeReceipts(approved, directory, {"policy": POLICY, "release": root.name}, identity.token)


def activate(root: Path, spec: dict[str, Any], *, codex_exe: Path, codex_home: Path,
             approved_scope_hash: str) -> str:
    """Persist one reviewed scope; never replenish an existing activation."""
    scope, _ = prepare(root, spec, codex_exe=codex_exe, codex_home=codex_home)
    if not re.fullmatch(r"[0-9a-f]{64}", approved_scope_hash) or canonical_hash(scope) != approved_scope_hash:
        raise EngineeringScopeError("ENGINEERING_EXACT_SCOPE_APPROVAL_REQUIRED")
    receipts = _receipts(root, create=True)
    existing = receipts.read("activation")
    if existing is not None and existing != scope:
        raise EngineeringScopeError("ENGINEERING_ACTIVATION_CONFLICT")
    receipts.put("activation", scope)
    config = _base(root) | {"owner_engineering_scope": POLICY}
    path = owner._safe(root, root / "data/owner-acceptance" / DESKTOP_NAME)
    if path.exists():
        if _json(path, 32768) != config:
            raise EngineeringScopeError("ENGINEERING_DESKTOP_CHANGED")
    else:
        with path.open("xb") as stream:
            stream.write(owner._canonical(config) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    return "already_activated_no_refund" if existing is not None else "activated_not_started"


def _activation(root: Path) -> tuple[dict[str, Any], ScopeReceipts]:
    receipts = _receipts(root)
    scope = receipts.read("activation")
    if scope is None:
        raise EngineeringScopeError("ENGINEERING_ACTIVATION_REQUIRED")
    model = scope["model"]
    current, _ = prepare(root, scope["selection"], codex_exe=Path(model["executable"][0]),
                         codex_home=Path(model["codex_home"]))
    if scope != current:
        raise EngineeringScopeError("ENGINEERING_ACTIVATION_STALE")
    return scope, receipts


def desktop_config_path(root: Path) -> Path:
    """Validate exact activation before the launcher selects this distinct mode."""
    _activation(root)
    path = owner._safe(root, root / "data/owner-acceptance" / DESKTOP_NAME)
    if _json(path, 32768) != _base(root) | {"owner_engineering_scope": POLICY}:
        raise EngineeringScopeError("ENGINEERING_DESKTOP_CHANGED")
    return path


def desktop_runner(path: Path, state_directory: Path):
    """Wire only the validated explicit Owner engineering mode, not material scope."""
    from .model import build_runner_from_config
    root = owner._acceptance_root()
    if (path.resolve() != desktop_config_path(root)
            or state_directory.resolve() != owner._safe(root, root / "data/owner-acceptance/desktop/.ahadiff")):
        raise EngineeringScopeError("ENGINEERING_DESKTOP_BINDING_MISMATCH")
    scope, receipts = _activation(root)
    def revalidate():
        model = scope["model"]
        current, _ = prepare(root, scope["selection"], codex_exe=Path(model["executable"][0]),
                             codex_home=Path(model["codex_home"]), execution=True)
        return current
    return EngineeringScopeRunner(build_runner_from_config(scope["model"]), receipts, scope, revalidate), bridge_configuration(scope["selection"]["classification"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "activate"))
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--codex-exe", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--approved-scope-hash")
    args = parser.parse_args()
    try:
        root = owner._acceptance_root()
        spec = _json(owner._safe(root, args.spec), 32768)
        if args.action == "activate":
            if not args.approved_scope_hash:
                raise EngineeringScopeError("ENGINEERING_EXACT_SCOPE_APPROVAL_REQUIRED")
            status = activate(root, spec, codex_exe=args.codex_exe, codex_home=args.codex_home,
                              approved_scope_hash=args.approved_scope_hash)
            print(json.dumps({"status": status, "model_called": False}))
        else:
            if args.approved_scope_hash:
                raise EngineeringScopeError("ENGINEERING_PREVIEW_CANNOT_ACTIVATE")
            scope, payload = prepare(root, spec, codex_exe=args.codex_exe, codex_home=args.codex_home)
            report = owner._safe(root, root / "reports/engineering-preview.json")
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({"scope": scope, "scope_hash": canonical_hash(scope),
                "full_redacted_payload": payload, "model_called": False,
                "warning": "Output target is not a provider hard consumption cap. Review all payload, including topics and answers."},
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": "preview_not_activated", "scope_hash": canonical_hash(scope),
                              "report": str(report), "model_called": False}))
    except Exception:
        print(json.dumps({"status": "blocked", "error_code": "ENGINEERING_OPERATION_FAILED_CLOSED"}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
