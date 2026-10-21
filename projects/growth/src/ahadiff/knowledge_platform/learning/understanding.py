"""User-owned open responses bridged to the existing outcome/review/Writer pipeline.

Saving a note is not submission. This module snapshots explicitly submitted notes,
never completes exercises, and never writes a draft or formal Wiki file itself.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from typing import Any

from ..task_views import public_task_record
from .backend import BackendError, RemoteKnowledgeBackend
from .desktop import desktop_backend, desktop_device_id, entry_id_for_concept
from .model_bridge import legacy_input_classification

_KIND = "open_understanding"
_OUTCOME_PATH = "/api/knowledge/v1/learning/outcomes"
_HASH = re.compile(r"[a-f0-9]{64}")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,95}")


def note_hash(text: str) -> str:
    """Hash exact UTF-8 words, including whitespace, without normalization."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _submission_id(record: dict[str, Any]) -> str:
    return note_hash(record["idempotency_key"])


def _error(code: Any, retryable: bool = False) -> dict[str, Any]:
    return {
        "code": code if isinstance(code, str) and _CODE.fullmatch(code) else "REMOTE_ERROR",
        "retryable": retryable,
    }


def _records(backend: RemoteKnowledgeBackend, card_id: str) -> list[dict[str, Any]]:
    device_id = desktop_device_id()
    result = []
    for record in backend.outbox.records():
        payload = record.get("payload", {})
        metadata = payload.get("metadata", {})
        if (
            record.get("path") == _OUTCOME_PATH
            and payload.get("device_id") == device_id
            and metadata.get("submission_kind") == _KIND
            and metadata.get("card_id") == card_id
        ):
            # A corrupt local record must not become new evidence on replay.
            if (
                not isinstance(payload.get("user_text"), str)
                or note_hash(payload["user_text"]) != metadata.get("note_sha256")
                or payload.get("session_id")
                != f"understanding:{card_id}:{metadata.get('card_version')}"
            ):
                raise BackendError("SUBMISSION_CORRUPT", "提交记录校验失败", False, 409)
            result.append(record)
    return sorted(
        result,
        key=lambda item: (item["payload"]["metadata"]["card_version"], item["payload"]["version"]),
        reverse=True,
    )


async def _candidate(
    backend: RemoteKnowledgeBackend, change_id: str, outcome_id: str
) -> dict[str, Any]:
    change = (await backend.read_change(change_id))["data"]
    refs = change.get("evidence_refs", [])
    if (
        change.get("change_id") != change_id
        or not isinstance(refs, list)
        or not any(
            isinstance(ref, dict) and ref.get("learning_outcome_id") == outcome_id for ref in refs
        )
        or type(change.get("base_revision")) is not int
        or change["base_revision"] < 0
        or not isinstance(change.get("body"), str)
    ):
        raise BackendError("INVALID_RESPONSE", "候选未绑定本次真实成果", False, 502)
    original, path = "", None
    if change["base_revision"]:
        base = (await backend.read_entry_revision(change["entry_id"], change["base_revision"]))[
            "data"
        ]
        if (
            base.get("entry_id") != change["entry_id"]
            or base.get("revision") != change["base_revision"]
            or not isinstance(base.get("body"), str)
        ):
            raise BackendError("INVALID_RESPONSE", "候选基础版本无法核验", False, 502)
        original, path = base["body"], base.get("path")
    committed = change.get("committed_revision")
    if (
        change.get("state") not in {"committed", "indexed"}
        or type(committed) is not int
        or committed < 1
    ):
        committed = None
    return {
        "change_id": change_id,
        "state": change.get("state"),
        "entry_id": change.get("entry_id"),
        "base_revision": change["base_revision"],
        "committed_revision": committed,
        "title": change.get("title"),
        "path": path if isinstance(path, str) else None,
        "original_markdown": original,
        "candidate_markdown": change["body"],
        "evidence_refs": [
            {
                key: value
                for key, value in ref.items()
                if key in {"source_id", "learning_outcome_id", "external_document_id", "revision"}
                and isinstance(value, str | int)
                and not isinstance(value, bool)
            }
            for ref in refs
            if isinstance(ref, dict)
        ],
    }


