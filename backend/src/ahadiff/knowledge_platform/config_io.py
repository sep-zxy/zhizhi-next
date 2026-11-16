"""Protected deployment configuration loading, independent of executable entry points."""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path


def load_config_object(path: Path, *, secret: bool = False) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Configuration must be a regular, non-symlink file")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Configuration file is too large")
    if secret and os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Credential file must not be readable by group or other users")
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object")
    return cast("dict[str, Any]", value)


def string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a string list")
    items = cast("list[Any]", value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{label} must be a string list")
    return cast("list[str]", items)
