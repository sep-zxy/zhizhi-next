"""Explicit C20 build, never deployment. Run only in the approved hosted workflow.

The environment guard prevents accidental local builds; it is not an attestation
or OS sandbox. The executor must supply locked offline dependencies and isolation
(no private vault mounts, credentials or network). Keep trusted inputs stable.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
from typing import Any

from ..publish.artifacts import validate_public_artifacts
from ..publish.quartz import quartz_generated_files
from .checks import _hash, _json, _repository, _require, _safe, _tree
from .publication import _destination
from .checks import verify_publication_manifest


_SITE = Path(__file__).resolve().parents[5] / "apps/public-wiki/site"


def _hosted_runner() -> None:
    # Existing project build policy, not a new authorization or isolation proof.
    _require(os.environ.get("GITHUB_ACTIONS") == "true"
             and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
             "GITHUB_HOSTED_BUILD_REQUIRED")


def _fonts(site: Path) -> None:
    script = _safe(site.parent / "scripts/offline-fonts.py")
    prepare = runpy.run_path(str(script))["prepare"]
    prepare(site, _safe(site.parent / "docs/build-assets/fonts-manifest.json"))


def _generated(root: Path) -> tuple[str, ...]:
    _tree(root)
    # The legacy helper excludes by basename; only the root manifest is metadata.
    _require(all(p == root / "publish-manifest.json"
                 for p in root.rglob("publish-manifest.json")), "NESTED_MANIFEST_REJECTED")
    return quartz_generated_files(root)


def _builder_cache(site: Path, protected: tuple[Path, ...]) -> None:
    """Quartz may write these caches; reject links and protected-input overlap."""
    _safe(site / "quartz", directory=True)
    for cache in (site / "quartz/.quartz-cache", site / ".quartz-cache"):
        for source in protected:
            _require(not cache.is_relative_to(source) and not source.is_relative_to(cache),
                     "BUILDER_CACHE_OVERLAPS_SOURCE")
        if cache.exists() or cache.is_symlink():
            _tree(_safe(cache, directory=True))
            for file in cache.rglob("*"):
                if file.is_file():
                    _require(file.stat().st_nlink == 1, "CACHE_HARDLINK_REJECTED")


def publish_build(*, manifest_path: str | Path, staging: str | Path,
                  output: str | Path, template_assets: str | Path, public_base_url: str,
                  repository: str | Path, database: str | Path, owner_id: str,
                  policy_path: str | Path, domain_policy_version: str = "1") -> dict[str, Any]:
    """Build one exact approved manifest and return a sanitized verification receipt.

    No shell, dependency install, sync, model, deploy, restore or existing output
    replacement. Failed output stays inside a disposable private temporary tree.
    stdout/stderr from the builder are never included in the operator receipt.
    """
    _hosted_runner()
    repo, db, policy_file = _repository(Path(repository)), _safe(database), _safe(policy_path)
    root, site = _safe(staging, directory=True), _safe(_SITE, directory=True)
    approved_path, assets_path = _safe(manifest_path), _safe(template_assets)
    approved, policy, template = _json(approved_path), _json(policy_file), _json(assets_path)
    _require(_json(root / "publish-manifest.json") == approved, "MANIFEST_MISMATCH")
    fingerprints = {p: _hash(p) for p in (approved_path, policy_file, assets_path)}
    generated = _generated(root)
    destination = _destination(output, repository=repo, database=db, policy=policy_file)
    for protected in (root, site, approved_path, assets_path):
        _require(not destination.is_relative_to(protected)
                 and not protected.is_relative_to(destination), "DESTINATION_OVERLAPS_SOURCE")
    # Existing Quartz validates the public HTTPS origin before rendering.
    _require(isinstance(public_base_url, str) and bool(public_base_url), "PUBLIC_ORIGIN_REQUIRED")
    node = shutil.which("node")
    _require(node is not None, "NODE_MISSING")
    _safe(site / "quartz/bootstrap-cli.mjs")
    _builder_cache(site, (repo, db.parent, root, approved_path, assets_path, policy_file))
    _fonts(site)
    common = dict(repository=repo, database=db, owner_id=owner_id, policy_path=policy_file,
                  domain_policy_version=domain_policy_version)
    verify_publication_manifest(approved_path, **common)
    with tempfile.TemporaryDirectory(prefix="knowledge-build-", dir=destination.parent) as temporary:
        workspace = Path(temporary)
        sanitized, built = workspace / "sanitized", workspace / "output"
        shutil.copytree(root, sanitized)
        _require(_json(sanitized / "publish-manifest.json") == approved, "MANIFEST_MISMATCH")
        _require(_generated(sanitized) == generated, "STAGING_CHANGED")
        env = {name: os.environ[name] for name in ("PATH", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
               if name in os.environ}
        env.update(TZ="UTC", QUARTZ_OFFLINE_PLUGINS="1", QUARTZ_PUBLIC_BASE_URL=public_base_url)
        result = subprocess.run(
            [node, "quartz/bootstrap-cli.mjs", "build", "-d", str(sanitized), "-o", str(built),
             "--concurrency=1"], cwd=site, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, check=False,
        )
        _require(result.returncode == 0, "QUARTZ_BUILD_FAILED")
        _tree(_safe(built, directory=True))
        _require(all(_hash(p) == digest for p, digest in fingerprints.items()), "BUILD_INPUT_CHANGED")
        _require(_json(sanitized / "publish-manifest.json") == approved, "MANIFEST_MISMATCH")
        _require(_generated(sanitized) == generated, "STAGING_CHANGED")
        verify_publication_manifest(approved_path, **common)
        report = validate_public_artifacts(
            built, approved, policy=policy,
            forbidden_fingerprints=policy.get("forbidden_fingerprints", []),
            trusted_template_assets=template["assets"], generated_files=generated,
        )
        # Reserve exclusively; never overwrite a website or a concurrent result.
        _destination(destination, repository=repo, database=db, policy=policy_file)
        destination.mkdir()
        identity = destination.stat()
        try:
            shutil.copytree(built, destination, dirs_exist_ok=True)
            _tree(destination)
            copied = validate_public_artifacts(
                destination, approved, policy=policy,
                forbidden_fingerprints=policy.get("forbidden_fingerprints", []),
                trusted_template_assets=template["assets"], generated_files=generated,
            )
            _require(copied == report, "OUTPUT_CHANGED")
            _require(all(_hash(p) == digest for p, digest in fingerprints.items()), "BUILD_INPUT_CHANGED")
            verify_publication_manifest(approved_path, **common)
        except Exception:
            if not destination.is_symlink() and destination.exists():
                current = destination.stat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    shutil.rmtree(destination)
            raise
    return {**report, "status": "verified_site_build", "effect": "new_output_and_builder_cache",
            "builder_cache_may_change": True,
            "build_hash": approved["build_hash"],
            "approved_manifest_sha256": fingerprints[approved_path],
            "template_assets_sha256": fingerprints[assets_path], "site_build_executed": True,
            "deploy_executed": False, "isolation_attested": False,
            "revalidation_required_before_deploy": True}
