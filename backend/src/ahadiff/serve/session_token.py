"""Single-process Viewer session token handoff from the packaged parent launcher."""

from __future__ import annotations

import os
import re
import secrets

SESSION_TOKEN_ENV = "AHADIFF_SERVE_SESSION_TOKEN"


def serve_session_token() -> str:
    """Consume a private child environment value, never bootstrap or persist it.

    The launcher moves the existing ephemeral token generation upstream only for
    its human-interactive Pi session. All other serve launches retain their
    original random token. This is not a domain identity or a provider credential.
    """
    token = os.environ.pop(SESSION_TOKEN_ENV, None)
    if token is None:
        return secrets.token_urlsafe(24)
    if (
        not os.environ.get("ZHIZHI_PORTABLE_ROOT")
        or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token)
    ):
        raise ValueError("Invalid packaged Viewer session configuration")
    return token
