"""Authenticated official messaging-channel adapters, disabled until configured."""

from .identity import SenderBindings
from .supervisor import ChannelSupervisor
from .telegram import TelegramBotAPI, TelegramLongPoller

__all__ = ["ChannelSupervisor", "SenderBindings", "TelegramBotAPI", "TelegramLongPoller"]
