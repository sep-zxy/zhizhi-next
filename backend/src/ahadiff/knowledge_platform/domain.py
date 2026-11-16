"""Canonical synchronous DomainService shared by every knowledge-platform adapter."""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ahadiff.core.sqlite_util import managed_sqlite_connection

if TYPE_CHECKING:
    from collections.abc import Callable

from .auth import Actor, authenticated, enforce_read_classification
from .changes import ChangeMethods
from .contracts import (
    canonical,
    fingerprint,
    new_id,
    now,
    required_text,
    text_hash,
    validate_payload,
)
from .errors import DomainError, invalid
from .portable import require_portable_path
from .publication import PublicationMethods
from .reviews import ReviewMethods
from .sources import SourceMethods
from .store import DomainStore
from .tasks import TaskMethods
from .writer import GitWriter


class DomainService(TaskMethods, SourceMethods, ChangeMethods, ReviewMethods, PublicationMethods):
    """Single domain ledger and CAS writer, with JSON-only public method boundaries.

    This service owns the connection. Adapters/workers must call these methods rather
    than opening SQLite themselves. All request authority is carried by Actor.
    """

    def __init__(
        self,
        database_path: str | Path,
        vault_path: str | Path | None = None,
        *,
        object_path: str | Path | None = None,
        cache_path: str | Path | None = None,
        policy_version: str = "1",
    ) -> None:
        database_path = require_portable_path(database_path, state=True)
        vault_path = require_portable_path(
            vault_path or database_path.parent / "vault.git", state=True
        )
        object_path = require_portable_path(
            object_path or database_path.parent / "objects", state=True
        )
        cache_path = require_portable_path(
            cache_path or database_path.parent / "cache" / "intake", state=True
        )
        self.store = DomainStore(database_path)
        self.writer = GitWriter(vault_path or database_path.parent / "vault.git")
        self.object_path = Path(object_path or database_path.parent / "objects").resolve()
        self.cache_path = Path(cache_path or database_path.parent / "cache" / "intake").resolve()
        self.object_path.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.policy_version = policy_version
        self.max_object_bytes = 200 * 1024 * 1024
        self._apply_lock = threading.RLock()
        self.fault_injector: Callable[[str, dict[str, Any]], None] | None = None

    def close(self) -> None:
        """Close persistent resources after all workers have stopped."""
        self.store.close()

    @classmethod
    def open_readonly(
        cls,
        database_path: str | Path,
        vault_path: str | Path | None = None,
        *,
        object_path: str | Path | None = None,
        policy_version: str = "1",
    ) -> DomainService:
        """Open only existing publication evidence; never initialize or recover state."""
        from .writer import ReadOnlyGitWriter

        database_path = require_portable_path(database_path, state=True)
        vault_path = require_portable_path(
            vault_path or database_path.parent / "vault.git", state=True
        )
        object_path = require_portable_path(
            object_path or database_path.parent / "objects", state=True
        )
        service = object.__new__(cls)
        service.store = DomainStore.open_readonly(database_path)
        try:
            service.writer = ReadOnlyGitWriter(vault_path or database_path.parent / "vault.git")
        except Exception:
            service.store.close()
            raise
        service.object_path = Path(object_path or database_path.parent / "objects").resolve()
        service.policy_version = policy_version
        return service

    @staticmethod
    def _validate_key(key: str) -> None:
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise invalid("A nonempty Idempotency-Key of at most 256 characters is required")

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise invalid("Invalid pagination")

    @staticmethod
    def _check_revision(record: dict[str, Any], patch: dict[str, Any]) -> None:
        if "expected_revision" in patch and patch["expected_revision"] != record["revision"]:
            raise DomainError("BASE_STALE", "Record revision changed", status_code=409)

    def _read(self, kind: str, record_id: str, actor: Actor) -> dict[str, Any]:
        authenticated(actor, "knowledge:read")
        with self.store.reading() as connection:
            return self._protect_read(
                connection, self.store.get(connection, kind, record_id, actor.owner_id), actor
            )

    def _protect_read(
        self, connection: sqlite3.Connection, record: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        """Apply the same classification boundary to HTTP, workers and direct adapters."""
        record = self.store.label_record(connection, record)
        enforce_read_classification(actor, record)
        return record

    def _visible_records(
        self, connection: sqlite3.Connection, records: list[dict[str, Any]], actor: Actor
    ) -> list[dict[str, Any]]:
        visible = []
        for record in records:
            try:
                visible.append(self._protect_read(connection, record, actor))
            except DomainError as exc:
                if exc.code != "CLASSIFICATION_FORBIDDEN":
                    raise
        return visible

    def _list_owned_records(
        self,
        kind: str,
        *,
        actor: Actor,
        state: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """Page visible records only; hidden records never affect counts or offsets."""
        authenticated(actor, "knowledge:read")
        self._validate_page(limit, offset)
        if kind not in {"task", "intake"} or limit > 100 or offset > 100_000:
            raise invalid("Invalid collection pagination")
        visible: list[dict[str, Any]] = []
        skipped = 0
        with self.store.reading() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind=? AND owner_id=? ORDER BY rowid DESC",
                (kind, actor.owner_id),
            )
            for row in rows:
                record = json.loads(row["data"])
                if state is not None and record.get("state") != state:
                    continue
                try:
                    record = self._protect_read(connection, record, actor)
                except DomainError as exc:
                    if exc.code != "CLASSIFICATION_FORBIDDEN":
                        raise
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                visible.append(record)
                if len(visible) > limit:
                    break
        return {"items": visible[:limit], "has_more": len(visible) > limit}

    @staticmethod
    def _receipt(
        connection: sqlite3.Connection, actor: Actor, operation: str, key: str, input_hash: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT input_hash,response FROM kp_receipts "
            "WHERE owner_id=? AND operation=? AND key=?",
            (actor.owner_id, operation, key),
        ).fetchone()
        if row is None:
            return None
        if row["input_hash"] != input_hash:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT",
                "Idempotency key was already used with different input",
                status_code=409,
            )
        return json.loads(row["response"])

    @staticmethod
    def _save_receipt(
        connection: sqlite3.Connection,
        actor: Actor,
        operation: str,
        key: str,
        input_hash: str,
        response: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO kp_receipts"
            "(owner_id,operation,key,input_hash,response,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (actor.owner_id, operation, key, input_hash, canonical(response), now()),
        )

    def _mutate(
        self,
        actor: Actor,
        operation: str,
        key: str,
        payload: dict[str, Any],
        action: Callable[[sqlite3.Connection], dict[str, Any]],
    ) -> dict[str, Any]:
        self._validate_key(key)
        digest = fingerprint(payload)
        with self.store.transaction() as connection:
            cached = self._receipt(connection, actor, operation, key, digest)
            if cached is not None:
                if cached.get("owner_id"):
                    return self._protect_read(connection, cached, actor)
                enforce_read_classification(actor, cached, default="personal")
                return cached
            result = action(connection)
            if result.get("owner_id"):
                result = self._protect_read(connection, result, actor)
            else:
                enforce_read_classification(actor, result, default="personal")
            self._save_receipt(connection, actor, operation, key, digest, result)
            return result

    @staticmethod
    def _unique(
        connection: sqlite3.Connection, actor: Actor, namespace: str, key: str, input_hash: str
    ) -> str | None:
        row = connection.execute(
            "SELECT input_hash,record_id FROM kp_unique_keys "
            "WHERE owner_id=? AND namespace=? AND key=?",
            (actor.owner_id, namespace, key),
        ).fetchone()
        if row is None:
            return None
        if row["input_hash"] != input_hash:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT",
                "Semantic identity already refers to different content",
                status_code=409,
            )
        return row["record_id"]

    @staticmethod
    def _save_unique(
        connection: sqlite3.Connection,
        actor: Actor,
        namespace: str,
        key: str,
        input_hash: str,
        record_id: str,
    ) -> None:
        connection.execute(
            "INSERT INTO kp_unique_keys(owner_id,namespace,key,input_hash,record_id) "
            "VALUES(?,?,?,?,?)",
            (actor.owner_id, namespace, key, input_hash, record_id),
        )

    def _entry(
        self,
        connection: sqlite3.Connection,
        entry_id: str,
        owner_id: str,
        revision: int | None = None,
    ) -> dict[str, Any]:
        if revision is None:
            catalog = self.store.get(connection, "entry", entry_id, owner_id)
        else:
            row = connection.execute(
                "SELECT metadata FROM kp_entry_revisions "
                "WHERE entry_id=? AND revision=? AND owner_id=?",
                (entry_id, revision, owner_id),
            ).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", "Entry revision was not found", status_code=404)
            catalog = json.loads(row["metadata"])
        entry = self.writer.read_entry(catalog["commit_sha"], catalog["path"])
        if (
            text_hash(entry["body"]) != catalog["content_hash"]
            or entry["revision"] != catalog["revision"]
        ):
            raise DomainError(
                "CONTENT_CORRUPT", "Formal Git revision does not match the catalog", status_code=409
            )
        return {**entry, "owner_id": owner_id}

    @staticmethod
    def _history_missing() -> DomainError:
        return DomainError("NOT_FOUND", "Entry revision was not found", status_code=404)

    def _history_entry(
        self, connection: sqlite3.Connection, entry_id: str, actor: Actor,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Check catalog identity before Git, then preserve subclass read authorization."""
        if revision is None:
            catalog = self.store.get(connection, "entry", entry_id, actor.owner_id)
        else:
            row = connection.execute(
                "SELECT metadata FROM kp_entry_revisions "
                "WHERE entry_id=? AND revision=? AND owner_id=?",
                (entry_id, revision, actor.owner_id),
            ).fetchone()
            if row is None:
                raise self._history_missing()
            catalog = json.loads(row["metadata"])
        try:
            self._protect_read(connection, dict(catalog), actor)
            if catalog.get("visibility") != "public":
                enforce_read_classification(actor, {"data_classification": "personal"})
        except DomainError as exc:
            if exc.code == "CLASSIFICATION_FORBIDDEN":
                raise self._history_missing() from None
            raise
        if catalog.get("deleted"):
            raise self._history_missing()
        if (
            catalog.get("entry_id") != entry_id
            or catalog.get("owner_id") != actor.owner_id
            or catalog.get("path") != GitWriter.path_for(actor.owner_id, entry_id)
            or not isinstance(catalog.get("commit_sha"), str)
            or len(catalog["commit_sha"]) != 40
            or any(char not in "0123456789abcdef" for char in catalog["commit_sha"])
            or (revision is not None and catalog.get("revision") != revision)
        ):
            raise DomainError("CONTENT_CORRUPT", "Entry catalog identity mismatch", status_code=409)
        try:
            entry = self.get_entry(entry_id, actor=actor, revision=revision)
        except DomainError as exc:
            if exc.code == "CLASSIFICATION_FORBIDDEN":
                raise self._history_missing() from None
            raise
        if entry.get("visibility") != "public":
            try:
                enforce_read_classification(actor, {"data_classification": "personal"})
            except DomainError as exc:
                if exc.code == "CLASSIFICATION_FORBIDDEN":
                    raise self._history_missing() from None
                raise
        # Metadata such as a title must not be supplied by an unverified catalog.
        for key in ("entry_id", "revision", "title", "content_hash", "updated_at", "deleted"):
            if entry.get(key) != catalog.get(key):
                raise DomainError("CONTENT_CORRUPT", "Entry metadata mismatch", status_code=409)
        return entry

    def list_entry_versions(
        self, entry_id: str, *, actor: Actor, limit: int = 20, offset: int = 0,
    ) -> dict[str, Any]:
        """List authorized formal revisions only; bounded scans never disclose total counts."""
        authenticated(actor, "knowledge:read")
        self._validate_page(limit, offset)
        if limit > 100 or offset > 1000:
            raise invalid("History pagination exceeds limit")
        with self.store.reading() as connection:
            current = self._history_entry(connection, entry_id, actor)
            rows = connection.execute(
                "SELECT revision FROM kp_entry_revisions WHERE entry_id=? AND owner_id=? "
                "ORDER BY revision DESC LIMIT 1001", (entry_id, actor.owner_id),
            )
            items: list[dict[str, Any]] = []
            skipped = 0
            for scanned, row in enumerate(rows):
                if scanned == 1000:
                    raise DomainError("HISTORY_LIMIT_EXCEEDED", "History scan limit exceeded", status_code=422)
                try:
                    entry = self._history_entry(connection, entry_id, actor, row["revision"])
                except DomainError as exc:
                    if exc.code == "NOT_FOUND":
                        continue
                    raise
                if skipped < offset:
                    skipped += 1
                    continue
                items.append({key: entry[key] for key in (
                    "revision", "title", "updated_at", "content_hash", "classification_labels",
                )})
                if len(items) > limit:
                    break
            return {"entry_id": entry_id, "items": items[:limit], "has_more": len(items) > limit,
                    "classification_labels": current["classification_labels"]}

    def diff_entry_versions(
        self, entry_id: str, *, actor: Actor, from_revision: int, to_revision: int,
    ) -> dict[str, Any]:
        """Compare two exact authorized bodies without Git log, paths, or publication grants."""
        authenticated(actor, "knowledge:read")
        for revision in (from_revision, to_revision):
            if type(revision) is not int or not 1 <= revision <= 2_147_483_647:
                raise invalid("A positive exact revision is required")
        with self.store.reading() as connection:
            current = self._history_entry(connection, entry_id, actor)
            before = self._history_entry(connection, entry_id, actor, from_revision)
            after = self._history_entry(connection, entry_id, actor, to_revision)
            for entry in (before, after):
                if len(entry["body"].encode("utf-8")) > 131072 or len(entry["body"].splitlines()) > 4000:
                    raise DomainError("DIFF_LIMIT_EXCEEDED", "Entry exceeds diff size limit", status_code=422)
            delta_lines = difflib.unified_diff(
                before["body"].splitlines(keepends=True), after["body"].splitlines(keepends=True),
                fromfile=f"revision-{from_revision}", tofile=f"revision-{to_revision}",
            )
            delta = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in delta_lines)
            if len(delta.encode("utf-8")) > 307200:
                raise DomainError("DIFF_LIMIT_EXCEEDED", "Diff exceeds output size limit", status_code=422)
            return {"entry_id": entry_id, "from_revision": from_revision, "to_revision": to_revision,
                    "from_content_hash": before["content_hash"], "to_content_hash": after["content_hash"],
                    "unified_diff": delta, "classification_labels": sorted(set(
                        current["classification_labels"] + before["classification_labels"] + after["classification_labels"]
                    ))}

    def _kill_point(self, stage: str, journal: dict[str, Any]) -> None:
        if self.fault_injector is not None:
            self.fault_injector(stage, journal)

    def submit_learning_outcome(
        self,
        payload: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Record authenticated user evidence, deduped by device/session/version."""
        authenticated(actor, "knowledge:write")
        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN",
                "Learning evidence requires authenticated user provenance",
                status_code=403,
            )
        payload = validate_payload(payload)
        for key in ("device_id", "session_id"):
            required_text(payload, key, limit=256)
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise invalid("Learning version must be a positive integer")
        if not isinstance(payload.get("user_text", ""), str):
            raise invalid("user_text must preserve actual user words")
        if not isinstance(payload.get("result", {}), dict):
            raise invalid("result must be an object")
        semantic_key = canonical([payload["device_id"], payload["session_id"], version])

        def submit(connection: sqlite3.Connection) -> dict[str, Any]:
            existing = self._unique(
                connection, actor, "learning_outcome", semantic_key, fingerprint(payload)
            )
            if existing:
                return self.store.get(connection, "learning", existing, actor.owner_id)
            if payload.get("entry_id"):
                self.store.get(connection, "entry", payload["entry_id"], actor.owner_id)
            outcome = {
                "outcome_id": new_id("learn"),
                "owner_id": actor.owner_id,
                "payload": payload,
                "content_hash": fingerprint(payload),
                "revision": 1,
                "actor_id": actor.actor_id,
                "trace_id": trace_id,
                "created_at": now(),
            }
            self.store.put(connection, "learning", outcome["outcome_id"], outcome)
            self._save_unique(
                connection,
                actor,
                "learning_outcome",
                semantic_key,
                fingerprint(payload),
                outcome["outcome_id"],
            )
            self.store.event(
                connection,
                "learning.outcome.submitted.v1",
                outcome,
                outcome["outcome_id"],
                {"entry_id": payload.get("entry_id"), "session_id": payload["session_id"]},
            )
            return outcome

        return self._mutate(actor, "submit_learning_outcome", idempotency_key, payload, submit)

    def get_learning_outcome(self, outcome_id: str, *, actor: Actor) -> dict[str, Any]:
        """Read owned user learning evidence without claiming mastery."""
        return self._read("learning", outcome_id, actor)

    def backup(self, destination: str | Path, *, actor: Actor) -> dict[str, Any]:
        """Take a coherent DB/Git/referenced-object snapshot for operator recovery.

        This is an operator-only deployment backup, not an owner export endpoint.
        The destination must be new; existing backups and live data are never replaced.
        Rebuildable indexes are deliberately excluded from the authoritative snapshot.
        """
        authenticated(actor, "knowledge:backup")
        if actor.kind != "service":
            raise DomainError(
                "FORBIDDEN", "Deployment backup requires an operator service", status_code=403
            )
        target = require_portable_path(destination, state=True).resolve()
        for protected in (self.object_path, self.writer.repository, self.cache_path):
            if target.is_relative_to(protected) or protected.is_relative_to(target):
                raise invalid("Backup destination must not overlap live storage")
        if target.exists():
            raise DomainError(
                "DESTINATION_EXISTS", "Backup destination must be new", status_code=409
            )
        with self._apply_lock:
            self._recover_writer(actor=actor)
            with self.store.transaction() as connection:
                target.mkdir(parents=True)
                head = self.writer.head()
                with (
                    managed_sqlite_connection(sqlite3.connect(self.store.path)) as source_db,
                    managed_sqlite_connection(sqlite3.connect(target / "platform.db")) as target_db,
                ):
                    source_db.backup(target_db)
                shutil.copytree(self.writer.repository, target / "vault.git", symlinks=True)
                objects = []
                for row in connection.execute("SELECT data FROM kp_records WHERE kind='source'"):
                    source = json.loads(row["data"])
                    relative = source["object_path"]
                    source_path = self.object_path / relative
                    data = source_path.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    if digest != source["content_hash"]:
                        raise DomainError(
                            "OBJECT_CORRUPT",
                            "Backup source failed integrity verification",
                            status_code=409,
                        )
                    output = target / "objects" / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
                    objects.append({"path": relative, "sha256": digest, "bytes": len(data)})
                if self.writer.head() != head:
                    raise DomainError(
                        "BASE_STALE",
                        "Git changed during backup; snapshot is not complete",
                        status_code=409,
                    )
                manifest = {
                    "schema_version": 1,
                    "git_revision": head,
                    "policy_version": self.policy_version,
                    "objects": sorted(objects, key=lambda item: item["path"]),
                    "database_sha256": hashlib.sha256(
                        (target / "platform.db").read_bytes()
                    ).hexdigest(),
                    "complete": True,
                }
                (target / "manifest.json").write_text(canonical(manifest), encoding="utf-8")
                return manifest
