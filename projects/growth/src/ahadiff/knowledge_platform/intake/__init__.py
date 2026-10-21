"""Unified intake service. Import HTTP routes lazily to keep pure parsers isolated."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.routing import Route

    from ..domain import DomainService
    from ..model import ModelRunner
    from .service import IntakeService


def create_intake_routes(
    domain: DomainService,
    model_runner: ModelRunner | None = None,
    *,
    intake_service: IntakeService | None = None,
    allow_text_attachments: bool = True,
) -> list[Route]:
    """Register authenticated intake, preview query and explicit keep/discard endpoints."""
    from .routes import create_intake_routes as create

    return create(
        domain,
        model_runner,
        intake_service=intake_service,
        allow_text_attachments=allow_text_attachments,
    )


def __getattr__(name: str) -> object:
    if name == "IntakeService":
        from .service import IntakeService

        return IntakeService
    raise AttributeError(name)


__all__ = ["IntakeService", "create_intake_routes"]
