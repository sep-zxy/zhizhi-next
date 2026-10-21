"""Trusted startup assembly for the official, explicitly enabled channel adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .auth import Actor
from .channels.identity import SenderBindings
from .channels.supervisor import ChannelSupervisor
from .channels.telegram import TelegramBotAPI, TelegramLongPoller
from .channels.wechat_kf import OfficialWechatKFAPI, WechatKFAdapter, WechatKFConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .domain import DomainService
    from .intake import IntakeService


def build_channels(
    config: dict[str, Any],
    *,
    domain: DomainService,
    intake: IntakeService,
    owner: str,
    identities: Sequence[Actor],
    resolve_secret: Callable[[str], str],
    allowed_classifications: frozenset[str] = frozenset({"public", "personal"}),
) -> tuple[ChannelSupervisor, Any]:
    """No identity enrollment or credential creation is performed by this loader."""
    if set(config) - {
        "bindings",
        "telegram",
        "wechat_kf",
        "secrets",
        "native_notifications_enabled",
    }:
        raise ValueError("Unknown channel configuration field")
    users = {
        actor.actor_id: actor
        for actor in identities
        if actor.owner_id == owner and actor.kind == "user"
    }
    mappings: dict[tuple[str, str, str, str], Actor] = {}
    for raw in config.get("bindings", []):
        if not isinstance(raw, dict):
            raise ValueError("Invalid exact sender binding")
        binding = cast("dict[str, Any]", raw)
        if set(binding) != {"channel", "account_id", "chat_id", "sender_id", "actor_id"}:
            raise ValueError("Invalid exact sender binding")
        if binding["actor_id"] not in users:
            raise ValueError("Channel sender must refer to an existing owner identity")
        key = (binding["channel"], binding["account_id"], binding["chat_id"], binding["sender_id"])
        if any(not isinstance(value, str) or not value for value in key) or key in mappings:
            raise ValueError("Invalid or duplicate channel identity binding")
        mappings[key] = users[binding["actor_id"]]
    bindings = SenderBindings(mappings)
    service = Actor(
        owner,
        "channel-worker",
        "service",
        frozenset({"knowledge:read", "knowledge:write", "tasks:work"}),
        allowed_classifications,
    )
    pollers: list[TelegramLongPoller] = []
    telegram = config.get("telegram")
    if telegram and telegram.get("enabled") is True:
        pollers.append(
            TelegramLongPoller(
                intake,
                TelegramBotAPI(resolve_secret(telegram["token_reference"])),
                bindings,
                account_id=telegram["account_id"],
                state_actor=service,
            )
        )
    wechat = config.get("wechat_kf")
    adapter = None
    if wechat and wechat.get("enabled") is True:
        settings = WechatKFConfig(
            account_id=wechat["account_id"],
            corp_id=wechat["corp_id"],
            open_kfid=wechat["open_kfid"],
            enabled=True,
            api_permission_confirmed=wechat.get("api_permission_confirmed") is True,
            api_managed_confirmed=wechat.get("api_managed_confirmed") is True,
            callback_token=resolve_secret(wechat["callback_token_reference"]),
            encoding_aes_key=resolve_secret(wechat["aes_key_reference"]),
        )
        api = OfficialWechatKFAPI(lambda: resolve_secret(wechat["access_token_reference"]))
        adapter = WechatKFAdapter(intake, settings, api, bindings, state_actor=service)
    return ChannelSupervisor(
        domain,
        actor=service,
        telegram_pollers=pollers,
        wechat_adapters=[adapter] if adapter else [],
        native_notifications_enabled=config.get("native_notifications_enabled") is True,
    ), adapter
