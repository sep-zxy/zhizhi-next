"""Integrate the existing knowledge-first UI without duplicating Wiki authority."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .backend import BackendError, RemoteKnowledgeBackend
from .model_bridge import legacy_input_classification, unified_mode_enabled

_backend: RemoteKnowledgeBackend | None = None
_device_id: str | None = None


def configure_desktop_backend(backend: RemoteKnowledgeBackend | None, *, device_id: str) -> None:
    """Set one remote backend for unified mode; never enable dual writes."""
    if not device_id.strip():
        raise ValueError("A persistent device identifier is required")
    global _backend, _device_id
    _backend, _device_id = backend, device_id


def desktop_backend() -> RemoteKnowledgeBackend:
    """Fail closed when unified mode was enabled without a remote service."""
    if not unified_mode_enabled() or _backend is None or _device_id is None:
        raise BackendError(
            "WAITING_CONFIGURATION", "统一知识模式尚未配置服务，未回退本地写入", True
        )
    return _backend


def entry_id_for_concept(concept_id: str) -> str:
    """Preserve one stable entry identity across devices and replay."""
    if not concept_id:
        raise ValueError("卡片缺少稳定概念身份")
    return str(uuid5(NAMESPACE_URL, "zhizhi:concept:" + concept_id))


def desktop_device_id() -> str:
    """Return only the explicitly configured durable device identity."""
    desktop_backend()
    assert _device_id is not None
    return _device_id


def learning_facts(detail: dict[str, Any]) -> dict[str, Any]:
    """Export observed facts and user-authored notes, never a mastery score."""
    if _device_id is None:
        raise BackendError("WAITING_CONFIGURATION", "缺少持久设备身份", True)
    card, progress = detail["card"], detail["learning"]
    return {
        "device_id": _device_id,
        "session_id": f"{card['card_id']}:{card['card_version']}",
        "entry_id": None,
        "user_text": progress.get("note_text") or "",
        "result": {
            "completed_at": progress.get("completed_at"),
            "explanation_seen_at": progress.get("back_seen_at"),
            "first_answers": {
                kind: {
                    key: answer.get(key)
                    for key in (
                        "question_id",
                        "question_version",
                        "option_id",
                        "correct",
                        "assisted",
                        "explanation_confirmed",
                    )
                }
                for kind, answer in progress.get("answers", {}).items()
            },
        },
        "metadata": {
            "evidence_classification": legacy_input_classification(),
            "concept_id": card["concept_id"],
            "card_id": card["card_id"],
            "suggested_entry_id": entry_id_for_concept(card["concept_id"]),
            "card_version": card["card_version"],
            "title": card.get("title") or card["learning_goal"],
        },
    }


async def submit_completed_learning(detail: dict[str, Any]) -> dict[str, Any]:
    """Record completion remotely or truthfully expose durable pending delivery."""
    backend = desktop_backend()
    try:
        response = await backend.submit_learning_facts(learning_facts(detail))
        return {"state": "submitted", **response["data"], "task_id": response.get("task_id")}
    except BackendError as error:
        if not error.retryable:
            raise
        return {"state": "pending", "error": {"code": error.code, "retryable": True}}


async def read_remote_original(concept_id: str) -> dict[str, Any] | None:
    """Fetch canonical content; network/auth failures must not look like absence."""
    try:
        return (await desktop_backend().read_entry(entry_id_for_concept(concept_id)))["data"]
    except BackendError as error:
        if error.status_code == 404 and error.code == "NOT_FOUND":
            # The generator may have reconciled this historical concept with an
            # existing title/alias. Its metadata binding is authoritative.
            entries = await desktop_backend().list_entries()
            matches = [
                entry
                for entry in entries
                if entry.get("metadata", {}).get("concept_id") == concept_id
                or concept_id in entry.get("metadata", {}).get("concept_ids", [])
            ]
            if len(matches) > 1:
                raise BackendError(
                    "AMBIGUOUS_CONCEPT", "多个条目绑定同一概念，请先核对", False, 409
                ) from error
            return matches[0] if matches else None
        raise


async def remote_articles() -> list[dict[str, Any]]:
    """Adapt canonical revisions for the existing desktop read model."""
    return [
        {
            "concept_id": entry.get("metadata", {}).get("concept_id", ""),
            "article_id": entry["entry_id"],
            "title": entry["title"],
            "relative_path": entry.get("path", ""),
            "version": entry["revision"],
            "published_at": entry.get("updated_at", ""),
            "markdown": entry.get("body", ""),
        }
        for entry in await desktop_backend().list_entries()
    ]


async def remote_draft_status(draft: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reconstruct acknowledged delivery after restart without changing local history."""
    if draft is None:
        return None
    for record in reversed(desktop_backend().outbox.records()):
        proposal = record["payload"]
        if (
            record["path"] != "/api/knowledge/v1/changes"
            or proposal.get("metadata", {}).get("draft_id") != draft["draft_id"]
            or proposal.get("body") != draft["markdown"]
        ):
            continue
        if record["state"] != "acknowledged":
            return {**draft, "delivery_error": record.get("error", {}).get("code", "PENDING")}
        change_id = record["response"]["data"]["change_id"]
        try:
            change = (await desktop_backend().read_change(change_id))["data"]
        except BackendError as error:
            return {
                **draft,
                "status": "submitted",
                "change_id": change_id,
                "delivery_error": error.code,
            }
        if change.get("committed_revision"):
            return {**draft, "status": "published", "change_id": change_id}
        return {
            **draft,
            "status": "submitted",
            "change_id": change_id,
            "review_state": change.get("state"),
        }
    return draft


