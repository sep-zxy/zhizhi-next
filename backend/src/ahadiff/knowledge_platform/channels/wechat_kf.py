"""Conditional official WeChat Customer Service callback and sync_msg adapter.

This is not a personal-account bot or a group sending webhook. Official account
qualification and API-managed customer-service access must be confirmed before
routes are registered. The optional MIT wechatpy SDK handles protocol crypto.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode

from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from ..intake.contracts import IntakeError
from .http_client import official_json_post

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

    from ..auth import Actor
    from ..intake.service import IntakeService
    from .identity import SenderBindings
    from .notifications import NativeNotificationDispatcher

CALLBACK_PATH = "/api/knowledge/v1/channels/wechat-kf/callback"


class CallbackCrypto(Protocol):
    """Pinned SDK interface; test substitutes must be clearly identified as fixtures."""

    def check_signature(self, signature: str, timestamp: str, nonce: str, echo_str: str) -> str: ...
    def decrypt_message(
        self, msg: dict[str, str], signature: str, timestamp: str, nonce: str
    ) -> str: ...


def _xml(data: str | bytes) -> ET.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > 256 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise IntakeError("INVALID_CALLBACK", "回调 XML 超限或含不允许的声明。", status_code=400)
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise IntakeError("INVALID_CALLBACK", "回调 XML 格式无效。", status_code=400) from exc


@dataclass(frozen=True, slots=True)
class WechatKFConfig:
    """No implicit registration, management switch, permission grant or credential save."""

    account_id: str
    corp_id: str
    open_kfid: str
    enabled: bool = False
    api_permission_confirmed: bool = False
    api_managed_confirmed: bool = False
    callback_token: str = field(default="", repr=False)
    encoding_aes_key: str = field(default="", repr=False)
    max_timestamp_skew_seconds: int = 300

    def require_ready(self) -> None:
        """Fail closed until the actual account's qualifications are confirmed."""
        if not (self.enabled and self.api_permission_confirmed and self.api_managed_confirmed):
            raise IntakeError(
                "CHANNEL_NOT_CONFIGURED",
                "微信客服主体/API 权限未确认，通道未启用。",
                status_code=503,
            )
        if not all(
            (
                self.account_id,
                self.corp_id,
                self.open_kfid,
                self.callback_token,
                self.encoding_aes_key,
            )
        ):
            raise IntakeError("NEEDS_AUTH", "微信客服安全配置尚未就绪。", status_code=503)


def create_wechat_crypto(config: WechatKFConfig) -> CallbackCrypto:
    """Use the verified wechatpy 1.8.18 enterprise API, without copying its source."""
    config.require_ready()
    try:
        from importlib import import_module
        from importlib.metadata import version

        if version("wechatpy") != "1.8.18":
            raise IntakeError(
                "SDK_VERSION_UNVERIFIED", "微信 SDK 版本未按审核版本固定。", status_code=503
            )
        crypto_type = import_module("wechatpy.enterprise.crypto").WeChatCrypto
    except ImportError as exc:
        raise IntakeError(
            "SDK_UNAVAILABLE", "可选微信 SDK 未安装，通道未启用。", status_code=503
        ) from exc
    return crypto_type(config.callback_token, config.encoding_aes_key, config.corp_id)


class WechatKFAPI(Protocol):
    """Receive-only official API surface."""

    async def sync_messages(
        self, *, open_kfid: str, cursor: str, token: str | None
    ) -> dict[str, Any]: ...


