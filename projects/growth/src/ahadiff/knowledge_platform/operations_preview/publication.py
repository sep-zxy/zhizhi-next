"""Explicit local publication checks and new-directory sanitized previews only."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from ..auth import Actor
from ..contracts import identifier
from ..errors import DomainError
from ..portable import require_portable_path
from ..publish.quartz import quartz_generated_files
from ..publish.revalidate import bind_domain_publication
from .checks import _json, _repository, _require, _revision, _safe, verify_publication_manifest
from .projection import project_domain_revision, revalidate_domain_projection


def eligibility(entry_id: str, *, repository: str | Path, database: str | Path,
                owner_id: str, domain_policy_version: str = "1") -> dict[str, Any]:
    """Read only the current owned entry; return no title, body, citation or path."""
    identifier(entry_id, "entry_id")
    repo = _repository(Path(repository))
    db = _safe(database)
    domain, resolver = bind_domain_publication(db, owner_id, repo,
                                              domain_policy_version=domain_policy_version)
    try:
        actor = Actor(owner_id, "local-operator-preview", "user", frozenset({"knowledge:read"}))
        try:
            entry = domain.get_entry(entry_id, actor=actor)
            resolver(entry)
        except DomainError as exc:
            # Codes are an allowlist, never the free-text exception reason.
            reason = exc.code if exc.code in {"NOT_FOUND", "FORBIDDEN", "CONTENT_CORRUPT",
                                              "PUBLICATION_CERTIFICATE_INVALID"} else "NOT_ELIGIBLE"
            return {"eligible": False, "reason_code": reason, "effect": "read_only"}
        return {"eligible": True, "reason_code": "CURRENT_CERTIFIED_ARTIFACT",
                "revision": entry["revision"], "effect": "read_only"}
    finally:
        domain.close()


def _destination(path: str | Path, *, repository: Path, database: Path, policy: Path) -> Path:
    destination = Path(os.path.abspath(require_portable_path(path, state=True)))
    _safe(destination.parent, directory=True)
    _require(not destination.exists() and not destination.is_symlink(), "DESTINATION_EXISTS")
    for protected in (repository, database.parent, policy):
        _require(not destination.is_relative_to(protected)
                 and not protected.is_relative_to(destination), "DESTINATION_OVERLAPS_SOURCE")
    return destination


def publish_preview(*, repository: str | Path, database: str | Path, owner_id: str,
                    revision: str, staging: str | Path, policy_path: str | Path,
                    policy_version: str = "1", template_version: str = "1",
                    domain_policy_version: str = "1") -> dict[str, Any]:
    """Project exact Git into a brand-new isolated directory, never deploy or build."""
    _revision(revision)
    repo = _repository(Path(repository))
    db = _safe(database)
    policy_file = _safe(policy_path)
    policy = _json(policy_file)
    destination = _destination(staging, repository=repo, database=db, policy=policy_file)
    domain, resolver = bind_domain_publication(db, owner_id, repo,
                                              domain_policy_version=domain_policy_version)
    try:
        with tempfile.TemporaryDirectory(prefix="knowledge-preview-", dir=destination.parent) as temp:
            temporary = Path(temp) / "sanitized"
            result = project_domain_revision(repo, revision, temporary, policy=policy, owner_id=owner_id,
                                          policy_version=policy_version,
                                          template_version=template_version,
                                          publication_resolver=resolver)
            manifest = result["public_manifest"]
            # Current ledger may revoke an old Git revision; recheck after generation.
            revalidate_domain_projection(manifest, repository=repo, owner_id=owner_id, policy=policy,
                                         publication_resolver=resolver)
            quartz_generated_files(temporary)  # Existing content-set / hash verification.
            _destination(destination, repository=repo, database=db, policy=policy_file)
            # Exclusive reservation identifies exactly the directory this call owns.
            # Never clean an existing or concurrently replaced destination.
            destination.mkdir()
            identity = destination.stat()
            try:
                shutil.copytree(temporary, destination, symlinks=False, dirs_exist_ok=True)
            except Exception:
                if not destination.is_symlink() and destination.exists():
                    current = destination.stat()
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        shutil.rmtree(destination)
                raise
    finally:
        domain.close()
    return {"status": "sanitized_preview_created", "effect": "new_local_staging_only",
            "revision": manifest["revision"], "build_hash": manifest["build_hash"],
            "counts": manifest["counts"], "excluded_count": len(result["internal_manifest"]["excluded"]),
            "site_build_executed": False, "deploy_executed": False,
            "revalidation_required_before_build_or_deploy": True}


def publish_build_plan(*, staging: str | Path, repository: str | Path,
                       database: str | Path, owner_id: str, policy_path: str | Path,
                       domain_policy_version: str = "1") -> dict[str, Any]:
    """Verify existing staging then describe the existing Quartz boundary, not run it."""
    root = _safe(staging, directory=True)
    from .checks import _tree
    _tree(root)
    report = verify_publication_manifest(root / "publish-manifest.json", repository=repository,
                                        database=database, owner_id=owner_id, policy_path=policy_path,
                                        domain_policy_version=domain_policy_version)
    generated = quartz_generated_files(root)
    return {**report, "status": "build_plan_only", "effect": "read_only",
            "expected_generated_file_count": len(generated),
            "engine": "projects/selfweb/site/quartz/bootstrap-cli.mjs",
            "steps": ["revalidate_current_eligibility", "use_locked_quartz_on_github_runner",
                      "build_only_sanitized_staging_into_new_output",
                      "verify_output_with_publish.verify_and_trusted_template_assets",
                      "separate_deployment_authorization"],
            "constraints": ["no_default_npm_build_sync", "no_private_vault_mount_in_builder",
                            "no_model_calls", "no_deployment", "no_local_packaging"]}
