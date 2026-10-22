"""Authenticated desktop read-only bridge to the authoritative relation projection."""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

from ahadiff.knowledge_platform.learning.backend import BackendError
from ahadiff.knowledge_platform.learning.desktop import desktop_backend

from .auth import require_write_token


async def wiki_relations(request: Any) -> JSONResponse:
    """Keep local and remote authentication; never substitute an offline graph."""
    require_write_token(request)
    try:
        traverse = request.url.path.endswith("/traverse")
        query = request.query_params
        bounds = {"max_depth", "max_nodes"} if traverse else {"limit"}
        if set(query) - {"kind", "direction", "visibility", *bounds} or any(
            len(query.getlist(key)) != 1 for key in query if key != "kind"
        ):
            raise ValueError("Unsupported relation query")
        options: dict[str, Any] = {
            "traverse": traverse,
            "kinds": query.getlist("kind") or None,
            "direction": query.get("direction", "both"),
            "visibility": query.get("visibility", "all"),
        }
        for key in bounds & set(query):
            if not query[key].isascii() or not query[key].isdigit():
                raise ValueError("Invalid relation bounds")
            options[key] = int(query[key])
        result = await desktop_backend().read_relations(request.path_params["entry_id"], **options)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except BackendError as exc:
        return JSONResponse(
            {"error": {
                "code": exc.code, "retryable": exc.retryable,
                "detail": "关系读取未完成，请按错误代码处理；未生成或修改关系。",
            }},
            status_code=exc.status_code or (503 if exc.retryable else 409),
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, TypeError):
        return JSONResponse(
            {"error": {"code": "INVALID_INPUT", "retryable": False,
                       "detail": "关系查询参数无效。"}}, status_code=422,
            headers={"Cache-Control": "no-store"},
        )
