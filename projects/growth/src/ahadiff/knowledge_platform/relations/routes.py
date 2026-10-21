"""Authenticated read-only relation HTTP views, never an anonymous graph endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..auth import request_actor
from ..contracts import new_id
from ..errors import DomainError, invalid

if TYPE_CHECKING:
    from starlette.requests import Request

    from .service import RelationService


def create_relation_routes(
    service: RelationService, *, prefix: str = "/api/knowledge/v1"
) -> list[Route]:
    """Attach behind DomainAuthenticationMiddleware; missing principals fail closed."""

    async def dispatch(request: Request, *, traverse: bool) -> JSONResponse:
        trace = new_id("trace")
        try:
            actor = request_actor(request)
            query = request.query_params
            options: dict[str, Any] = {
                "actor": actor,
                "kinds": query.getlist("kind") or None,
                "direction": query.get("direction", "both"),
                "visibility": query.get("visibility", "all"),
            }
            if traverse:
                action = service.traverse
                options.update(
                    max_depth=int(query.get("max_depth", "2")),
                    max_nodes=int(query.get("max_nodes", "100")),
                )
            else:
                action = service.neighbors
                options["limit"] = int(query.get("limit", "100"))
            result = await run_in_threadpool(action, request.path_params["entry_id"], **options)
            return JSONResponse(
                {
                    "data": result,
                    "task_id": None,
                    "trace_id": trace,
                    "data_classification": result["data_classification"],
                    "classification_labels": result["classification_labels"],
                }
            )
        except ValueError:
            error = invalid("Relation query bounds must be integers")
            return JSONResponse(error.as_dict(trace), status_code=error.status_code)
        except DomainError as error:
            return JSONResponse(error.as_dict(trace), status_code=error.status_code)

    async def neighbors(request: Request) -> JSONResponse:
        return await dispatch(request, traverse=False)

    async def traverse(request: Request) -> JSONResponse:
        return await dispatch(request, traverse=True)

    return [
        Route(prefix + "/relations/{entry_id}", neighbors, methods=["GET"]),
        Route(prefix + "/relations/{entry_id}/traverse", traverse, methods=["GET"]),
    ]
