"""Official channel lifecycle with durable waits, bounded retry and opt-in replies.

All credentials, sender bindings, enabled adapters and the service actor are
injected by trusted assembly. This module never reads or creates credentials.
Run one supervisor per owner/account; horizontal channel leasing is not claimed.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..errors import DomainError
from ..intake.contracts import IntakeError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..auth import Actor
    from ..domain import DomainService
    from .telegram import TelegramLongPoller
    from .wechat_kf import WechatKFAdapter

_LOG = logging.getLogger(__name__)
_SAFE_CODES = frozenset(
    {
        "NEEDS_AUTH",
        "RATE_LIMITED",
        "CHANNEL_UNAVAILABLE",
        "CHANNEL_BACKLOG",
        "CHANNEL_CONFLICT",
        "CHANNEL_NOT_CONFIGURED",
        "INVALID_CHANNEL_RESPONSE",
        "CHANNEL_RESPONSE_TOO_LARGE",
        "CHANNEL_ENCODING_UNSUPPORTED",
        "FORBIDDEN",
        "LEASE_STALE",
        "INVALID_STATE",
        "IDEMPOTENCY_CONFLICT",
    }
)


def _safe_code(error: Exception) -> str:
    code = error.code if isinstance(error, DomainError | IntakeError) else None
    return code if code in _SAFE_CODES else "CHANNEL_FAILURE"


class ChannelSupervisor:
    """Lifespan component: ``await run_forever(stop)``; setting stop cancels polls."""

    def __init__(
        self,
        domain: DomainService,
        *,
        actor: Actor,
        telegram_pollers: Sequence[TelegramLongPoller] = (),
        wechat_adapters: Sequence[WechatKFAdapter] = (),
        worker_id: str = "channel-worker",
        poll_seconds: float = 1.0,
        retry_initial_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
        lease_seconds: int = 120,
        native_notifications_enabled: bool = False,
    ) -> None:
        actor.require("knowledge:read")
        actor.require("knowledge:write")
        actor.require("tasks:work")
        if actor.kind != "service" or not worker_id:
            raise ValueError("Channel supervision requires an injected service worker")
        if not 0 < poll_seconds <= 60 or not 0 < retry_initial_seconds <= retry_max_seconds <= 300:
            raise ValueError("Channel polling and retry limits are invalid")
        if not 60 <= lease_seconds <= 3600:
            raise ValueError("Channel lease must be between 60 and 3600 seconds")
        self.domain, self.actor, self.worker_id = domain, actor, worker_id
        self.telegram_pollers = tuple(telegram_pollers)
        self.wechat_adapters = tuple(wechat_adapters)
        self.poll_seconds = poll_seconds
        self.retry_initial_seconds, self.retry_max_seconds = (
            retry_initial_seconds,
            retry_max_seconds,
        )
        self.lease_seconds = lease_seconds
        self._health: dict[str, dict[str, Any]] = {}
        self._running = False
        keys: set[str] = set()
        for adapter in (*self.telegram_pollers, *self.wechat_adapters):
            if (
                adapter.intake.domain is not domain
                or adapter.state_actor.owner_id != actor.owner_id
            ):
                raise ValueError("Channel adapters must share their supervisor's domain and owner")
            if adapter.channel_key in keys:
                raise ValueError("Only one channel worker per account may be configured")
            keys.add(adapter.channel_key)
        self._wechat = {
            (adapter.config.account_id, adapter.config.open_kfid): adapter
            for adapter in self.wechat_adapters
        }
        self.notifications = None
        adapters = (*self.telegram_pollers, *self.wechat_adapters)
        if native_notifications_enabled and adapters:
            from .notifications import NativeNotificationDispatcher

            self.notifications = NativeNotificationDispatcher(
                adapters[0].intake, actor=actor, adapters=adapters
            )

    @property
    def health(self) -> dict[str, dict[str, Any]]:
        """Return only categorical states, never source content, URLs or exception text."""
        return {key: dict(value) for key, value in self._health.items()}

    def _delay(self, failures: int) -> float:
        return min(self.retry_max_seconds, self.retry_initial_seconds * 2 ** min(failures - 1, 16))

    def _status(self, key: str, state: str, code: str | None = None) -> None:
        value = {"state": state, "error_code": code}
        if self._health.get(key) == value:
            return
        self._health[key] = value
        try:
            # Separate namespace avoids racing with official cursor/offset writes.
            self.domain.set_channel_state(
                "supervisor:" + key,
                value,
                actor=self.actor,
                idempotency_key="channel-health:" + uuid4().hex,
            )
        except Exception as error:
            self._health[key] = {**value, "status_persistence_failed": True}
            _LOG.warning("Channel health persistence failed: %s", _safe_code(error))

    @staticmethod
    async def _wait(stop: asyncio.Event, seconds: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=seconds)

    async def _telegram_loop(self, poller: TelegramLongPoller, stop: asyncio.Event) -> None:
        ready, failures = False, 0
        while not stop.is_set():
            try:
                if not ready:
                    await poller.check_ready()
                    ready = True
                await poller.poll_once()
                failures = 0
                self._status(poller.channel_key, "ready")
                await self._wait(stop, self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                ready, failures = False, failures + 1
                self._status(poller.channel_key, "waiting", _safe_code(error))
                await self._wait(stop, self._delay(failures))

    async def _heartbeat(self, task: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            self.domain.renew_task_lease(
                task["task_id"],
                task["lease_token"],
                actor=self.actor,
                lease_seconds=self.lease_seconds,
            )

    async def process_wechat_once(self) -> bool:
        """Claim one persisted sync task; safe to call after service restart."""
        if not self._wechat:
            return False
        task = self.domain.claim_task(
            self.worker_id,
            actor=self.actor,
            kinds=["channel.wechat_kf.sync"],
            lease_seconds=self.lease_seconds,
        )
        if task is None:
            return False
        payload = task.get("payload") or {}
        account_id, open_kfid = payload.get("account_id"), payload.get("open_kfid")
        adapter = (
            self._wechat.get((account_id, open_kfid))
            if isinstance(account_id, str) and isinstance(open_kfid, str)
            else None
        )
        key = adapter.channel_key if adapter is not None else "wechat_kf:unconfigured"
        heartbeat: asyncio.Task[None] | None = None
        operation: asyncio.Task[dict[str, Any]] | None = None
        try:
            if adapter is None:
                raise IntakeError("CHANNEL_NOT_CONFIGURED", "该微信客服通道尚未配置。")
            heartbeat = asyncio.create_task(self._heartbeat(task))
            operation = asyncio.create_task(adapter.process_claimed(task))
            finished, _ = await asyncio.wait(
                {heartbeat, operation}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in finished:
                await heartbeat
            await operation
            self._status(key, "ready")
        except asyncio.CancelledError:
            # Leave the leased task for reconciliation, never guess a completion.
            raise
        except Exception as error:
            code = _safe_code(error)
            self._status(key, "waiting", code)
            if code != "LEASE_STALE":
                wait_auth = code in {"NEEDS_AUTH", "CHANNEL_NOT_CONFIGURED", "FORBIDDEN"}
                patch: dict[str, Any] = {
                    "state": "waiting_auth" if wait_auth else "retry_wait",
                    "lease_token": task["lease_token"],
                    "error": {"code": code},
                }
                if not wait_auth:
                    patch["next_run_at"] = (
                        datetime.now(UTC) + timedelta(seconds=self._delay(task.get("attempt", 1)))
                    ).isoformat()
                try:
                    self.domain.update_task(
                        task["task_id"],
                        patch,
                        actor=self.actor,
                        idempotency_key=f"channel-failure:{task['task_id']}:{task['lease_token']}",
                    )
                except Exception as persistence_error:
                    self._status(key, "waiting", _safe_code(persistence_error))
        finally:
            for pending in (operation, heartbeat):
                if pending is not None:
                    pending.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await pending
        return True

    async def _wechat_loop(self, stop: asyncio.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                await self.process_wechat_once()
                failures = 0
                await self._wait(stop, self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                self._status("wechat_kf:worker", "waiting", _safe_code(error))
                await self._wait(stop, self._delay(failures))

    async def _notification_loop(self, stop: asyncio.Event) -> None:
        failures = 0
        assert self.notifications is not None
        while not stop.is_set():
            try:
                self.notifications.collect_events()
                await self.notifications.process_once()
                failures = 0
                await self._wait(stop, self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                self._status("native-outbox", "waiting", _safe_code(error))
                await self._wait(stop, self._delay(failures))

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Run configured receivers until stopped; cancellation always joins children."""
        if self._running:
            raise RuntimeError("Channel supervisor is already running")
        self._running = True
        workers = [
            asyncio.create_task(self._telegram_loop(poller, stop))
            for poller in self.telegram_pollers
        ]
        if self._wechat:
            workers.append(asyncio.create_task(self._wechat_loop(stop)))
        if self.notifications is not None:
            workers.append(asyncio.create_task(self._notification_loop(stop)))
        try:
            await stop.wait()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self._running = False
