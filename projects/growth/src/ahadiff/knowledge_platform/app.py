"""Explicit assembly of the four implementation lanes into one ASGI service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import Actor, DomainAuthenticationMiddleware, TokenAuthenticator
from .core_routes import create_core_routes
from .domain import DomainService
from .errors import DomainError
from .intake import IntakeService, create_intake_routes
from .model import DisabledModelRunner
from .relations import RelationService, create_relation_routes
from .search import create_read_routes
from .workflow import KnowledgeWorkflow, WorkflowSettings

if TYPE_CHECKING:
    from collections.abc import Mapping


class TraceMiddleware:
    """Use server-generated trace identifiers instead of trusting inbound labels."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["trace_id"] = uuid4().hex
        await self.app(scope, receive, send)


class ExactCallbackAuthentication:
    """Exempt only installed, independently verified WeChat callback routes."""

    def __init__(
        self, app: Any, authenticator: TokenAuthenticator, callback_path: str | None = None
    ) -> None:
        self.app = app
        self.callback_path = callback_path
        self.authenticated = DomainAuthenticationMiddleware(app, authenticator)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if (
            self.callback_path is not None
            and scope["type"] == "http"
            and scope.get("path") == self.callback_path
            and scope.get("method") in {"GET", "POST"}
        ):
            # The callback adapter itself verifies platform signature, decryption
            # and sender binding. No other /channels route is exempt.
            await self.app(scope, receive, send)
            return
        await self.authenticated(scope, receive, send)


def _unique_routes(routes: list[Route]) -> None:
    claimed: set[tuple[str, str]] = set()
    for route in routes:
        for method in route.methods or {"GET"}:
            pair = (route.path, method)
            if pair in claimed:
                raise ValueError(f"Duplicate route registration: {method} {route.path}")
            claimed.add(pair)


def create_application(
    state_directory: str | Path,
    *,
    tokens: Mapping[str, Actor],
    model_runner: Any = None,
    workflow_settings: WorkflowSettings | None = None,
    start_worker: bool = False,
    wechat_adapter: Any = None,
    domain: DomainService | None = None,
    intake_service: Any = None,
    channel_supervisor: Any = None,
    media_processor: Any = None,
) -> Starlette:
    """Create a fail-closed application; no implicit login, token or publication.

    Tokens must come from a protected deployment secret source. Workers and real
    writes are separately enabled. Test factories may supply a fixture DomainService
    and model runner, but those settings cannot be activated through HTTP requests.
    """
    from .portable import validate_portable_runtime

    validate_portable_runtime(state_directory)
    root = Path(state_directory).expanduser().resolve()
    if start_worker and workflow_settings is None:
        raise ValueError("Worker operation requires an explicit owner and policy")
    service = domain or DomainService(
        root / "platform.sqlite3", root / "vault", object_path=root / "objects"
    )
    runner = model_runner or DisabledModelRunner()
    provider_ids = workflow_settings.allowed_provider_ids if workflow_settings else ()
    intake = intake_service or IntakeService(
        service,
        runner,
        allowed_provider_ids=provider_ids,
        input_classification=workflow_settings.input_classification
        if workflow_settings
        else "personal",
        media_processor=media_processor,
    )
    worker = (
        KnowledgeWorkflow(service, runner, workflow_settings, intake_service=intake)
        if workflow_settings
        else None
    )
    routes = (
        create_core_routes(service)
        + create_intake_routes(service, runner, intake_service=intake)
        + create_read_routes(service, runner)
        + create_relation_routes(RelationService(service))
    )
    callback_path = None
    if wechat_adapter is not None:
        from .channels.wechat_kf import create_wechat_kf_routes

        callback_routes = create_wechat_kf_routes(wechat_adapter)
        callback_path = "/api/knowledge/v1/channels/wechat-kf/callback"
        if any(route.path != callback_path for route in callback_routes):
            raise ValueError("Unexpected callback authentication exemption")
        routes.extend(callback_routes)

    async def health(request: Any) -> JSONResponse:
        background = getattr(request.app.state, "background_tasks", [])
        failed = any(task.done() and not task.cancelled() for task in background)
        return JSONResponse(
            {
                "status": "degraded" if failed else "ready",
                "service": "zhizhi-knowledge",
                "worker_enabled": start_worker,
                "channel_worker_enabled": channel_supervisor is not None,
                "channel_status": getattr(channel_supervisor, "status", "configured")
                if channel_supervisor is not None
                else "disabled",
                "automatic_commit_enabled": bool(
                    workflow_settings and workflow_settings.auto_commit_enabled
                ),
                "publication_enabled": False,
            },
            status_code=503 if failed else 200,
        )

    async def domain_error(request: Any, error: Exception) -> JSONResponse:
        if not isinstance(error, DomainError):
            raise error
        return JSONResponse(
            error.as_dict(getattr(request.state, "trace_id", None)), status_code=error.status_code
        )

    routes.append(Route("/health", health, methods=["GET"]))
    _unique_routes(routes)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        stop = asyncio.Event()
        background = (
            asyncio.create_task(worker.run_forever(stop)) if start_worker and worker else None
        )
        tasks = [background] if background is not None else []
        if channel_supervisor is not None:
            tasks.append(asyncio.create_task(channel_supervisor.run_forever(stop)))
        app.state.background_tasks = tasks
        try:
            yield
        finally:
            stop.set()
            for background in tasks:
                try:
                    await asyncio.wait_for(asyncio.shield(background), timeout=5)
                except TimeoutError:
                    background.cancel()
                    with suppress(asyncio.CancelledError):
                        await background
            close = getattr(service, "close", None)
            if close is not None:
                close()

    app = Starlette(
        routes=routes,
        exception_handlers={DomainError: domain_error},
        middleware=[
            Middleware(TraceMiddleware),
            Middleware(
                ExactCallbackAuthentication,
                authenticator=TokenAuthenticator(tokens),
                callback_path=callback_path,
            ),
        ],
        lifespan=lifespan,
    )
    app.state.domain = service
    app.state.workflow = worker
    app.state.intake_service = intake
    app.state.channel_supervisor = channel_supervisor
    return app
