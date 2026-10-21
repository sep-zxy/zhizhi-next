"""Authenticated HTTP intake endpoints with JSON envelopes and bounded bodies."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..auth import Actor
from ..errors import DomainError, invalid
from .contracts import IntakeError
from .service import IntakeService

if TYPE_CHECKING:
    from starlette.requests import Request

    from ..domain import DomainService
    from ..model import ModelRunner

PREFIX = "/api/knowledge/v1/intakes"


def _actor(request: Request) -> Actor:
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, Actor):
        raise IntakeError("UNAUTHENTICATED", "请先认证。", status_code=401)
    return actor


async def _body(request: Request) -> dict[str, Any]:
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > 512 * 1024:
            raise IntakeError("BODY_TOO_LARGE", "收件请求超过大小限制。", status_code=413)
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IntakeError("INVALID_JSON", "请求必须是有效的 JSON 对象。", status_code=422) from exc
    if not isinstance(payload, dict):
        raise IntakeError("INVALID_JSON", "请求必须是 JSON 对象。", status_code=422)
    return payload


def _key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "")
    if not value or len(value) > 256:
        raise IntakeError(
            "IDEMPOTENCY_KEY_REQUIRED", "写操作需要 Idempotency-Key。", status_code=422
        )
    return value


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    # The authenticated owner may read their source text, but API responses never
    # expose server object/cache paths or unrelated internal metadata.
    keys = (
        "intake_id",
        "state",
        "channel",
        "url",
        "title",
        "preview_revision",
        "parse_status",
        "data_classification",
        "classification_labels",
        "source_id",
        "task_id",
        "expires_at",
        "created_at",
        "updated_at",
    )
    result = {key: record[key] for key in keys if key in record}
    preview = record.get("preview")
    if isinstance(preview, dict):
        result["preview"] = {key: value for key, value in preview.items() if key != "cache_ref"}
    return result


def create_intake_routes(
    domain: DomainService,
    model_runner: ModelRunner | None = None,
    *,
    intake_service: IntakeService | None = None,
) -> list[Route]:
    """Create routes; a supervisor consumes persisted intake.preview tasks separately."""
    service = intake_service or IntakeService(domain, model_runner)
    if service.domain is not domain:
        raise ValueError("HTTP and worker intake services must share the same domain")

    async def listing(request: Request) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4().hex
        try:
            actor = _actor(request)
            if set(request.query_params) - {"limit", "offset", "state"}:
                raise invalid("Unsupported intake list parameter")
            page = await run_in_threadpool(
                domain.list_intakes,
                actor=actor,
                state=request.query_params.get("state"),
                limit=int(request.query_params.get("limit", "100")),
                offset=int(request.query_params.get("offset", "0")),
            )
            return JSONResponse(
                {
                    "data": {
                        "items": [_public_record(record) for record in page["items"]],
                        "has_more": page["has_more"],
                    },
                    "task_id": None,
                    "trace_id": trace_id,
                }
            )
        except DomainError as exc:
            return JSONResponse(exc.as_dict(trace_id), status_code=exc.status_code)
        except IntakeError as exc:
            return JSONResponse(
                {
                    "error": {"code": exc.code, "detail": exc.detail, "retryable": exc.retryable},
                    "trace_id": trace_id,
                },
                status_code=exc.status_code,
            )
        except (ValueError, TypeError):
            error = invalid("Intake pagination must be valid integers")
            return JSONResponse(error.as_dict(trace_id), status_code=422)

    async def endpoint(request: Request) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", None) or uuid.uuid4().hex
        try:
            actor = _actor(request)
            intake_id = request.path_params.get("intake_id")
            if request.method == "GET":
                if intake_id is None:
                    data: Any = {"capabilities": service.capabilities()}
                else:
                    record = domain.read_intake(intake_id, actor=actor)
                    if service._expired(record) and not record.get("source_id"):
                        record = service.expire(intake_id, actor=actor)
                    data = _public_record(record)
                    data["publication"] = service.publication_status(intake_id, actor=actor)
                return JSONResponse(
                    {"data": data, "task_id": data.get("task_id"), "trace_id": trace_id}
                )
            payload = await _body(request)
            key = _key(request)
            if intake_id is None:
                allowed = {
                    "text",
                    "url",
                    "attachment_refs",
                    "requested_action",
                    "channel",
                    "external_message_id",
                    "data_classification",
                }
                if set(payload) - allowed:
                    raise IntakeError(
                        "INVALID_INPUT", "收件请求包含不支持的字段。", status_code=422
                    )
                if payload.get("channel", "http") != "http":
                    raise IntakeError(
                        "FORBIDDEN", "HTTP 请求不能冒充外部消息渠道。", status_code=403
                    )
                external_id = payload.get("external_message_id")
                if external_id is not None and (
                    not isinstance(external_id, str) or len(external_id) > 256
                ):
                    raise IntakeError("INVALID_INPUT", "消息标识格式无效。", status_code=422)
                data = service.receive(
                    payload,
                    actor=actor,
                    idempotency_key=key,
                    channel="http",
                    external_message_id=external_id,
                    trace_id=trace_id,
                )
                return JSONResponse(
                    {"data": data, "task_id": data["task_id"], "trace_id": trace_id},
                    status_code=202,
                )
            if set(payload) - {"decision", "preview_revision"}:
                raise IntakeError("INVALID_INPUT", "决定请求包含不支持的字段。", status_code=422)
            if payload.get("decision") == "keep":
                revision = payload.get("preview_revision")
                if type(revision) is not int or revision < 1:
                    raise IntakeError(
                        "PREVIEW_REVISION_REQUIRED",
                        "收藏必须指定已查看的预览版本。",
                        status_code=409,
                    )
                source = service.keep(
                    intake_id,
                    preview_revision=revision,
                    actor=actor,
                    idempotency_key=key,
                    trace_id=trace_id,
                )
                data = {
                    key: source[key]
                    for key in ("source_id", "intake_id", "task_id", "visibility", "content_hash")
                    if key in source
                }
                data["state"] = "retained"
                data["publication"] = service.publication_status(intake_id, actor=actor)
            elif payload.get("decision") == "discard":
                data = _public_record(
                    service.discard(
                        intake_id,
                        actor=actor,
                        idempotency_key=key,
                        preview_revision=payload.get("preview_revision"),
                        trace_id=trace_id,
                    )
                )
            else:
                raise IntakeError(
                    "INVALID_ACTION", "decision 必须为 keep 或 discard。", status_code=422
                )
            return JSONResponse(
                {"data": data, "task_id": data.get("task_id"), "trace_id": trace_id}
            )
        except DomainError as exc:
            return JSONResponse(exc.as_dict(trace_id), status_code=exc.status_code)
        except IntakeError as exc:
            return JSONResponse(
                {
                    "error": {"code": exc.code, "detail": exc.detail, "retryable": exc.retryable},
                    "trace_id": trace_id,
                },
                status_code=exc.status_code,
            )

    return [
        Route(PREFIX, listing, methods=["GET"]),
        Route(PREFIX, endpoint, methods=["POST"]),
        Route(PREFIX + "/capabilities", endpoint, methods=["GET"]),
        Route(PREFIX + "/{intake_id}", endpoint, methods=["GET"]),
        Route(PREFIX + "/{intake_id}/decision", endpoint, methods=["POST"]),
    ]
