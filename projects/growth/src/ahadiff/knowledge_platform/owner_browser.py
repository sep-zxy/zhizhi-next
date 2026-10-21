"""Default-off browser capability for one explicitly configured existing owner.

This module neither loads credentials nor creates a persistent identity. Shared
pairing/proof/CSRF mechanics are inherited; the synthetic test policy is unchanged.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

from .auth import Actor, TokenAuthenticator
from .browser_sessions import TEST_ACTOR, TEST_OWNER, LoopbackBrowserSessions

OWNER_BROWSER_SCOPES = frozenset({"knowledge:read", "knowledge:write", "knowledge:review"})


def configured_owner_policy(
    config: dict[str, Any],
    *,
    protected_current_user: bool = False,
) -> dict[str, str]:
    """Read only an explicit existing protected-file policy, never infer from a token."""
    if os.name == "nt" and not protected_current_user:
        # The shared JSON loader does not verify Windows DACLs. Do not turn
        # plaintext file presence into permission to activate a real owner.
        raise ValueError("Windows owner file protection is not yet verified")
    policy = config.get("owner_browser")
    if (
        not isinstance(policy, dict)
        or set(policy) != {"owner_id", "actor_id", "origin"}
        or any(not isinstance(value, str) or not value.strip() for value in policy.values())
    ):
        raise ValueError("Explicit owner browser policy is required")
    # Validate the full origin and reserved-identity restrictions without activation.
    OwnerBrowserSessions(
        policy["origin"],
        TokenAuthenticator({}),
        owner_id=policy["owner_id"],
        actor_id=policy["actor_id"],
    )
    return {key: policy[key] for key in ("owner_id", "actor_id", "origin")}


class OwnerBrowserSessions(LoopbackBrowserSessions):
    """One user principal, narrowed to browser read/write/review, never worker/apply."""

    cookie_name = "wiki_owner_session"
    auth_prefix = "/api/knowledge/v1/auth/owner"
    fixture = False
    authentication_mode = "owner_loopback"

    def __init__(
        self,
        origin: str,
        authenticator: TokenAuthenticator,
        *,
        owner_id: str,
        actor_id: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not owner_id or not actor_id or owner_id == TEST_OWNER or actor_id == TEST_ACTOR:
            raise ValueError("Owner browser policy requires an explicit non-test identity")
        self.owner_id, self.actor_id = owner_id, actor_id
        super().__init__(origin, authenticator, clock=clock)

    def approval_actor(self, actor: Actor) -> Actor:
        """A valid bearer for a different owner, device or service cannot approve."""
        if (
            actor.kind != "user"
            or actor.owner_id != self.owner_id
            or actor.actor_id != self.actor_id
        ):
            raise self._deny("OWNER_IDENTITY_REQUIRED", status=403)
        scopes = (
            OWNER_BROWSER_SCOPES if "*" in actor.scopes else actor.scopes & OWNER_BROWSER_SCOPES
        )
        if "knowledge:read" not in scopes:
            raise self._deny("OWNER_SCOPE_REQUIRED", status=403)
        return Actor(actor.owner_id, actor.actor_id, "user", scopes, actor.allowed_classifications)

    def validate_identities(self, actors: Iterable[Actor]) -> None:
        """Factory validation before exposing routes; configuration cannot invent an actor."""
        matches = [
            actor
            for actor in actors
            if actor.owner_id == self.owner_id and actor.actor_id == self.actor_id
        ]
        if not matches or any(actor != matches[0] for actor in matches):
            raise ValueError("Owner browser identity is absent or ambiguous")
        self.approval_actor(matches[0])
