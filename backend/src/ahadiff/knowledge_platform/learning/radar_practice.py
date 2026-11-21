"""Existing desktop learning entry for genuine, ungraded radar practice."""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse
from starlette.routing import Route

from ..auth import classification_labels, classify_record
from .backend import BackendError, outcome_key
from .desktop import desktop_backend, desktop_device_id
from .model_bridge import legacy_input_classification


def _record(suggestion_id: str) -> dict[str, Any] | None:
    return next(
        (
            record
            for record in reversed(desktop_backend().outbox.records())
            if record["payload"].get("metadata", {}).get("radar_suggestion_id") == suggestion_id
        ),
        None,
    )


async def read_practice(suggestion_id: str) -> dict[str, Any]:
    """Restore pending user text from the durable outbox, even while offline."""
    record = _record(suggestion_id)
    try:
        result = (await desktop_backend().read_radar(suggestion_id))["data"]
    except BackendError as error:
        if not error.retryable or record is None:
            raise
        metadata = record["payload"]["metadata"]
        result = {
            "suggestion_id": suggestion_id,
            "suggestion_state": "accepted",
            "practice": {
                "entry_id": record["payload"]["entry_id"],
                "title": metadata["title"],
                "question": metadata["practice_question"],
                "body": "",
                **({"source_reference": metadata["source_reference"]} if "source_reference" in metadata else {}),
            },
        }
    if record:
        result["user_text"] = record["payload"]["user_text"]
        if record["state"] != "acknowledged":
            result["state"] = "pending" if record["state"] == "pending" else "rejected"
            result["delivery_error"] = record.get("error", {}).get("code")
        else:
            result["state"] = "submitted"
            result["outcome_id"] = record["response"]["data"]["outcome_id"]
            result["task_id"] = record["response"].get("task_id")
    result["published"] = False
    return result


async def submit_practice(suggestion_id: str, user_text: str) -> dict[str, Any]:
    """Submit immutable user-authored facts; no grading or mastery is fabricated."""
    if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 20000:
        raise BackendError("INVALID_INPUT", "请填写自己的实际解释或实践原话", False, 422)
    backend = desktop_backend()
    record = _record(suggestion_id)
    if record:
        facts = record["payload"]
        if facts["user_text"] != user_text:
            raise BackendError(
                "IDEMPOTENCY_CONFLICT", "已提交的练习原话不可替换，请保留原记录", False, 409
            )
    else:
        practice = (await backend.read_radar(suggestion_id))["data"]
        if practice["suggestion_state"] != "accepted":
            raise BackendError("INVALID_STATE", "请先接受该学习建议", False, 409)
        target = practice["practice"]
        labels = {"practice": practice, "input_classification": legacy_input_classification()}
        facts = {
            "device_id": desktop_device_id(),
            "session_id": "radar:" + suggestion_id,
            "version": 1,
            "entry_id": target["entry_id"],
            "user_text": user_text,
            "result": {"practice_kind": "open_response", "graded": False},
            "metadata": {
                "radar_suggestion_id": suggestion_id,
                "title": target["title"],
                "practice_question": target["question"],
                **({"source_reference": target["source_reference"]} if "source_reference" in target else {}),
                "evidence_classification": classify_record(labels),
                "classification_labels": sorted(classification_labels(labels)),
            },
        }
        # This happens before any outbound write so a lost response is recoverable.
        backend.outbox.enqueue(outcome_key(facts), "/api/knowledge/v1/learning/outcomes", facts)
    try:
        response = await backend.submit_learning_outcome(facts)
    except BackendError as error:
        if not error.retryable:
            raise
        return {
            "state": "pending",
            "suggestion_id": suggestion_id,
            "published": False,
            "error": {"code": error.code, "retryable": True},
        }
    return {
        "state": "submitted",
        "suggestion_id": suggestion_id,
        "outcome_id": response["data"]["outcome_id"],
        "task_id": response.get("task_id"),
        "knowledge_state": "queued",
        "published": False,
    }


def create_desktop_learning_routes() -> list[Route]:
    """Mount under the existing local-token-protected knowledge API."""
    from ahadiff.serve.auth import require_write_token

    prefix = "/api/growth/local/knowledge/radar"

    async def endpoint(request: Any) -> JSONResponse:
        require_write_token(request)
        try:
            suggestion_id = request.path_params.get("suggestion_id")
            if request.method == "GET" and "plan_hash" in request.path_params:
                digest = request.path_params["plan_hash"]
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("Invalid plan identity")
                record = desktop_backend().outbox.get("learning-plan:" + digest)
                result = {"plan_hash": digest, "delivery_state": record["state"] if record else "not_submitted",
                          "receipt": record.get("response", {}).get("data") if record else None}
            elif request.method == "GET" and request.url.path == prefix + "/snoozed":
                result = (await desktop_backend().list_snoozed_radar(
                    before=request.query_params.get("before")
                ))["data"]
            elif request.method == "GET":
                result = await read_practice(suggestion_id)
            else:
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise ValueError("Expected object")
                if request.url.path.endswith("/plans/preview"):
                    result = (await desktop_backend().preview_learning_plan(payload))["data"]
                elif request.url.path.endswith("/plans/accept"):
                    result = (await desktop_backend().accept_learning_plan(payload))["data"]
                elif request.url.path.endswith("/runs"):
                    result = (
                        await desktop_backend().run_radar(limit=int(payload.get("limit", 3)))
                    )["data"]
                elif request.url.path.endswith("/decision"):
                    response = await desktop_backend().decide_radar(
                        suggestion_id, payload["decision"]
                    )
                    result = (
                        await read_practice(suggestion_id)
                        if payload["decision"] == "accept"
                        else response["data"]
                    )
                else:
                    result = await submit_practice(suggestion_id, payload["user_text"])
            return JSONResponse(
                result, status_code=202 if result.get("state") == "pending" else 200
            )
        except BackendError as error:
            return JSONResponse(
                {
                    "error": {
                        "code": error.code,
                        "detail": error.detail,
                        "retryable": error.retryable,
                    }
                },
                status_code=error.status_code or (503 if error.retryable else 409),
            )
        except (KeyError, TypeError, ValueError):
            return JSONResponse(
                {"error": {"code": "INVALID_INPUT", "detail": "Invalid practice request"}},
                status_code=422,
            )

    return [
        Route(prefix + "/plans/preview", endpoint, methods=["POST"]),
        Route(prefix + "/plans/{plan_hash}", endpoint, methods=["GET"]),
        Route(prefix + "/plans/accept", endpoint, methods=["POST"]),
        Route(prefix + "/runs", endpoint, methods=["POST"]),
        Route(prefix + "/snoozed", endpoint, methods=["GET"]),
        Route(prefix + "/{suggestion_id}/decision", endpoint, methods=["POST"]),
        Route(prefix + "/{suggestion_id}/complete", endpoint, methods=["POST"]),
        Route(prefix + "/{suggestion_id}", endpoint, methods=["GET"]),
    ]
