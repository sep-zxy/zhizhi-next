"""Non-secret, explicitly configured navigation only; never an authentication bridge."""

from __future__ import annotations

import re


def learning_viewer_url(value: str | None) -> str | None:
    """Accept only the launcher's canonical, separately bound loopback root URL."""
    if value is None:
        return None
    match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{3,4})/", value) if isinstance(
        value, str
    ) else None
    if match is None or not 1024 <= int(match.group(1)) <= 65535:
        raise ValueError("Learning navigation requires a canonical non-privileged loopback root")
    return value