async def _view(backend: RemoteKnowledgeBackend, record: dict[str, Any]) -> dict[str, Any]:
    payload = record["payload"]
    metadata = payload["metadata"]
    failure = record.get("error") or {}
    view: dict[str, Any] = {
        "submission_id": _submission_id(record),
        "card_id": metadata["card_id"],
        "card_version": metadata["card_version"],
        "note_sha256": metadata["note_sha256"],
        "user_text": payload["user_text"],
        "delivery_state": record["state"],
        "outcome_id": None,
        "task_id": None,
        "remote_state": "unknown" if record.get("attempts", 0) else "not_submitted",
        "task": None,
        "change": None,
        "error": _error(failure.get("code"), record["state"] == "pending") if failure else None,
    }
    if record["state"] != "acknowledged":
        return view
    response = record.get("response", {})
    data = response.get("data", {})
    outcome_id = data.get("outcome_id") or data.get("learning_outcome_id")
    task_id = response.get("task_id") or data.get("task_id")
    view.update(outcome_id=outcome_id, task_id=task_id, remote_state="unknown", error=None)
    try:
        if not isinstance(outcome_id, str) or not isinstance(task_id, str):
            raise BackendError("INVALID_RESPONSE", "领域回执缺少成果或任务身份", False, 502)
        task = (await backend.read_task(task_id))["data"]
        if task.get("task_id") != task_id or task.get("kind") != "knowledge.generate":
            raise BackendError("INVALID_RESPONSE", "任务身份不匹配", False, 502)
        view["task"] = public_task_record(task)
        result = task.get("result")
        change_id = result.get("change_id") if isinstance(result, dict) else None
        if change_id:
            if not isinstance(change_id, str):
                raise BackendError("INVALID_RESPONSE", "候选身份无效", False, 502)
            view["change"] = await _candidate(backend, change_id, outcome_id)
        view["remote_state"] = "available"
    except BackendError as error:
        view["error"] = _error(error.code, error.retryable)
    return view


async def submit_understanding(
    detail: dict[str, Any], *, card_version: int, note_sha256: str
) -> dict[str, Any]:
    """Submit an exact saved snapshot, or replay its immutable pending delivery."""
    backend = desktop_backend()  # No local fallback and no implicit identity setup.
    card = detail["card"]
    if type(card_version) is not int or card_version < 1 or not _HASH.fullmatch(note_sha256):
        raise BackendError("INVALID_INPUT", "请提供有效版本与原话摘要", False, 422)
    record = next(
        (
            item
            for item in _records(backend, card["card_id"])
            if item["payload"]["metadata"]["card_version"] == card_version
            and item["payload"]["metadata"]["note_sha256"] == note_sha256
        ),
        None,
    )
    if record is None:
        text = detail["learning"].get("note_text") or ""
        if card["card_version"] != card_version or note_hash(text) != note_sha256:
            raise BackendError(
                "UNDERSTANDING_STALE", "原话或卡片版本已变化，请重新核对", False, 409
            )
        if not text.strip() or len(text) > 20_000:
            raise BackendError("INVALID_INPUT", "请先保存自己的真实理解", False, 422)
        classification = legacy_input_classification()
        if classification not in {
            "public",
            "personal",
            "third_party_private",
            "employer",
            "secret",
        }:
            raise BackendError("WAITING_CONFIGURATION", "缺少可信材料分类配置", True, 503)
        facts = {
            "device_id": desktop_device_id(),
            "session_id": f"understanding:{card['card_id']}:{card_version}",
            "entry_id": None,
            "user_text": text,
            "result": {"practice_kind": "open_response", "graded": False},
            "metadata": {
                "submission_kind": _KIND,
                "card_id": card["card_id"],
                "card_version": card_version,
                "concept_id": card["concept_id"],
                "suggested_entry_id": entry_id_for_concept(card["concept_id"]),
                "title": card.get("title") or card.get("learning_goal") or "本人理解",
                "note_sha256": note_sha256,
                "evidence_classification": "personal"
                if classification == "public"
                else classification,
            },
        }
        record = backend.outbox.enqueue_facts(facts)
    # Delivery has already persisted pending/rejected; never expose raw details.
    with suppress(BackendError):
        await backend.submit_learning_outcome(record["payload"])
    current = backend.outbox.get(record["idempotency_key"])
    if current is None:
        raise BackendError("SUBMISSION_CORRUPT", "提交记录不可恢复", False, 409)
    return await _view(backend, current)


async def read_submissions(card_id: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """Restore immutable local facts and read remote status, without delivering writes."""
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise BackendError("INVALID_INPUT", "分页参数无效", False, 422)
    backend = desktop_backend()
    records = _records(backend, card_id)
    return {
        "items": [await _view(backend, record) for record in records[offset : offset + limit]],
        "has_more": len(records) > offset + limit,
    }


async def read_submission(card_id: str, submission_id: str) -> dict[str, Any]:
    """Read one card-bound submission; other cards' records remain inaccessible."""
    backend = desktop_backend()
    if not _HASH.fullmatch(submission_id):
        raise BackendError("INVALID_INPUT", "提交身份无效", False, 422)
    record = next(
        (item for item in _records(backend, card_id) if _submission_id(item) == submission_id), None
    )
    if record is None:
        raise BackendError("NOT_FOUND", "未找到该卡片的提交记录", False, 404)
    return await _view(backend, record)
