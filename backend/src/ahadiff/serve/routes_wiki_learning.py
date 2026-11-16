"""Versioned free learning API, separate from engineering opportunities and exercise rules."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse

from ahadiff.knowledge_platform.learning.backend import BackendError
from ahadiff.knowledge_platform.learning.wiki_learning import (
    create_learning_context,
    list_learning_contexts,
    read_learning_context,
    read_learning_submissions,
    save_learning_note,
    submit_learning_understanding,
)

from .auth import require_write_token, serve_state


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Create(_Input):
    request_id: UUID
    entry_id: str = Field(strict=True, min_length=1, max_length=128)
    revision: int = Field(strict=True, ge=1, le=2**53 - 1)
    expected_content_hash: str = Field(strict=True, min_length=64, max_length=64)


class _Note(_Input):
    request_id: UUID
    expected_context_version: int = Field(strict=True, ge=1, le=2**53 - 1)
    note_text: str = Field(strict=True, max_length=20_000)


class _Understanding(_Input):
    context_version: int = Field(strict=True, ge=1, le=2**53 - 1)
    note_sha256: str = Field(strict=True, min_length=64, max_length=64)


async def _body(request: Any, model: type[_Input]) -> dict[str, Any]:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 100_000:
            raise BackendError("REQUEST_TOO_LARGE", "请求正文过大", False, 413)
    return model.model_validate(json.loads(data)).model_dump(mode="json")


def _page(request: Any) -> dict[str, int]:
    query = request.query_params
    if set(query) - {"limit", "offset"} or len(query.multi_items()) != len(query):
        raise ValueError("Unexpected pagination")
    for value in query.values():
        if not value.isascii() or not value.isdigit():
            raise ValueError("Invalid pagination")
    return {"limit": int(query.get("limit", "20")), "offset": int(query.get("offset", "0"))}


async def wiki_entry_learning(request: Any) -> JSONResponse:
    require_write_token(request)
    path = serve_state(request).state_dir / "growth.sqlite"
    context_id = str(request.path_params.get("context_id", ""))
    try:
        if "understanding/submissions" in request.url.path:
            if request.method == "POST":
                if request.query_params:
                    raise ValueError("Unexpected query")
                body = await _body(request, _Understanding)
                result = await submit_learning_understanding(path, context_id, **body)
                return JSONResponse(result, status_code=202)
            submission_id = request.path_params.get("submission_id")
            result = await read_learning_submissions(
                path, context_id, submission_id=submission_id, **_page(request)
            )
        elif request.method == "POST":
            if request.query_params:
                raise ValueError("Unexpected query")
            if context_id:
                result = await save_learning_note(path, context_id, await _body(request, _Note))
            else:
                result = await create_learning_context(path, await _body(request, _Create))
        elif context_id:
            if request.query_params:
                raise ValueError("Unexpected query")
            result = await read_learning_context(path, context_id)
        else:
            result = await list_learning_contexts(path, **_page(request))
        return JSONResponse(result)
    except BackendError as exc:
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "retryable": exc.retryable,
                    "detail": "正式版本学习或本人解释操作未完成，请核对状态。",
                }
            },
            status_code=exc.status_code or (503 if exc.retryable else 409),
        )
    except (ValueError, ValidationError, TypeError, sqlite3.IntegrityError):
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_INPUT",
                    "retryable": False,
                    "detail": "学习上下文请求参数无效。",
                }
            },
            status_code=422,
        )
