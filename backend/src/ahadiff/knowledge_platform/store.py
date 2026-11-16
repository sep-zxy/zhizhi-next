"""The single SQLite write boundary, atomic idempotency receipts and outbox."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ahadiff.core.sqlite_util import managed_sqlite_connection

if TYPE_CHECKING:
    from collections.abc import Iterator

from .auth import classification_labels, classify_record
from .contracts import canonical, new_id, now
from .errors import DomainError

SCHEMA = """
CREATE TABLE IF NOT EXISTS kp_schema (version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO kp_schema VALUES (1);
CREATE TABLE IF NOT EXISTS kp_records (
 kind TEXT NOT NULL, id TEXT NOT NULL, owner_id TEXT NOT NULL,
 revision INTEGER NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(kind,id));
CREATE INDEX IF NOT EXISTS kp_records_owner ON kp_records(kind,owner_id);
CREATE TABLE IF NOT EXISTS kp_receipts (
 owner_id TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
 input_hash TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,operation,key));
CREATE TABLE IF NOT EXISTS kp_unique_keys (
 owner_id TEXT NOT NULL, namespace TEXT NOT NULL, key TEXT NOT NULL,
 input_hash TEXT NOT NULL, record_id TEXT NOT NULL,
 PRIMARY KEY(owner_id,namespace,key));
CREATE TABLE IF NOT EXISTS kp_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
 type TEXT NOT NULL, owner_id TEXT NOT NULL, aggregate_id TEXT NOT NULL,
 data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kp_events_owner ON kp_events(owner_id,sequence);
CREATE TABLE IF NOT EXISTS kp_deliveries (
 consumer TEXT NOT NULL, event_id TEXT NOT NULL REFERENCES kp_events(event_id),
 state TEXT NOT NULL, lease_token TEXT, lease_until TEXT, attempts INTEGER NOT NULL DEFAULT 0,
 receipt TEXT, PRIMARY KEY(consumer,event_id));
CREATE TABLE IF NOT EXISTS kp_entry_revisions (
 entry_id TEXT NOT NULL, revision INTEGER NOT NULL, owner_id TEXT NOT NULL,
 commit_sha TEXT NOT NULL, path TEXT NOT NULL, content_hash TEXT NOT NULL,
 metadata TEXT NOT NULL, PRIMARY KEY(entry_id,revision));
CREATE TABLE IF NOT EXISTS kp_writer_journal (
 change_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state TEXT NOT NULL,
 data TEXT NOT NULL);
"""


class DomainStore:
    """Serialize all writes on one durable connection; workers use the service API."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            str(path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.executescript(SCHEMA)

    @classmethod
    def open_readonly(cls, path: str | Path) -> DomainStore:
        """Read existing committed state, without DDL, migration, backup or recovery."""
        path = Path(path).resolve()
        if not path.is_file():
            raise DomainError("NOT_FOUND", "Existing ledger is required", status_code=404)
        result = object.__new__(cls)
        result.path = path
        result._lock = threading.RLock()
        result.connection = sqlite3.connect(
            path.as_uri() + "?mode=ro", uri=True, isolation_level=None, check_same_thread=False
        )
        result.connection.row_factory = sqlite3.Row
        result.connection.execute("PRAGMA query_only=ON")
        return result

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run one serialized write transaction, rolling back every incomplete effect."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """Protect this connection from interleaving with its own transaction."""
        with self._lock:
            yield self.connection

    @staticmethod
    def get(
        connection: sqlite3.Connection, kind: str, record_id: str, owner_id: str
    ) -> dict[str, Any]:
        """Owner-scoped lookup deliberately hides existence across principals."""
        row = connection.execute(
            "SELECT data FROM kp_records WHERE kind=? AND id=? AND owner_id=?",
            (kind, record_id, owner_id),
        ).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "The requested record was not found", status_code=404)
        return json.loads(row["data"])

    @staticmethod
    def label_record(connection: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
        """Propagate classifications across owned references, including legacy records.

        Classification is a monotonic read boundary, not publication eligibility.
        Referenced public metadata never downgrades private input or prior labels.
        """
        kinds = {
            "intake_id": "intake",
            "source_id": "source",
            "external_document_id": "external_document",
            "learning_outcome_id": "learning",
            "outcome_id": "learning",
            "change_id": "change",
            "entry_id": "entry",
        }
        labels = set(classification_labels(record, default=None))
        visited: set[tuple[str, str]] = set()
        owner = record.get("owner_id")

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in kinds and isinstance(item, str) and owner:
                        identity = (kinds[key], item)
                        if identity in visited:
                            continue
                        visited.add(identity)
                        row = connection.execute(
                            "SELECT data FROM kp_records WHERE kind=? AND id=? AND owner_id=?",
                            (*identity, owner),
                        ).fetchone()
                        if row:
                            related = json.loads(row["data"])
                            labels.update(classification_labels(related, default=None))
                            visit(related)
                    else:
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(record)
        labels = labels or {"personal"}
        record["classification_labels"] = sorted(labels)
        record["data_classification"] = classify_record({"classification_labels": sorted(labels)})
        return record

    @staticmethod
    def put(
        connection: sqlite3.Connection, kind: str, record_id: str, record: dict[str, Any]
    ) -> None:
        """Save a record within a caller-owned domain transaction."""
        DomainStore.label_record(connection, record)
        cursor = connection.execute(
            "INSERT INTO kp_records(kind,id,owner_id,revision,data) VALUES(?,?,?,?,?) "
            "ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,data=excluded.data "
            "WHERE kp_records.owner_id=excluded.owner_id",
            (kind, record_id, record["owner_id"], record.get("revision", 1), canonical(record)),
        )
        if cursor.rowcount != 1:
            raise DomainError("NOT_FOUND", "The requested record was not found", status_code=404)

    @staticmethod
    def event(
        connection: sqlite3.Connection,
        event_type: str,
        record: dict[str, Any],
        aggregate_id: str,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert an event in the same transaction as its authoritative state change."""
        event = {
            "event_id": new_id("evt"),
            "type": event_type,
            "version": 1,
            "owner_id": record["owner_id"],
            "trace_id": trace_id or record.get("trace_id"),
            "aggregate_id": aggregate_id,
            "aggregate_revision": record.get("revision", 1),
            "occurred_at": now(),
            "payload": payload or {},
            "data_classification": classify_record(record, default="personal"),
            "classification_labels": sorted(classification_labels(record, default="personal")),
        }
        cursor = connection.execute(
            "INSERT INTO kp_events(event_id,type,owner_id,aggregate_id,data) VALUES(?,?,?,?,?)",
            (event["event_id"], event_type, event["owner_id"], aggregate_id, canonical(event)),
        )
        event["sequence"] = cursor.lastrowid
        return event

    def backup(self, destination: str | Path) -> None:
        """Take a consistent SQLite backup, including committed WAL contents."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, managed_sqlite_connection(sqlite3.connect(destination)) as target:
            self.connection.backup(target)

    def close(self) -> None:
        """Close the domain connection after workers have stopped."""
        with self._lock:
            self.connection.close()
