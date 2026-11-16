"""Keep the API alive and explicitly waiting when its secret service is sealed."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .auth import Actor
from .intake.contracts import IntakeError
from .secrets import SecretUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable

    from .domain import DomainService


class DeferredChannelRuntime:
    """Lazy credentials; no callback is acknowledged before real verification."""

    def __init__(
        self,
        domain: DomainService,
        owner: str,
        factory: Callable[[], tuple[Any, Any]],
        *,
        retry_seconds: float = 60,
    ) -> None:
        if retry_seconds <= 0:
            raise ValueError("A positive retry interval is required")
        self.domain, self.factory, self.retry_seconds = domain, factory, retry_seconds
        self.actor = Actor(
            owner, "channel-bootstrap", "service", frozenset({"knowledge:read", "knowledge:write"})
        )
        self.status = "waiting_configuration"
        self.error_code: str | None = None
        self.adapter: Any = None

    def _record(self, status: str, code: str | None = None) -> None:
        self.status, self.error_code = status, code
        self.domain.set_channel_state(
            "bootstrap",
            {
                "state": status,
                "error_code": code,
                "checked_at": datetime.now(UTC).isoformat(),
            },
            actor=self.actor,
            idempotency_key="channel-bootstrap:" + uuid4().hex,
        )

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            retryable = True
            try:
                supervisor, adapter = await asyncio.to_thread(self.factory)
                if stop.is_set():
                    return
                self.adapter = adapter
                self._record("ready")
                await supervisor.run_forever(stop)
                return
            except SecretUnavailable:
                self._record("waiting_auth", "SECRET_UNAVAILABLE")
            except IntakeError as error:
                code = (
                    error.code
                    if error.code in {"NEEDS_AUTH", "CHANNEL_NOT_CONFIGURED", "CHANNEL_UNAVAILABLE"}
                    else "CHANNEL_CONFIGURATION_FAILED"
                )
                self._record("waiting_auth" if code == "NEEDS_AUTH" else "blocked", code)
                retryable = code in {"NEEDS_AUTH", "CHANNEL_UNAVAILABLE"}
            except ValueError:
                self._record("blocked", "INVALID_CHANNEL_CONFIGURATION")
                retryable = False
            finally:
                self.adapter = None
            if not retryable:
                await stop.wait()
                return
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.retry_seconds)

    def verify_url(self, **params: Any) -> str:
        if self.adapter is None:
            raise IntakeError(
                "NEEDS_AUTH", "官方渠道凭据暂不可用。", status_code=503, retryable=True
            )
        return str(self.adapter.verify_url(**params))

    def receive_callback(self, body: bytes, **params: Any) -> Any:
        if self.adapter is None:
            raise IntakeError(
                "NEEDS_AUTH", "官方渠道凭据暂不可用。", status_code=503, retryable=True
            )
        return self.adapter.receive_callback(body, **params)
