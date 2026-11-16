"""Authenticated read, learning-outcome and radar HTTP adapters."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..radar.service import RadarService
from .index import SearchService

if TYPE_CHECKING:
    from starlette.requests import Request


def create_read_routes(domain: Any, model_runner: Any = None) -> list[Route]:
    """Expose D-owned routes; canonical entry routes remain owned by the core."""
    from ahadiff.knowledge_platform.auth import (
        classify_record,
        enforce_read_classification,
        request_actor,
    )
    from ahadiff.knowledge_platform.errors import DomainError

    search = SearchService(domain)
    radar = RadarService(domain)

    async def body(request: Request) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise ValueError("Request body is too large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return value

    async def dispatch(request: Request, handler: Any) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or str(uuid4())
        try:
            actor = request_actor(request)
            result = await handler(request, actor, trace_id)
            enforce_read_classification(actor, result)
            return JSONResponse(
                {
                    "data": result,
                    "task_id": result.get("task_id") if isinstance(result, dict) else None,
                    "trace_id": trace_id,
                    "data_classification": classify_record(result),
                }
            )
        except DomainError as error:
            return JSONResponse(error.as_dict(trace_id), status_code=error.status_code)
        except (TypeError, ValueError, KeyError):
            return JSONResponse(
                {
                    "error": {
                        "code": "INVALID_INPUT",
                        "retryable": False,
                        "detail": "Invalid request",
                    },
                    "trace_id": trace_id,
                },
                status_code=422,
            )

    async def search_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        results = await run_in_threadpool(
            search.search,
            request.query_params.get("q", ""),
            actor=actor,
            scope=request.query_params.get("scope", "wiki"),
            limit=int(request.query_params.get("limit", "10")),
        )
        # An empty response contains no user data. Never use this default for
        # nonempty, unclassified records, which remain fail-closed.
        return {"results": results, **({"data_classification": "public"} if not results else {})}

    async def growth_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        events = await run_in_threadpool(
            domain.list_events,
            actor=actor,
            after=int(request.query_params.get("after", "0")),
            types=["knowledge.committed.v1"],
            limit=100,
        )
        return {"events": events, **({"data_classification": "public"} if not events else {})}

    async def outcome_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        payload = await body(request)
        key = request.headers.get("Idempotency-Key", "")
        if not key.strip() or len(key) > 256 or not isinstance(payload.get("metadata", {}), dict):
            raise ValueError("Invalid outcome request identity or metadata")
        if payload.get("metadata", {}).get("radar_suggestion_id"):
            payload = await run_in_threadpool(radar.validate_submission, payload, actor=actor)
        outcome = await run_in_threadpool(
            domain.submit_learning_outcome,
            payload,
            actor=actor,
            idempotency_key=key,
            trace_id=trace_id,
        )
        outcome_id = outcome.get("outcome_id") or outcome.get("learning_outcome_id")
        task_payload: dict[str, Any] = {"evidence_refs": [{"learning_outcome_id": outcome_id}]}
        if payload.get("entry_id"):
            task_payload["entry_id"] = payload["entry_id"]
        elif payload.get("metadata", {}).get("suggested_entry_id"):
            task_payload["suggested_entry_id"] = payload["metadata"]["suggested_entry_id"]
        task = await run_in_threadpool(
            domain.create_task,
            "knowledge.generate",
            task_payload,
            actor=actor,
            idempotency_key="learning-generation:" + str(outcome_id),
            trace_id=trace_id,
        )
        suggestion_id = payload.get("metadata", {}).get("radar_suggestion_id")
        if suggestion_id:
            await run_in_threadpool(radar.complete, suggestion_id, str(outcome_id), actor=actor)
        return {**outcome, "task_id": task["task_id"], "knowledge_state": "queued"}

    async def radar_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        payload = await body(request)
        suggestions = await run_in_threadpool(
            radar.run, actor=actor, limit=int(payload.get("limit", 3))
        )
        return {
            "suggestions": suggestions,
            **({"data_classification": "public"} if not suggestions else {}),
        }

    async def decision_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        payload = await body(request)
        return await run_in_threadpool(
            radar.decide, request.path_params["suggestion_id"], payload["decision"], actor=actor
        )

    async def radar_read_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        return await run_in_threadpool(
            radar.read, request.path_params["suggestion_id"], actor=actor
        )

    async def plan_handler(request: Request, actor: Any, trace_id: str) -> dict[str, Any]:
        from ..learning.plan import accept_plan, preview_plan

        function = accept_plan if request.url.path.endswith("/accept") else preview_plan
        return await run_in_threadpool(function, domain, await body(request), actor=actor)

    def endpoint(handler: Any) -> Any:
        async def wrapped(request: Request) -> JSONResponse:
            return await dispatch(request, handler)

        return wrapped

    return [
        Route("/api/knowledge/v1/radar/plans/preview", endpoint(plan_handler), methods=["POST"]),
        Route("/api/knowledge/v1/radar/plans/accept", endpoint(plan_handler), methods=["POST"]),
        Route("/api/knowledge/v1/search", endpoint(search_handler), methods=["GET"]),
        Route("/api/knowledge/v1/growth", endpoint(growth_handler), methods=["GET"]),
        Route("/api/knowledge/v1/learning/outcomes", endpoint(outcome_handler), methods=["POST"]),
        Route("/api/knowledge/v1/radar/runs", endpoint(radar_handler), methods=["POST"]),
        Route(
            "/api/knowledge/v1/radar/{suggestion_id}", endpoint(radar_read_handler), methods=["GET"]
        ),
        Route(
            "/api/knowledge/v1/radar/{suggestion_id}/decision",
            endpoint(decision_handler),
            methods=["POST"],
        ),
    ]