class OfficialWechatKFAPI:
    """Inject the existing access token from the authorized secret service on each call."""

    def __init__(self, access_token: Callable[[], str]) -> None:
        self._access_token = access_token

    async def sync_messages(
        self, *, open_kfid: str, cursor: str, token: str | None
    ) -> dict[str, Any]:
        """Call sync_msg, not enterprise messaging or a send-only group webhook."""
        access_token = self._access_token()
        if not access_token:
            raise IntakeError("NEEDS_AUTH", "微信客服凭据不可用。", status_code=503)
        url = "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?" + urlencode(
            {"access_token": access_token}
        )
        payload: dict[str, Any] = {
            "open_kfid": open_kfid,
            "cursor": cursor,
            "limit": 100,
            "voice_format": 0,
        }
        if token:
            payload["token"] = token
        result = await official_json_post(url, payload, timeout=15.0)
        code = result.get("errcode", 0)
        if code:
            raise IntakeError(
                "NEEDS_AUTH"
                if code in {40014, 42001, 40001, 60030}
                else "RATE_LIMITED"
                if code in {45009, 45011}
                else "CHANNEL_UNAVAILABLE",
                "微信客服 API 未接受请求；未绕过账号权限或频率限制。",
                status_code=503,
                retryable=code in {45009, 45011},
            )
        return result

    async def send_native(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fixed official text API, only called by an explicitly enabled dispatcher."""
        if (
            set(payload) != {"touser", "open_kfid", "msgid", "msgtype", "text"}
            or payload["msgtype"] != "text"
        ):
            raise ValueError("Only fixed native text notifications are supported")
        content = payload.get("text", {}).get("content")
        if not isinstance(content, str) or not 1 <= len(content.encode()) <= 2048:
            raise ValueError("Wechat native text must fit the official byte limit")
        token = self._access_token()
        if not token:
            raise IntakeError("NEEDS_AUTH", "微信客服凭据不可用。", status_code=503)
        response = await official_json_post(
            "https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?" + urlencode({"access_token": token}),
            payload,
            timeout=15.0,
        )
        code = response.get("errcode")
        if code != 0:
            raise IntakeError(
                "NEEDS_AUTH"
                if code in {40014, 42001, 40001, 60030}
                else "RATE_LIMITED"
                if code in {45009, 45011}
                else "CHANNEL_REJECTED",
                "微信客服回执未被接受，未绕过服务窗口或发送额度。",
                status_code=503,
            )
        return response


class WechatKFAdapter:
    """Verify callback first, queue quickly, then retrieve and bind actual senders."""

    def __init__(
        self,
        intake: IntakeService,
        config: WechatKFConfig,
        api: WechatKFAPI,
        bindings: SenderBindings,
        *,
        state_actor: Actor,
        crypto: CallbackCrypto | None = None,
    ) -> None:
        config.require_ready()
        self.intake, self.config, self.api = intake, config, api
        self.bindings, self.state_actor = bindings, state_actor
        self.crypto = crypto or create_wechat_crypto(config)
        self.channel_key = "wechat_kf:" + config.account_id + ":" + config.open_kfid
        self._tokens: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()
        self.notifications: NativeNotificationDispatcher | None = None

    def _validate_signature(
        self, encrypted: str, signature: str, timestamp: str, nonce: str
    ) -> None:
        try:
            recent = abs(time.time() - int(timestamp)) <= self.config.max_timestamp_skew_seconds
        except (ValueError, OverflowError):
            recent = False
        if not recent or not nonce or len(nonce) > 256 or len(signature) != 40:
            raise IntakeError("INVALID_SIGNATURE", "回调签名或时间窗口无效。", status_code=403)
        signed = "".join(sorted((self.config.callback_token, timestamp, nonce, encrypted)))
        digest = hashlib.sha1(signed.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, signature):
            raise IntakeError("INVALID_SIGNATURE", "回调签名无效。", status_code=403)

    def verify_url(self, *, encrypted: str, signature: str, timestamp: str, nonce: str) -> str:
        """Verify the official URL challenge; never return a value before authentication."""
        self._validate_signature(encrypted, signature, timestamp, nonce)
        try:
            return self.crypto.check_signature(signature, timestamp, nonce, encrypted)
        except Exception as exc:
            raise IntakeError(
                "INVALID_SIGNATURE", "无法验证微信客服回调。", status_code=403
            ) from exc

    def receive_callback(
        self, raw: bytes, *, signature: str, timestamp: str, nonce: str
    ) -> dict[str, Any] | None:
        """Persist a sync task before returning success, without storing plaintext tokens."""
        encrypted = _xml(raw).findtext("Encrypt") or ""
        self._validate_signature(encrypted, signature, timestamp, nonce)
        try:
            plaintext = self.crypto.decrypt_message(
                {"Encrypt": encrypted}, signature, timestamp, nonce
            )
        except Exception as exc:
            raise IntakeError(
                "INVALID_SIGNATURE", "无法解密微信客服回调。", status_code=403
            ) from exc
        event = _xml(plaintext)
        if event.findtext("Event") != "kf_msg_or_event":
            return None
        if event.findtext("OpenKfId") != self.config.open_kfid:
            raise IntakeError("FORBIDDEN", "回调客服账号不匹配。", status_code=403)
        fingerprint = hashlib.sha256(raw).hexdigest()
        task = self.intake.domain.create_task(
            "channel.wechat_kf.sync",
            {"account_id": self.config.account_id, "open_kfid": self.config.open_kfid},
            actor=self.state_actor,
            idempotency_key="wechat-callback:" + fingerprint,
        )
        now = time.monotonic()
        self._tokens = {key: value for key, value in self._tokens.items() if value[0] > now}
        token = event.findtext("Token")
        if token and len(self._tokens) < 1000:
            self._tokens[task["task_id"]] = (now + 540, token)
        return {"task_id": task["task_id"], "state": task["state"]}

    async def process_claimed(self, task: dict[str, Any]) -> dict[str, Any]:
        """Resume sync from the domain cursor; missing transient token never loses receipts."""
        if task.get("kind") != "channel.wechat_kf.sync" or not task.get("lease_token"):
            raise IntakeError("INVALID_TASK", "需要已领取的微信同步任务。", status_code=409)
        if (
            task.get("payload", {}).get("open_kfid") != self.config.open_kfid
            or task.get("payload", {}).get("account_id") != self.config.account_id
        ):
            raise IntakeError("FORBIDDEN", "任务客服账号不匹配。", status_code=403)
        async with self._lock:
            token_item = self._tokens.get(task["task_id"])
            token = token_item[1] if token_item and token_item[0] > time.monotonic() else None
            state = self.intake.domain.get_channel_state(self.channel_key, actor=self.state_actor)
            cursor = state.get("state", state).get("cursor", "")
            receipts: list[str] = []
            for _page in range(20):
                result = await self.api.sync_messages(
                    open_kfid=self.config.open_kfid, cursor=cursor, token=token
                )
                messages = result.get("msg_list", [])
                if not isinstance(messages, list):
                    raise IntakeError(
                        "INVALID_CHANNEL_RESPONSE", "微信客服消息格式无效。", status_code=502
                    )
                for message in messages:
                    receipt = self._receive_message(message)
                    if receipt is not None:
                        receipts.append(receipt["intake_id"])
                next_cursor = result.get("next_cursor")
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise IntakeError(
                        "INVALID_CHANNEL_RESPONSE", "微信客服游标无效。", status_code=502
                    )
                self.intake.domain.set_channel_state(
                    self.channel_key,
                    {"cursor": next_cursor},
                    actor=self.state_actor,
                    idempotency_key="wechat-cursor:"
                    + hashlib.sha256(next_cursor.encode()).hexdigest(),
                )
                if not result.get("has_more"):
                    self._tokens.pop(task["task_id"], None)
                    return self.intake.domain.update_task(
                        task["task_id"],
                        {
                            "state": "succeeded",
                            "lease_token": task["lease_token"],
                            "result": {"receipt_ids": receipts},
                        },
                        actor=self.state_actor,
                        idempotency_key=f"wechat-sync:{task['task_id']}:{task.get('attempt', 0)}",
                    )
                if next_cursor == cursor:
                    raise IntakeError(
                        "INVALID_CHANNEL_RESPONSE", "微信客服游标未推进。", status_code=502
                    )
                cursor = next_cursor
                self.intake.domain.renew_task_lease(
                    task["task_id"], task["lease_token"], actor=self.state_actor, lease_seconds=120
                )
            raise IntakeError(
                "CHANNEL_BACKLOG", "微信客服分页超过本轮上限，请续跑已有任务。", retryable=True
            )

    def _receive_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("origin") != 3:
            return None
        if message.get("open_kfid") != self.config.open_kfid:
            return None
        sender = message.get("external_userid")
        actor = self.bindings.resolve(
            "wechat_kf", self.config.account_id, self.config.open_kfid, str(sender or "")
        )
        if actor is None:
            return None
        msgid = message.get("msgid")
        if not isinstance(msgid, str) or not msgid:
            raise IntakeError(
                "INVALID_CHANNEL_RESPONSE", "微信客服消息缺少稳定标识。", status_code=502
            )
        text = (
            message.get("text", {}).get("content")
            if isinstance(message.get("text"), dict)
            else None
        )
        link = message.get("link", {}).get("url") if isinstance(message.get("link"), dict) else None
        unsupported = None
        if (
            self.notifications is not None
            and isinstance(text, str)
            and text.startswith(("知枝收藏 ", "知枝丢弃 "))
        ):
            return self.notifications.wechat_decision(
                self, text, sender_id=str(sender), external_id=msgid
            )
        if not text and not link:
            unsupported = "微信消息类型尚未接入，请通过 HTTP Inbox 补充正文或链接。"
        receipt = self.intake.receive(
            {"text": text or "", "url": link},
            actor=actor,
            idempotency_key="wechat-message:" + msgid,
            channel=self.channel_key,
            external_message_id=msgid,
            unsupported_reason=unsupported,
        )
        if self.notifications is not None:
            self.notifications.register_receipt(
                self,
                receipt,
                channel="wechat_kf",
                account_id=self.config.account_id,
                chat_id=self.config.open_kfid,
                sender_id=str(sender),
            )
        return receipt

    async def send_native(
        self, route: dict[str, Any], rendered: dict[str, Any], task_id: str
    ) -> str:
        """Reply only to the exactly bound customer; never change service-window limits."""
        if (
            self.notifications is None
            or route.get("channel_key") != self.channel_key
            or self.bindings.resolve(
                "wechat_kf",
                self.config.account_id,
                self.config.open_kfid,
                route.get("sender_id", ""),
            )
            is None
        ):
            raise IntakeError("FORBIDDEN", "未启用或未绑定微信客服回执。", status_code=403)
        sender = getattr(self.api, "send_native", None)
        if sender is None:
            raise IntakeError("CHANNEL_NOT_CONFIGURED", "微信原生发送 API 尚未配置。")
        response = await sender(
            {
                "touser": route["sender_id"],
                "open_kfid": self.config.open_kfid,
                "msgid": hashlib.sha256(task_id.encode()).hexdigest()[:32],
                "msgtype": "text",
                "text": {"content": rendered["text"]},
            }
        )
        message_id = response.get("msgid")
        if (
            response.get("errcode") != 0
            or not isinstance(message_id, str)
            or not 1 <= len(message_id) <= 128
        ):
            raise IntakeError("CHANNEL_UNAVAILABLE", "无法确认微信客服投递状态。")
        return message_id


def create_wechat_kf_routes(adapter: WechatKFAdapter) -> list[Route]:
    """Register only this exact signed callback path outside bearer middleware."""

    async def callback(request: Request) -> PlainTextResponse | JSONResponse:
        try:
            query = request.query_params
            params = {
                "signature": query.get("msg_signature", ""),
                "timestamp": query.get("timestamp", ""),
                "nonce": query.get("nonce", ""),
            }
            if request.method == "GET":
                return PlainTextResponse(
                    adapter.verify_url(encrypted=query.get("echostr", ""), **params)
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 256 * 1024:
                    raise IntakeError("BODY_TOO_LARGE", "回调体积超过限制。", status_code=413)
                chunks.append(chunk)
            adapter.receive_callback(b"".join(chunks), **params)
            return PlainTextResponse("success")
        except IntakeError as exc:
            return JSONResponse(
                {
                    "error": {"code": exc.code, "detail": exc.detail, "retryable": exc.retryable},
                    "trace_id": None,
                },
                status_code=exc.status_code,
            )

    return [Route(CALLBACK_PATH, callback, methods=["GET", "POST"])]
