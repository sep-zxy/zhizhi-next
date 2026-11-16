"""Fail-closed inspection of explicitly selected local operator artifacts.

Callers must keep inputs stable during inspection. This is not an OS sandbox,
restore guarantee, signature verification or authorization to deploy anything.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from typing import Any

from ..portable import require_portable_path
from ..git_process import run_git
from ..publish.projection import _path
from ..publish.revalidate import bind_domain_publication
from ..writer import GitWriter, ReadOnlyGitWriter, _git_command


class PreviewError(ValueError):
    """A deliberately non-sensitive operator validation failure."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PreviewError(code)


def _safe(path: str | Path, *, directory: bool = False) -> Path:
    candidate = Path(os.path.abspath(require_portable_path(path, state=True)))
    for part in [candidate, *candidate.parents]:
        _require(not part.is_symlink(), "LINK_REJECTED")
        if hasattr(part, "is_junction"):
            _require(not part.is_junction(), "LINK_REJECTED")
    _require(candidate.is_dir() if directory else candidate.is_file(), "COMPONENT_MISSING")
    _require(candidate.resolve() == candidate, "REDIRECT_REJECTED")
    return candidate


def _tree(root: Path) -> None:
    # Never follow links, including links to another part of the snapshot.
    for base, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(base) / name
            _safe(path, directory=name in directories)
            _require(stat.S_ISREG(path.stat().st_mode) or path.is_dir(), "SPECIAL_FILE_REJECTED")


