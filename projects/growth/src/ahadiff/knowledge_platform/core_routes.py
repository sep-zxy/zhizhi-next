"""Versioned core HTTP adapter; authentication never comes from request JSON."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import classification_labels, classify_record, enforce_read_classification, request_actor
from .contracts import new_id
from .errors import DomainError, invalid
from .task_views import public_task_event, public_task_record

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

    from .domain import DomainService


def create_core_routes(domain: DomainService) -> list[Route]:
    """Return task/source/change/review routes, leaving intake/read views to adapters."""
    prefix = "/api/knowledge/v1"

    def endpoint(handler: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(request: Request) -> JSONResponse:
            trace = getattr(request.state, "trace_id", None) or new_id("trace")
            try:
                actor = request_actor(request)
                data = await handler(request, actor, trace)
                # An authorized empty page contains no private record. A public-
                # only reader must not receive a fabricated classification error
                # merely because there are no labels left after filtering.
                empty_page = data == [] or (
                    isinstance(data, dict) and data == {"items": [], "has_more": False}
                )
                default = "public" if empty_page else "personal"
                enforce_read_classification(actor, data, default=default)
                task_id = data.get("task_id") if isinstance(data, dict) else None
                return JSONResponse(
                    {
                        "data": data,
                        "task_id": task_id,
                        "trace_id": trace,
                        "data_classification": classify_record(data, default=default),
                        "classification_labels": sorted(
                            classification_labels(data, default=default)
                        ),
                    }
                )
            except DomainError as exc:
                return JSONResponse(exc.as_dict(trace), status_code=exc.status_code)
            except (ValueError, TypeError, json.JSONDecodeError):
                error = invalid("Request parameters are invalid")
                return JSONResponse(error.as_dict(trace), status_code=422)

        return wrapped

    async def body(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise invalid("Request body is too large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise invalid("Request body must be an object")
        return value

    def key(request: Request) -> str:
        value = request.headers.get("Idempotency-Key", "")
        domain._validate_key(value)
        return value

    async def read_task(request: Request, actor: Any, trace: str) -> Any:
        return public_task_record(
            await run_in_threadpool(
                domain.read_task, request.path_params["task_id"], actor=actor
            )
        )

    async def list_tasks(request: Request, actor: Any, trace: str) -> Any:
        if set(request.query_params) - {"limit", "offset", "state"}:
            raise invalid("Unsupported task list parameter")
        page = await run_in_threadpool(
            domain.list_tasks,
            actor=actor,
            state=request.query_params.get("state"),
            limit=int(request.query_params.get("limit", "100")),
            offset=int(request.query_params.get("offset", "0")),
        )
        return {
            "items": [public_task_record(record) for record in page["items"]],
            "has_more": page["has_more"],
        }

    async def task_events(request: Request, actor: Any, trace: str) -> Any:
        events = await run_in_threadpool(
            domain.task_events,
            request.path_params["task_id"],
            actor=actor,
            after=int(request.query_params.get("after", "0")),
            limit=int(request.query_params.get("limit", "100")),
        )
        return [public_task_event(event) for event in events]

    async def resume_task(request: Request, actor: Any, trace: str) -> Any:
        payload = await body(request)
        task = await run_in_threadpool(
            domain.read_task, request.path_params["task_id"], actor=actor
        )
        if task["state"] not in {
            "waiting_auth",
            "waiting_quota",
            "waiting_user",
            "waiting_review",
            "waiting_input",
            "blocked",
            "partial",
            "retry_wait",
        }:
            raise DomainError(
                "INVALID_STATE", "Only a waiting task may be resumed", status_code=409
            )
        return public_task_record(
            await run_in_threadpool(
                domain.update_task,
                task["task_id"],
                {
                    "state": "queued",
                    "expected_revision": payload.get("expected_revision", task["revision"]),
                },
                actor=actor,
                idempotency_key=key(request),
                trace_id=trace,
            )
        )

    async def source(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.get_source, request.path_params["source_id"], actor=actor
        )

    async def evidence(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(domain.read_evidence, await body(request), actor=actor)

    async def create_change(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.propose_change,
            await body(request),
            actor=actor,
            idempotency_key=key(request),
            trace_id=trace,
        )

    async def read_change(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.get_change, request.path_params["change_id"], actor=actor
        )

    async def review_change(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.record_review,
            request.path_params["change_id"],
            await body(request),
            actor=actor,
            idempotency_key=key(request),
            trace_id=trace,
        )

    async def challenge(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.create_decision_challenge, request.path_params["change_id"], actor=actor
        )

    async def user_decision(request: Request, actor: Any, trace: str) -> Any:
        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN",
                "This endpoint requires the authenticated user's decision",
                status_code=403,
            )
        return await review_change(request, actor, trace)

    async def apply_change(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.apply_change,
            request.path_params["change_id"],
            actor=actor,
            idempotency_key=key(request),
            trace_id=trace,
        )

    async def reviews(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.list_reviews,
            actor=actor,
            state=request.query_params.get("state", "needs_user"),
            limit=int(request.query_params.get("limit", "100")),
        )

    async def events(request: Request, actor: Any, trace: str) -> Any:
        types = request.query_params.getlist("type") or None
        return await run_in_threadpool(
            domain.list_events,
            actor=actor,
            types=types,
            after=int(request.query_params.get("after", "0")),
            limit=int(request.query_params.get("limit", "100")),
        )

    async def entry(request: Request, actor: Any, trace: str) -> Any:
        revision = request.query_params.get("revision")
        return await run_in_threadpool(
            domain.get_entry,
            request.path_params["entry_id"],
            actor=actor,
            revision=int(revision) if revision else None,
        )

    async def entries(request: Request, actor: Any, trace: str) -> Any:
        return await run_in_threadpool(
            domain.list_entries,
            actor=actor,
            limit=int(request.query_params.get("limit", "100")),
            offset=int(request.query_params.get("offset", "0")),
        )

    return [
        Route(prefix + "/tasks", endpoint(list_tasks), methods=["GET"]),
        Route(prefix + "/tasks/{task_id}", endpoint(read_task), methods=["GET"]),
        Route(prefix + "/tasks/{task_id}/events", endpoint(task_events), methods=["GET"]),
        Route(prefix + "/tasks/{task_id}/resume", endpoint(resume_task), methods=["POST"]),
        Route(prefix + "/sources/{source_id}", endpoint(source), methods=["GET"]),
        Route(prefix + "/evidence/read", endpoint(evidence), methods=["POST"]),
        Route(prefix + "/changes", endpoint(create_change), methods=["POST"]),
        Route(prefix + "/changes/{change_id}", endpoint(read_change), methods=["GET"]),
        Route(prefix + "/changes/{change_id}/reviews", endpoint(review_change), methods=["POST"]),
        Route(
            prefix + "/changes/{change_id}/decision-challenge", endpoint(challenge), methods=["GET"]
        ),
        Route(prefix + "/changes/{change_id}/decisions", endpoint(user_decision), methods=["POST"]),
        Route(prefix + "/changes/{change_id}/apply", endpoint(apply_change), methods=["POST"]),
        Route(prefix + "/reviews", endpoint(reviews), methods=["GET"]),
        Route(prefix + "/events", endpoint(events), methods=["GET"]),
        Route(prefix + "/entries", endpoint(entries), methods=["GET"]),
        Route(prefix + "/entries/{entry_id}", endpoint(entry), methods=["GET"]),
    ]
