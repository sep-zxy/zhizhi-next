"""Server-configured channel identity bindings, never inferred from message text."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..auth import Actor


class SenderBindings:
    """Resolve exact channel/account/chat/sender tuples configured by an administrator."""

    def __init__(self, bindings: Mapping[tuple[str, str, str, str], Actor]) -> None:
        self._bindings = dict(bindings)

    def resolve(self, channel: str, account_id: str, chat_id: str, sender_id: str) -> Actor | None:
        """Return a verified actor only for an exact enrolled sender and destination."""
        return self._bindings.get((channel, account_id, chat_id, sender_id))
