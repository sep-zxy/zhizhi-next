"""Explicit references to authorized immutable Wiki revisions, not new learning cards."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from anyio import to_thread

from .backend import BackendError, canonical_json
from .desktop import desktop_backend, desktop_device_id

if TYPE_CHECKING:
    from pathlib import Path


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scope() -> str:
    return _hash(canonical_json([desktop_backend().base_url, desktop_device_id()]))


def context_authority_key(base_url: str, device_id: str, owner_id: str) -> str:
    """Pure binding shared by desktop contexts and separately bounded verification."""
    if any(not isinstance(value, str) or not value for value in (base_url, device_id, owner_id)):
        raise ValueError("A complete context identity binding is required")
    scope = _hash(canonical_json([base_url.rstrip("/"), device_id]))
    return _hash(canonical_json([scope, owner_id]))


def _authority(owner_id: str, scope: str) -> str:
    if scope != _scope():
        raise BackendError("CONFIGURATION_CHANGED", "服务绑定已变化", False, 409)
    return context_authority_key(desktop_backend().base_url, desktop_device_id(), owner_id)


async def _entry(entry_id: str, revision: int) -> tuple[dict[str, Any], str]:
    backend, scope = desktop_backend(), _scope()
    response = await backend.read_entry_revision(entry_id, revision)
    if desktop_backend() is not backend or _scope() != scope:
        raise BackendError("CONFIGURATION_CHANGED", "服务绑定已变化", False, 409)
    item = response.get("data")
    if (
        not isinstance(item, dict)
        or item.get("entry_id") != entry_id
        or type(item.get("revision")) is not int
        or item["revision"] != revision
        or not isinstance(item.get("body"), str)
        or not isinstance(item.get("title"), str)
        or not isinstance(item.get("owner_id"), str)
        or not item["owner_id"]
        or item.get("content_hash") != _hash(item["body"])
    ):
        raise BackendError("INVALID_RESPONSE", "无法核验精确正式版本", False, 502)
    if item.get("deleted") is True:
        raise BackendError("NOT_FOUND", "该版本已删除", False, 404)
    refs = item.get("evidence_refs", [])
    if not isinstance(refs, list):
        raise BackendError("INVALID_RESPONSE", "引用格式无效", False, 502)
    labels = item.get("classification_labels", response.get("classification_labels", []))
    classification = item.get("data_classification", response.get("data_classification", "unknown"))
    if (
        not isinstance(classification, str)
        or not isinstance(labels, list)
        or not all(isinstance(label, str) for label in labels)
    ):
        raise BackendError("INVALID_RESPONSE", "分类格式无效", False, 502)
    return {
        "entry_id": entry_id,
        "revision": revision,
        "title": item["title"],
        "body": item["body"],
        "content_hash": item["content_hash"],
        "data_classification": classification,
        "classification_labels": labels,
        "evidence_refs": [
            {
                key: value
                for key, value in ref.items()
                if key
                in {
                    "source_id",
                    "learning_outcome_id",
                    "external_document_id",
                    "revision",
                    "source_revision",
                }
                and isinstance(value, str | int)
                and not isinstance(value, bool)
            }
            for ref in refs
            if isinstance(ref, dict)
        ],
    }, _authority(item["owner_id"], scope)


def _cards(path: Path) -> list[dict[str, Any]]:
    from ahadiff.growth.local import GrowthLocalRepository

    with GrowthLocalRepository(path) as ledger:
        return [
            dict(row)
            for row in ledger.connection.execute(
                "SELECT cards.card_id,cards.card_version,concepts.title FROM knowledge_cards cards "
                "JOIN growth_tasks tasks ON tasks.task_id=cards.card_id "
                "JOIN knowledge_concepts concepts USING(concept_id) "
                "JOIN growth_topics topics USING(topic_id) "
                "WHERE tasks.progress != 'dismissed' AND topics.status = 'active' "
                "ORDER BY cards.updated_at DESC, cards.card_id"
            )
        ]


async def preview_context(path: Path, entry_id: str, revision: int) -> dict[str, Any]:
    """Authorize the exact revision before reading local cards. No persistent association."""
    entry, _ = await _entry(entry_id, revision)
    return {"entry": entry, "cards": await to_thread.run_sync(_cards, path)}


def _public(row: Any) -> dict[str, Any]:
    return {
        "context_id": row["context_id"],
        "entry_id": row["entry_id"],
        "revision": row["entry_revision"],
        "content_hash": row["content_hash"],
        "card_id": row["card_id"],
        "card_version": row["card_version"],
        "created_at": row["created_at"],
        "state": "linked",
    }


def _attach(path: Path, scope: str, authority: str, payload: dict[str, Any]) -> dict[str, Any]:
    from ahadiff.growth.local import GrowthLocalRepository

    with GrowthLocalRepository(path) as ledger:
        connection = ledger.connection
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            digest = _hash(canonical_json(payload))
            previous = connection.execute(
                "SELECT input_hash,context_id FROM knowledge_entry_context_requests "
                "WHERE authority_key=? AND request_id=?",
                (authority, payload["request_id"]),
            ).fetchone()
            if previous:
                if previous["input_hash"] != digest:
                    raise BackendError("IDEMPOTENCY_CONFLICT", "请求键已绑定不同操作", False, 409)
                return _public(
                    connection.execute(
                        "SELECT * FROM knowledge_entry_contexts WHERE context_id=?",
                        (previous["context_id"],),
                    ).fetchone()
                )
            card = connection.execute(
                "SELECT cards.card_version,tasks.progress,topics.status FROM knowledge_cards cards "
                "JOIN growth_tasks tasks ON tasks.task_id=cards.card_id "
                "JOIN growth_topics topics USING(topic_id) WHERE cards.card_id=?",
                (payload["target_card_id"],),
            ).fetchone()
            if not card or card["progress"] == "dismissed" or card["status"] != "active":
                raise BackendError("NOT_FOUND", "目标学习卡不可用", False, 404)
            if card["card_version"] != payload["expected_card_version"]:
                raise BackendError("CARD_VERSION_STALE", "目标卡版本已变化", False, 409)
            existing = connection.execute(
                "SELECT * FROM knowledge_entry_contexts WHERE authority_key=? AND entry_id=? "
                "AND entry_revision=? AND card_id=? AND card_version=?",
                (
                    authority,
                    payload["entry_id"],
                    payload["revision"],
                    payload["target_card_id"],
                    payload["expected_card_version"],
                ),
            ).fetchone()
            context_id = existing["context_id"] if existing else str(uuid4())
            if existing and existing["content_hash"] != payload["expected_content_hash"]:
                raise BackendError("CONTENT_CONFLICT", "既有正式引用内容不符", False, 409)
            if not existing:
                connection.execute(
                    "INSERT INTO knowledge_entry_contexts VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        context_id,
                        scope,
                        authority,
                        payload["entry_id"],
                        payload["revision"],
                        payload["expected_content_hash"],
                        payload["target_card_id"],
                        payload["expected_card_version"],
                        datetime.now(UTC).isoformat(),
                    ),
                )
            connection.execute(
                "INSERT INTO knowledge_entry_context_requests VALUES (?,?,?,?)",
                (authority, payload["request_id"], digest, context_id),
            )
            return _public(
                connection.execute(
                    "SELECT * FROM knowledge_entry_contexts WHERE context_id=?",
                    (context_id,),
                ).fetchone()
            )


async def attach_context(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Explicit action: reauthorize exact body/hash, then atomically CAS an existing card."""
    scope = _scope()
    entry, authority = await _entry(payload["entry_id"], payload["revision"])
    if entry["content_hash"] != payload["expected_content_hash"]:
        raise BackendError("CONTENT_CONFLICT", "正式内容摘要已变化", False, 409)
    return await to_thread.run_sync(_attach, path, scope, authority, payload)