async def submit_remote_draft(
    draft: dict[str, Any], detail: dict[str, Any], *, request_id: str
) -> dict[str, Any]:
    """Submit original facts and a version-bound draft to the common review path."""
    from ahadiff.knowledge_platform.auth import classification_labels, classify_record

    backend = desktop_backend()
    key = "wiki-draft:" + request_id
    # A browser restart may generate a fresh request UUID. Reuse delivery of
    # this exact draft instead of creating a second change proposal.
    for record in backend.outbox.records():
        if (
            record["path"] == "/api/knowledge/v1/changes"
            and record["payload"].get("metadata", {}).get("draft_id") == draft["draft_id"]
            and record["payload"].get("body") == draft["markdown"]
        ):
            key = record["idempotency_key"]
            break
    previous = backend.outbox.get(key)
    if previous is not None:
        old_payload = previous["payload"]
        if (
            old_payload.get("body") != draft["markdown"]
            or old_payload.get("metadata", {}).get("draft_id") != draft["draft_id"]
        ):
            raise BackendError("IDEMPOTENCY_CONFLICT", "同一提交请求不能替换草稿内容", False, 409)
        response = await backend.propose_wiki_change(old_payload, idempotency_key=key)
        return {
            "state": "submitted",
            "published": False,
            **response["data"],
            "task_id": response.get("task_id"),
        }
    outcome = await backend.submit_learning_facts(learning_facts(detail))
    original = await read_remote_original(draft["concept_id"])
    original_body = original["body"] if original else None
    actual_hash = (
        hashlib.sha256(original_body.encode()).hexdigest() if original_body is not None else None
    )
    if actual_hash != draft["original_hash"]:
        raise BackendError(
            "BASE_STALE", "远端条目已变化；本地草稿仍保留，请重新核对差异", False, 409
        )
    outcome_id = outcome["data"].get("outcome_id") or outcome["data"].get("learning_outcome_id")
    if not outcome_id:
        raise BackendError("INVALID_RESPONSE", "学习成果响应缺少身份")
    classification_context = {
        "original": original or {},
        "submitted": {"classification": legacy_input_classification()},
    }
    proposal = {
        "entry_id": original["entry_id"] if original else entry_id_for_concept(draft["concept_id"]),
        "base_revision": int(original["revision"]) if original else 0,
        "title": detail["card"].get("title") or detail["card"]["learning_goal"],
        "body": draft["markdown"],
        "entry_type": "knowledge",
        "visibility": original.get("visibility", "private") if original else "private",
        "evidence_refs": [{"learning_outcome_id": outcome_id}],
        "risk_flags": ["requires_ai_review"],
        "metadata": {
            **(original.get("metadata", {}) if original else {}),
            "evidence_classification": classify_record(classification_context),
            "classification_labels": sorted(classification_labels(classification_context)),
            "concept_id": draft["concept_id"],
            "card_id": draft["card_id"],
            "draft_id": draft["draft_id"],
            "content_kind": "knowledge_synthesis",
        },
    }
    response = await backend.propose_wiki_change(proposal, idempotency_key=key)
    return {
        "state": "submitted",
        "published": False,
        **response["data"],
        "task_id": response.get("task_id"),
    }
