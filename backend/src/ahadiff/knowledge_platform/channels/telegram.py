"""Telegram official Bot API long polling with durable, post-receipt offsets.

No bot creation, credential discovery, webhook changes, private-user automation,
or cross-channel forwarding occurs here. Deploy one poller per bot account.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from ..intake.contracts import IntakeError
from .http_client import official_json_post

if TYPE_CHECKING:
    from ..auth import Actor
    from ..intake.service import IntakeService
    from .identity import SenderBindings
    from .notifications import NativeNotificationDispatcher


class TelegramAPI(Protocol):
    """Official API transport; fixtures must explicitly supply their own implementation."""

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(slots=True)
class TelegramBotAPI:
    """Memory-only bot credential injected by the authorized secret provider."""

    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self.token):
            raise IntakeError("NEEDS_AUTH", "尚未配置有效的 Telegram Bot 凭据。", status_code=503)

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Only receive/status methods are exposed; no implicit outgoing messages."""
        if method not in {"getUpdates", "getWebhookInfo"}:
            raise ValueError("Telegram operation is not allowed")
        return await self._request(method, payload)

    async def send_native(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Only the opt-in bound-recipient dispatcher uses this restricted sender."""
        allowed = {
            "sendMessage": {
                "chat_id",
                "text",
                "reply_markup",
                "protect_content",
                "link_preview_options",
            },
            "answerCallbackQuery": {"callback_query_id", "text"},
        }
        if method not in allowed or set(payload) - allowed[method]:
            raise ValueError("Unsupported native notification operation")
        return await self._request(method, payload)

    async def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = await official_json_post(
            f"https://api.telegram.org/bot{self.token}/{method}", payload
        )
        if result.get("ok") is not True:
            code = result.get("error_code")
            raise IntakeError(
                "NEEDS_AUTH"
                if code in {401, 403}
                else "RATE_LIMITED"
                if code == 429
                else "CHANNEL_REJECTED"
                if code == 400
                else "CHANNEL_UNAVAILABLE",
                "Telegram 官方 API 未接受请求。",
                status_code=503,
                retryable=code == 429,
            )
        return result


class TelegramLongPoller:
    """Authenticate transport, bind senders, persist intake, then advance the offset."""

    def __init__(
        self,
        intake: IntakeService,
        api: TelegramAPI,
        bindings: SenderBindings,
        *,
        account_id: str,
        state_actor: Actor,
        polling_seconds: int = 25,
    ) -> None:
        if not account_id or not 1 <= polling_seconds <= 30:
            raise ValueError("A stable account id and polling timeout are required")
        self.intake, self.api, self.bindings = intake, api, bindings
        self.account_id, self.state_actor = account_id, state_actor
        self.polling_seconds = polling_seconds
        self.channel_key = "telegram:" + account_id
        self._lock = asyncio.Lock()
        self.notifications: NativeNotificationDispatcher | None = None

    async def check_ready(self) -> None:
        """Refuse conflicting webhooks rather than silently deleting their configuration."""
        response = await self.api.call("getWebhookInfo", {})
        result = response.get("result")
        if not isinstance(result, dict) or result.get("url"):
            raise IntakeError(
                "CHANNEL_CONFLICT",
                "Telegram 已配置 webhook，不能同时启用 long polling。",
                status_code=409,
            )

    async def poll_once(self) -> list[dict[str, Any]]:
        """Return only stable receipts; a failed intake prevents acknowledging that update."""
        async with self._lock:
            state = self.intake.domain.get_channel_state(self.channel_key, actor=self.state_actor)
            saved = state.get("state", state)
            offset = int(saved.get("offset", 0))
            response = await self.api.call(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": self.polling_seconds,
                    "limit": 100,
                    "allowed_updates": ["message", "callback_query"]
                    if self.notifications
                    else ["message"],
                },
            )
            updates = response.get("result")
            if not isinstance(updates, list):
                raise IntakeError(
                    "INVALID_CHANNEL_RESPONSE", "Telegram 更新格式无效。", status_code=502
                )
            if any(
                not isinstance(update, dict) or type(update.get("update_id")) is not int
                for update in updates
            ):
                raise IntakeError(
                    "INVALID_CHANNEL_RESPONSE", "Telegram 更新标识无效。", status_code=502
                )
            receipts: list[dict[str, Any]] = []
            for update in sorted(updates, key=lambda value: value.get("update_id", -1)):
                if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                    raise IntakeError(
                        "INVALID_CHANNEL_RESPONSE", "Telegram 更新标识无效。", status_code=502
                    )
                update_id = update["update_id"]
                if update_id < offset:
                    continue
                receipt = self._receive_update(update)
                if receipt is not None:
                    receipts.append(receipt)
                # Unknown senders are deliberately ignored, without parsing their
                # URLs or creating records. A known sender's durable receipt must
                # exist before this acknowledgement can happen.
                offset = update_id + 1
                self.intake.domain.set_channel_state(
                    self.channel_key,
                    {"offset": offset},
                    actor=self.state_actor,
                    idempotency_key=f"telegram-offset:{self.account_id}:{offset}",
                )
            return receipts

    def _receive_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        callback = update.get("callback_query")
        if self.notifications is not None and isinstance(callback, dict):
            sender = callback.get("from") or {}
            message = callback.get("message") or {}
            if not isinstance(message, dict):
                return None
            chat = message.get("chat") or {}
            data, callback_id = callback.get("data"), callback.get("id")
            if (
                not isinstance(sender, dict)
                or not isinstance(chat, dict)
                or type(chat.get("id")) is not int
                or type(sender.get("id")) is not int
                or chat.get("type") != "private"
                or str(chat.get("id")) != str(sender.get("id"))
                or sender.get("is_bot")
                or not isinstance(data, str)
                or len(data.encode()) > 64
                or not isinstance(callback_id, str)
                or not 1 <= len(callback_id) <= 200
            ):
                return None
            return self.notifications.decide(
                self,
                data=data,
                chat_id=str(chat["id"]),
                sender_id=str(sender["id"]),
                external_id=callback_id,
                callback_id=callback_id,
            )
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender, chat = message.get("from"), message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict) or sender.get("is_bot"):
            return None
        actor = self.bindings.resolve(
            "telegram", self.account_id, str(chat.get("id", "")), str(sender.get("id", ""))
        )
        if actor is None:
            return None
        text = message.get("text") or message.get("caption")
        unsupported = None
        if not isinstance(text, str) or not text.strip():
            # Files need a separately authorized bounded getFile adapter. Do not
            # guess that a captionless attachment was captured successfully.
            text = ""
            unsupported = "Telegram 消息附件尚未接入，请通过 HTTP Inbox 补充正文或链接。"
        update_id = update["update_id"]
        receipt = self.intake.receive(
            {"text": text},
            actor=actor,
            idempotency_key=f"telegram:{self.account_id}:{update_id}",
            channel=self.channel_key,
            external_message_id=str(update_id),
            unsupported_reason=unsupported,
        )
        if (
            self.notifications is not None
            and chat.get("type") == "private"
            and str(chat.get("id")) == str(sender.get("id"))
        ):
            self.notifications.register_receipt(
                self,
                receipt,
                channel="telegram",
                account_id=self.account_id,
                chat_id=str(chat["id"]),
                sender_id=str(sender["id"]),
            )
        return receipt

    async def send_native(
        self, route: dict[str, Any], rendered: dict[str, Any], task_id: str
    ) -> str:
        """Emit only templates to the exact enrolled private sender, never arbitrary chats."""
        if (
            self.notifications is None
            or route.get("channel_key") != self.channel_key
            or (route.get("chat_id") != route.get("sender_id"))
            or self.bindings.resolve(
                "telegram", self.account_id, route["chat_id"], route["sender_id"]
            )
            is None
        ):
            raise IntakeError("FORBIDDEN", "未启用或未绑定原生私聊回执。", status_code=403)
        sender = getattr(self.api, "send_native", None)
        if sender is None:
            raise IntakeError("CHANNEL_NOT_CONFIGURED", "原生发送 API 尚未配置。")
        if "callback_query_id" in rendered:
            response = await sender("answerCallbackQuery", rendered)
            if response.get("ok") is not True:
                raise IntakeError("CHANNEL_UNAVAILABLE", "无法确认回调答复状态。")
            return "callback-ack"
        payload = {
            "chat_id": route["chat_id"],
            "text": rendered["text"],
            "protect_content": True,
            "link_preview_options": {"is_disabled": True},
        }
        if "reply_markup" in rendered:
            payload["reply_markup"] = rendered["reply_markup"]
        response = await sender("sendMessage", payload)
        result = response.get("result")
        if (
            response.get("ok") is not True
            or not isinstance(result, dict)
            or type(result.get("message_id")) is not int
        ):
            raise IntakeError("CHANNEL_UNAVAILABLE", "无法确认消息投递状态。")
        return str(result["message_id"])