def _stored(path: Path, card_id: str, scope: str) -> list[dict[str, Any]]:
    from ahadiff.growth.local import GrowthLocalRepository

    with GrowthLocalRepository(path) as ledger:
        if not ledger.connection.execute(
            "SELECT 1 FROM knowledge_cards WHERE card_id=?", (card_id,)
        ).fetchone():
            raise BackendError("NOT_FOUND", "目标学习卡不存在", False, 404)
        return [
            dict(row)
            for row in ledger.connection.execute(
                "SELECT * FROM knowledge_entry_contexts WHERE card_id=? AND backend_scope=? "
                "ORDER BY created_at,context_id",
                (card_id, scope),
            )
        ]


async def read_contexts(path: Path, card_id: str) -> dict[str, Any]:
    """Never show a cached formal body or a reference after authorization is lost."""
    UUID(card_id)
    rows = await to_thread.run_sync(_stored, path, card_id, _scope())
    items = []
    for row in rows:
        entry, authority = await _entry(row["entry_id"], row["entry_revision"])
        if authority != row["authority_key"]:
            continue
        if entry["content_hash"] != row["content_hash"]:
            raise BackendError("CONTENT_CONFLICT", "历史引用摘要不符", False, 409)
        items.append({**_public(row), "entry": entry})
    return {"items": items, "has_more": False}
