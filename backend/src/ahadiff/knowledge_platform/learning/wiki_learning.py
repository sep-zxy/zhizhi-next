"""Explicit free learning from exact formal revisions, never synthetic engineering cards."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

from anyio import to_thread

from ..auth import classification_labels, classify_record
from .backend import BackendError, canonical_json
from .desktop import desktop_backend, desktop_device_id
from .entry_context import _entry, _scope, context_authority_key
from .model_bridge import legacy_input_classification
from .understanding import _submission_id, _view, note_hash

if TYPE_CHECKING:
    from pathlib import Path

_KIND = "wiki_entry_understanding"
_ENTRY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_HASH = re.compile(r"[a-f0-9]{64}")
_NAMESPACE = UUID("541a206a-203a-4815-92cc-e9b1728753f8")


def verify_saved_learning_outcome(
    path: Path, facts: dict[str, Any], *, backend_url: str, device_id: str, owner_id: str
) -> bool:
    """Read-only, default-deny verification for a separately configured local guard.

    The caller MUST bind path and identities to its approved isolated state,
    never to request fields. No globals, schema migration, model, or networking.
    Prior saved versions can be proven by immutable mutation receipts, rather
    than trusting an arbitrary context UUID or metadata hash from HTTP.
    """
    try:
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
            return False
        for candidate in (path, *path.parents):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
            if candidate == path and not stat.S_ISREG(info.st_mode):
                return False
        # SQLite may consult these existing coordination files; never follow a
        # redirected WAL/SHM either. A missing sidecar is not a missing database.
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = path.with_name(path.name + suffix)
            try:
                info = sidecar.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
        metadata = facts["metadata"]
        context_id = _uuid(metadata["context_id"])
        version = _integer(metadata["context_version"])
        digest = _digest(metadata["note_sha256"])
        _integer(metadata["source_revision"])
        _digest(metadata["source_content_hash"])
        text = facts["user_text"]
        authority = context_authority_key(backend_url, device_id, owner_id)
        scope = note_hash(canonical_json([backend_url.rstrip("/"), device_id]))
        if (
            facts.get("device_id") != device_id
            or facts.get("entry_id") is not None
            or type(facts.get("version")) is not int
            or facts["version"] != 1
            or facts.get("session_id") != f"understanding:wiki:{context_id}:{version}"
            or facts.get("result") != {"practice_kind": "open_response", "graded": False}
            or facts["result"].get("graded") is not False
            or metadata.get("submission_kind") != _KIND
            or metadata.get("source_kind") != "wiki_entry"
            or metadata.get("authority_key") != authority
            or metadata.get("suggested_entry_id") != str(uuid5(_NAMESPACE, authority + context_id))
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 20_000
            or note_hash(text) != digest
        ):
            return False
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 33:
                return False
            row = connection.execute(
                "SELECT * FROM wiki_entry_learning_contexts WHERE context_id=? "
                "AND backend_scope=? AND authority_key=?",
                (context_id, scope, authority),
            ).fetchone()
            if row is None or (
                row["entry_id"] != metadata.get("source_entry_id")
                or row["entry_revision"] != metadata.get("source_revision")
                or row["content_hash"] != metadata.get("source_content_hash")
                or row["context_version"] < version
                or note_hash(row["note_text"]) != row["note_sha256"]
            ):
                return False
            if row["context_version"] == version:
                return row["note_text"] == text and row["note_sha256"] == digest
            snapshots = connection.execute(
                "SELECT response_json FROM wiki_entry_learning_requests WHERE context_id=? "
                "AND authority_key=? AND operation='note' "
                "AND json_extract(response_json,'$.context_version')=? "
                "AND json_extract(response_json,'$.note_sha256')=? LIMIT 1",
                (context_id, authority, version, digest),
            ).fetchall()
            if not snapshots:
                return False
            saved = json.loads(snapshots[0]["response_json"])
            return saved == {
                **dict(row),
                "context_version": version,
                "note_text": text,
                "note_sha256": digest,
                "updated_at": saved.get("updated_at"),
            }
        finally:
            connection.close()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, BackendError):
        return False


def _invalid() -> BackendError:
    return BackendError("INVALID_INPUT", "无法核验学习上下文参数", False, 422)


def _uuid(value: Any) -> str:
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise _invalid()
    return value


def _integer(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        raise _invalid()
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise _invalid()
    return value


def _summary(row: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "context_id": row["context_id"],
        "source_kind": "wiki_entry",
        "context_version": row["context_version"],
        "entry_id": row["entry_id"],
        "revision": row["entry_revision"],
        "content_hash": row["content_hash"],
        "title": entry["title"],
        "note_sha256": row["note_sha256"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "state": "reading",
    }


def _detail(row: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    return {**_summary(row, entry), "entry": entry, "note_text": row["note_text"]}


def _rows(path: Path, scope: str, context_id: str | None, limit: int, offset: int) -> list[dict]:
    from ahadiff.growth.local import GrowthLocalRepository

    with GrowthLocalRepository(path) as ledger:
        if context_id is not None:
            query = ledger.connection.execute(
                "SELECT * FROM wiki_entry_learning_contexts WHERE backend_scope=? AND context_id=?",
                (scope, context_id),
            )
        else:
            query = ledger.connection.execute(
                "SELECT * FROM wiki_entry_learning_contexts WHERE backend_scope=? "
                "ORDER BY updated_at DESC,context_id LIMIT ? OFFSET ?",
                (scope, limit + 1, offset),
            )
        return [dict(row) for row in query]


async def _authorize(row: dict[str, Any]) -> dict[str, Any]:
    entry, authority = await _entry(row["entry_id"], row["entry_revision"])
    if authority != row["authority_key"] or _scope() != row["backend_scope"]:
        raise BackendError("NOT_FOUND", "学习上下文不可用", False, 404)
    if entry["content_hash"] != row["content_hash"]:
        raise BackendError("CONTENT_CONFLICT", "历史正式版本摘要不符", False, 409)
    if note_hash(row["note_text"]) != row["note_sha256"]:
        raise BackendError("CONTEXT_CORRUPT", "本人原话摘要不符", False, 409)
    return entry


async def _authorized_row(path: Path, context_id: str) -> tuple[dict, dict]:
    rows = await to_thread.run_sync(_rows, path, _scope(), _uuid(context_id), 1, 0)
    if not rows:
        raise BackendError("NOT_FOUND", "学习上下文不可用", False, 404)
    return rows[0], await _authorize(rows[0])


async def read_learning_context(path: Path, context_id: str) -> dict[str, Any]:
    row, entry = await _authorized_row(path, context_id)
    return _detail(row, entry)


async def list_learning_contexts(path: Path, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise _invalid()
    rows = await to_thread.run_sync(_rows, path, _scope(), None, limit, offset)
    # Even the look-ahead record must be authorized before disclosing has_more.
    items = [_summary(row, await _authorize(row)) for row in rows]
    return {"items": items[:limit], "has_more": len(rows) > limit}


def _mutate(
    path: Path, scope: str, authority: str, payload: dict[str, Any], operation: str
) -> dict[str, Any]:
    from ahadiff.growth.local import GrowthLocalRepository

    with GrowthLocalRepository(path) as ledger, ledger.connection as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _scope() != scope:
            raise BackendError("CONFIGURATION_CHANGED", "服务绑定已变化", False, 409)
        digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        receipt = connection.execute(
            "SELECT * FROM wiki_entry_learning_requests "
            "WHERE authority_key=? AND operation=? AND request_id=?",
            (authority, operation, payload["request_id"]),
        ).fetchone()
        if receipt:
            if receipt["input_hash"] != digest:
                raise BackendError("IDEMPOTENCY_CONFLICT", "请求键已绑定其他操作", False, 409)
            return json.loads(receipt["response_json"])
        now = datetime.now(UTC).isoformat()
        if operation == "create":
            existing = connection.execute(
                "SELECT * FROM wiki_entry_learning_contexts "
                "WHERE authority_key=? AND entry_id=? AND entry_revision=?",
                (authority, payload["entry_id"], payload["revision"]),
            ).fetchone()
            if existing:
                if existing["content_hash"] != payload["expected_content_hash"]:
                    raise BackendError("CONTENT_CONFLICT", "既有正式版本摘要不符", False, 409)
                result = dict(existing)
            else:
                context_id = str(uuid4())
                connection.execute(
                    "INSERT INTO wiki_entry_learning_contexts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        context_id,
                        scope,
                        authority,
                        payload["entry_id"],
                        payload["revision"],
                        payload["expected_content_hash"],
                        1,
                        "",
                        note_hash(""),
                        now,
                        now,
                    ),
                )
                result = dict(
                    connection.execute(
                        "SELECT * FROM wiki_entry_learning_contexts WHERE context_id=?",
                        (context_id,),
                    ).fetchone()
                )
        else:
            row = connection.execute(
                "SELECT * FROM wiki_entry_learning_contexts WHERE context_id=? AND authority_key=?",
                (payload["context_id"], authority),
            ).fetchone()
            if row is None:
                raise BackendError("NOT_FOUND", "学习上下文不可用", False, 404)
            if row["context_version"] != payload["expected_context_version"]:
                raise BackendError("CONTEXT_VERSION_STALE", "本人原话版本已变化", False, 409)
            if row["note_text"] != payload["note_text"]:
                connection.execute(
                    "UPDATE wiki_entry_learning_contexts SET note_text=?,note_sha256=?,"
                    "context_version=context_version+1,updated_at=? WHERE context_id=?",
                    (
                        payload["note_text"],
                        note_hash(payload["note_text"]),
                        now,
                        payload["context_id"],
                    ),
                )
            result = dict(
                connection.execute(
                    "SELECT * FROM wiki_entry_learning_contexts WHERE context_id=?",
                    (payload["context_id"],),
                ).fetchone()
            )
        connection.execute(
            "INSERT INTO wiki_entry_learning_requests VALUES (?,?,?,?,?,?)",
            (
                authority,
                operation,
                payload["request_id"],
                digest,
                result["context_id"],
                canonical_json(result),
            ),
        )
        return result


async def create_learning_context(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"request_id", "entry_id", "revision", "expected_content_hash"}:
        raise _invalid()
    _uuid(payload["request_id"])
    _integer(payload["revision"])
    _digest(payload["expected_content_hash"])
    if not isinstance(payload["entry_id"], str) or not _ENTRY.fullmatch(payload["entry_id"]):
        raise _invalid()
    scope = _scope()
    entry, authority = await _entry(payload["entry_id"], payload["revision"])
    if entry["content_hash"] != payload["expected_content_hash"]:
        raise BackendError("CONTENT_CONFLICT", "请重新核对正式版本", False, 409)
    row = await to_thread.run_sync(_mutate, path, scope, authority, payload, "create")
    return _detail(row, entry)


async def save_learning_note(
    path: Path, context_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if set(payload) != {"request_id", "expected_context_version", "note_text"}:
        raise _invalid()
    _uuid(payload["request_id"])
    _integer(payload["expected_context_version"])
    if not isinstance(payload["note_text"], str) or len(payload["note_text"]) > 20_000:
        raise _invalid()
    try:
        payload["note_text"].encode("utf-8")
    except UnicodeEncodeError as error:
        raise _invalid() from error
    row, entry = await _authorized_row(path, context_id)
    result = await to_thread.run_sync(
        _mutate,
        path,
        row["backend_scope"],
        row["authority_key"],
        {**payload, "context_id": context_id},
        "note",
    )
    return _detail(result, entry)


def _records(context_id: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for record in desktop_backend().outbox.records():
        payload = record.get("payload", {})
        meta = payload.get("metadata", {})
        if (
            record.get("path") != "/api/knowledge/v1/learning/outcomes"
            or payload.get("device_id") != desktop_device_id()
            or meta.get("submission_kind") != _KIND
            or meta.get("context_id") != context_id
            or meta.get("authority_key") != row["authority_key"]
        ):
            continue
        if (
            not isinstance(payload.get("user_text"), str)
            or note_hash(payload["user_text"]) != meta.get("note_sha256")
            or payload.get("session_id")
            != f"understanding:wiki:{context_id}:{meta.get('context_version')}"
            or meta.get("source_entry_id") != row["entry_id"]
            or meta.get("source_revision") != row["entry_revision"]
            or meta.get("source_content_hash") != row["content_hash"]
        ):
            raise BackendError("SUBMISSION_CORRUPT", "不可变提交校验失败", False, 409)
        records.append(record)
    return sorted(
        records, key=lambda value: value["payload"]["metadata"]["context_version"], reverse=True
    )


def _snapshot(
    path: Path, row: dict[str, Any], entry: dict[str, Any], context_version: int, note_sha256: str
) -> dict[str, Any]:
    """Check note CAS and persist its outbox snapshot under the same write lock.

    The outbox has its own crash-safe idempotency lock. No network is performed
    while the SQLite transaction holds the note stable.
    """
    from ahadiff.growth.local import GrowthLocalRepository

    backend = desktop_backend()
    context_id = row["context_id"]
    record = next(
        (
            item
            for item in _records(context_id, row)
            if item["payload"]["metadata"]["context_version"] == context_version
            and item["payload"]["metadata"]["note_sha256"] == note_sha256
        ),
        None,
    )
    if record is not None:
        return record
    with GrowthLocalRepository(path) as ledger, ledger.connection as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _scope() != row["backend_scope"]:
            raise BackendError("CONFIGURATION_CHANGED", "服务绑定已变化", False, 409)
        fresh = connection.execute(
            "SELECT * FROM wiki_entry_learning_contexts WHERE context_id=? AND authority_key=?",
            (context_id, row["authority_key"]),
        ).fetchone()
        if fresh is None:
            raise BackendError("NOT_FOUND", "学习上下文不可用", False, 404)
        row = dict(fresh)
        if row["context_version"] != context_version or row["note_sha256"] != note_sha256:
            raise BackendError("UNDERSTANDING_STALE", "本人原话版本已变化", False, 409)
        if note_hash(row["note_text"]) != note_sha256:
            raise BackendError("CONTEXT_CORRUPT", "本人原话摘要不符", False, 409)
        if not row["note_text"].strip():
            raise BackendError("INVALID_INPUT", "请先保存自己的真实理解", False, 422)
        classification = legacy_input_classification()
        if classification not in {
            "public",
            "personal",
            "employer",
            "third_party_private",
            "secret",
        }:
            raise BackendError("WAITING_CONFIGURATION", "缺少可信分类配置", True, 503)
        privacy = {
            "source": entry,
            "input_classification": classification,
            "evidence_classification": "personal",
        }
        return backend.outbox.enqueue_facts(
            {
                "device_id": desktop_device_id(),
                "session_id": f"understanding:wiki:{context_id}:{context_version}",
                "entry_id": None,
                "user_text": row["note_text"],
                "result": {"practice_kind": "open_response", "graded": False},
                "metadata": {
                    "submission_kind": _KIND,
                    "context_id": context_id,
                    "context_version": context_version,
                    "authority_key": row["authority_key"],
                    "source_kind": "wiki_entry",
                    "source_entry_id": row["entry_id"],
                    "source_revision": row["entry_revision"],
                    "source_content_hash": row["content_hash"],
                    "note_sha256": note_sha256,
                    "suggested_entry_id": str(uuid5(_NAMESPACE, row["authority_key"] + context_id)),
                    "title": "本人理解：" + entry["title"],
                    "evidence_classification": classify_record(privacy),
                    "classification_labels": sorted(classification_labels(privacy)),
                },
            }
        )


async def submit_learning_understanding(
    path: Path, context_id: str, *, context_version: int, note_sha256: str
) -> dict[str, Any]:
    _integer(context_version)
    _digest(note_sha256)
    row, entry = await _authorized_row(path, context_id)
    backend = desktop_backend()
    record = await to_thread.run_sync(_snapshot, path, row, entry, context_version, note_sha256)
    if desktop_backend() is not backend or _scope() != row["backend_scope"]:
        raise BackendError("CONFIGURATION_CHANGED", "服务绑定已变化", False, 409)
    with suppress(BackendError):
        await backend.submit_learning_outcome(record["payload"])
    current = backend.outbox.get(record["idempotency_key"])
    if current is None:
        raise BackendError("SUBMISSION_CORRUPT", "提交记录不可恢复", False, 409)
    return await _view(backend, current)


async def read_learning_submissions(
    path: Path,
    context_id: str,
    *,
    submission_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise _invalid()
    row, _ = await _authorized_row(path, context_id)
    backend = desktop_backend()
    records = _records(context_id, row)
    if submission_id is not None:
        _digest(submission_id)
        record = next((item for item in records if _submission_id(item) == submission_id), None)
        if record is None:
            raise BackendError("NOT_FOUND", "理解提交不存在", False, 404)
        return await _view(backend, record)
    return {
        "items": [await _view(backend, item) for item in records[offset : offset + limit]],
        "has_more": len(records) > offset + limit,
    }