def _json(path: Path) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    result = json.loads(_safe(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    _require(isinstance(result, dict), "INVALID_MANIFEST")
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with _safe(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: Any) -> str:
    _require(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None,
             "INVALID_DIGEST")
    return value


def _relative(value: Any) -> str:
    _require(isinstance(value, str) and bool(value), "INVALID_PATH")
    normalized = _path(value)
    _require(normalized == value and normalized != ".", "INVALID_PATH")
    return normalized


def _revision(value: Any) -> str:
    _require(isinstance(value, str) and re.fullmatch("[0-9a-f]{40}|[0-9a-f]{64}", value)
             is not None, "INVALID_REVISION")
    return value


def _repository(path: Path) -> Path:
    root = _safe(path, directory=True)
    _tree(root)
    _safe(root / "HEAD")
    _safe(root / "objects", directory=True)
    config = _safe(root / "config").read_text(encoding="utf-8")
    _require(not re.search(r"(?im)^\s*\[\s*(include|includeif|extensions)\b", config),
             "EXTERNAL_GIT_CONFIG_REJECTED")
    # These features can resolve objects or refs outside the selected backup or
    # replace historical content. Portable snapshots must be self-contained.
    for relative in ("commondir", "gitdir", "shallow", "info/grafts", "objects/info/alternates",
                     "objects/info/http-alternates", "refs/replace", "config.worktree"):
        _require(not (root / relative).exists(), "EXTERNAL_OR_REPLACED_GIT_REJECTED")
    packed = root / "packed-refs"
    if packed.exists():
        _require(b"refs/replace/" not in packed.read_bytes(), "EXTERNAL_OR_REPLACED_GIT_REJECTED")
    return root


@contextmanager
def _read_database(database: Path, expected_hash: str):
    # SQLite mode=ro may create WAL/SHM files beside a WAL-mode database even
    # when no query mutates it. Inspect only a disposable, hash-matched copy.
    # This is not a restored application: no objects, Git, migrations or writes.
    parent = Path(tempfile.gettempdir()).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="knowledge-backup-inspect-", dir=parent) as temporary:
        copy = Path(temporary) / "platform.db"
        copy.write_bytes(database.read_bytes())
        _require(_hash(copy) == expected_hash, "SNAPSHOT_CHANGED_DURING_INSPECTION")
        connection = sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            yield connection
        finally:
            connection.close()


def inspect_backup(snapshot: str | Path) -> dict[str, Any]:
    """Validate the existing domain.backup v1 format without creating any state."""
    root = _safe(snapshot, directory=True)
    _tree(root)
    manifest = _json(root / "manifest.json")
    _require(manifest.get("schema_version") == 1 and manifest.get("complete") is True,
             "INCOMPLETE_OR_UNSUPPORTED_SNAPSHOT")
    database = _safe(root / "platform.db")
    # backup() creates a standalone SQLite DB. Sidecars indicate an active or
    # incomplete copy. Do not ignore a WAL via immutable=1 or checkpoint it.
    for suffix in ("-wal", "-shm", "-journal"):
        _require(not Path(str(database) + suffix).exists(), "DATABASE_SIDECAR_REJECTED")
    database_hash = _digest(manifest.get("database_sha256"))
    _require(_hash(database) == database_hash, "DATABASE_HASH_MISMATCH")
    repository = _repository(root / "vault.git")
    writer = ReadOnlyGitWriter(repository)
    revision = manifest.get("git_revision")
    if revision is not None:
        _revision(revision)
    _require(writer.head() == revision, "GIT_HEAD_MISMATCH")
    if revision:
        _require(writer._git("cat-file", "-t", revision).strip() == b"commit", "INVALID_COMMIT")
    objects = manifest.get("objects")
    _require(isinstance(objects, list), "INVALID_OBJECT_MANIFEST")
    expected = {}
    for item in objects:
        _require(isinstance(item, dict), "INVALID_OBJECT_MANIFEST")
        relative = _relative(item.get("path"))
        digest = _digest(item.get("sha256"))
        size = item.get("bytes")
        _require(type(size) is int and size >= 0, "INVALID_OBJECT_SIZE")
        _require(relative not in expected or expected[relative] == digest, "CONFLICTING_OBJECT")
        path = _safe(root / "objects" / relative)
        _require(path.stat().st_size == size and _hash(path) == digest, "OBJECT_HASH_MISMATCH")
        expected[relative] = digest
    actual = set()
    if (root / "objects").exists():
        actual = {str(p.relative_to(root / "objects").as_posix())
                  for p in (root / "objects").rglob("*") if p.is_file()}
    _require(actual == set(expected), "OBJECT_SET_MISMATCH")
    references = {}
    # mode=ro + query_only, never DomainService() initialization or recovery.
    with _read_database(database, database_hash) as connection:
        _require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                 "DATABASE_INTEGRITY_FAILURE")
        for (data,) in connection.execute("SELECT data FROM kp_records WHERE kind='source'"):
            source = json.loads(data)
            relative = _relative(source.get("object_path"))
            digest = _digest(source.get("content_hash"))
            _require(relative not in references or references[relative] == digest,
                     "SOURCE_REFERENCE_CONFLICT")
            references[relative] = digest
        _require(references == expected, "SOURCE_REFERENCE_MISMATCH")
        revisions = 0
        latest = {}
        for entry_id, number, owner_id, commit, path, digest, metadata_json in connection.execute(
            "SELECT entry_id,revision,owner_id,commit_sha,path,content_hash,metadata "
            "FROM kp_entry_revisions ORDER BY revision"
        ):
            metadata = json.loads(metadata_json)
            _require(isinstance(metadata, dict), "HISTORY_METADATA_MISMATCH")
            for key, value in (("entry_id", entry_id), ("revision", number),
                               ("owner_id", owner_id), ("commit_sha", commit),
                               ("path", path), ("content_hash", digest)):
                _require(metadata.get(key) == value, "HISTORY_METADATA_MISMATCH")
            _revision(commit)
            _relative(path)
            _digest(digest)
            _require(revision is not None, "ENTRY_WITHOUT_GIT_HEAD")
            ancestry = run_git(
                _git_command("--git-dir", str(repository), "merge-base", "--is-ancestor",
                             commit, revision),
                cwd=repository, env=writer._environment(), timeout=30,
            )
            _require(ancestry.returncode == 0, "ENTRY_NOT_IN_SNAPSHOT_HISTORY")
            tree = writer._git("ls-tree", commit, "--", path)
            _require(tree.startswith(b"100644 blob "), "ENTRY_BLOB_MISSING")
            entry = writer.read_entry(commit, path)
            _require(hashlib.sha256(entry["body"].encode("utf-8")).hexdigest() == digest,
                     "ENTRY_HASH_MISMATCH")
            # The application reads the metadata JSON, not just the SQL columns.
            # Reconstruct its canonical document and compare the complete blob,
            # binding title, classification, owner namespace and all metadata.
            _require(path == GitWriter.path_for(owner_id, entry_id), "ENTRY_NAMESPACE_MISMATCH")
            _require(writer.file_hash(commit, path) == hashlib.sha256(
                GitWriter.document({**metadata, "body": entry["body"]})
            ).hexdigest(), "HISTORY_DOCUMENT_MISMATCH")
            latest[(owner_id, entry_id)] = metadata
            revisions += 1
        catalog_keys = set()
        for entry_id, owner_id, number, data in connection.execute(
            "SELECT id,owner_id,revision,data FROM kp_records WHERE kind='entry'"
        ):
            catalog = json.loads(data)
            key = (owner_id, entry_id)
            _require(key in latest and catalog == latest[key] and catalog.get("revision") == number,
                     "CURRENT_CATALOG_MISMATCH")
            catalog_keys.add(key)
        _require(catalog_keys == set(latest), "CURRENT_CATALOG_MISMATCH")
        _require(connection.execute(
            "SELECT COUNT(*) FROM kp_writer_journal WHERE state != 'committed'"
        ).fetchone()[0] == 0, "PENDING_WRITER_JOURNAL")
    _require(_hash(database) == database_hash and writer.head() == revision,
             "SNAPSHOT_CHANGED_DURING_INSPECTION")
    return {"status": "verified_snapshot", "effect": "read_only", "schema_version": 1,
            "database_sha256": database_hash, "git_revision": revision,
            "object_count": len(expected), "entry_revision_count": revisions,
            "restore_executed": False, "recovery_proven": False}


