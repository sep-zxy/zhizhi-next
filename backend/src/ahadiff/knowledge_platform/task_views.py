"""Browser/API task projections without worker leases, inputs or model output."""

from __future__ import annotations

import re
from typing import Any, cast

from .contracts import TASK_STATES
from .model.diagnostics import sanitized_failure
from .model.validation import sanitized_usage

_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,95}")
_MODEL_STATES = frozenset(
    {"succeeded", "waiting_auth", "waiting_quota", "blocked",
     "invalid_output", "failed", "cancelled"}
)


def _error_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if _ERROR_CODE.fullmatch(value) else "TASK_ERROR"


def _model_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    value = cast("dict[str, Any]", value)
    safe: dict[str, Any] = {}
    status = value.get("status")
    if isinstance(status, str) and status in _MODEL_STATES:
        safe["status"] = status
    for key in (
        "task_id", "provider_id", "model_id", "requested_model_id",
        "runtime_version", "retry_after",
    ):
        item = value.get(key)
        if item is None or (isinstance(item, str) and len(item) <= 160):
            safe[key] = item
    if type(value.get("fixture")) is bool:
        safe["fixture"] = value["fixture"]
    code = _error_code(value.get("error_code"))
    if code is not None:
        safe["error_code"] = code
    failure = sanitized_failure(value.get("failure"))
    if failure is not None:
        safe["failure"] = failure
    if isinstance(value.get("usage"), dict):
        safe["usage"] = sanitized_usage(value["usage"])
    return safe


def public_task_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project an already authorized task; projection never grants read permission."""
    keys = {
        "task_id", "kind", "state", "revision", "attempt", "max_attempts",
        "created_at", "updated_at", "next_run_at", "reconciliation_required",
        "trace_id", "data_classification", "classification_labels",
    }
    result = {key: value for key, value in record.items() if key in keys}
    error = record.get("error")
    result["error"] = None
    if isinstance(error, dict):
        error = cast("dict[str, Any]", error)
        code = _error_code(error.get("code"))
        if code is not None:
            result["error"] = {"code": code}
            if type(error.get("retryable")) is bool:
                result["error"]["retryable"] = error["retryable"]
    settled = record.get("result")
    result["result"] = None
    if isinstance(settled, dict):
        settled = cast("dict[str, Any]", settled)
        visible: dict[str, Any] = {
            key: settled[key]
            for key in (
                "entry_id", "revision", "change_id", "intake_id", "stage", "status"
            )
            if key in settled
            and isinstance(settled[key], str | int | bool | type(None))
        }
        model = _model_audit(settled.get("model"))
        if model:
            visible["model"] = model
        attempts = settled.get("model_attempts")
        if isinstance(attempts, list):
            visible["model_attempts"] = [
                _model_audit(item) for item in cast("list[Any]", attempts)[:16]
            ]
        result["result"] = visible
    return result


def public_task_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep task timeline facts, not arbitrary future event payloads or owner IDs."""
    keys = {
        "sequence", "event_id", "type", "version", "trace_id", "aggregate_id",
        "aggregate_revision", "occurred_at", "data_classification", "classification_labels",
    }
    result = {key: value for key, value in event.items() if key in keys}
    visible: dict[str, Any] = {}
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload = cast("dict[str, Any]", payload)
        state = payload.get("state")
        if isinstance(state, str) and state in TASK_STATES:
            visible["state"] = state
        attempt = payload.get("attempt")
        if type(attempt) is int and 0 <= attempt <= 10000:
            visible["attempt"] = attempt
        if type(payload.get("reclaimed")) is bool:
            visible["reclaimed"] = payload["reclaimed"]
        if payload.get("reason") == "quota_retry_due":
            visible["reason"] = "quota_retry_due"
    result["payload"] = visible
    return result