def restore_plan(snapshot: str | Path, *, dry_run: bool) -> dict[str, Any]:
    _require(dry_run is True, "DRY_RUN_REQUIRED")
    report = inspect_backup(snapshot)
    return {**report, "status": "restore_plan_only", "dry_run": True,
            "steps": ["select_new_isolated_destination", "copy_verified_snapshot_without_overwrite",
                      "verify_isolated_copy_again", "rebuild_disposable_indexes",
                      "run_application_acceptance", "request_explicit_cutover_approval"],
            "limitations": ["no_files_copied", "no_database_recovery", "no_live_cutover",
                            "checksums_do_not_prove_authenticity"]}


def verify_publication_manifest(manifest_path: str | Path, *, repository: str | Path,
                                database: str | Path, owner_id: str,
                                policy_path: str | Path,
                                domain_policy_version: str = "1") -> dict[str, Any]:
    """Rebuild transient projection with the existing sanitizer and trusted resolver.

    No staging destination, build, deployment or credentials are accepted here.
    Temporary files used by the existing revalidator are removed before return.
    """
    manifest = _json(Path(manifest_path))
    from .projection import revalidate_domain_projection
    policy = _json(Path(policy_path))
    repo = _repository(Path(repository))
    db = _safe(database)
    domain, resolver = bind_domain_publication(db, owner_id, repo,
                                              domain_policy_version=domain_policy_version)
    try:
        revalidate_domain_projection(manifest, repository=repo, owner_id=owner_id, policy=policy,
                                     publication_resolver=resolver)
    finally:
        domain.close()
    return {"status": "manifest_eligibility_verified", "effect": "temporary_projection_only",
            "revision": _revision(manifest["revision"]),
            "build_hash": _digest(manifest["build_hash"]),
            "deploy_executed": False, "site_build_executed": False}
