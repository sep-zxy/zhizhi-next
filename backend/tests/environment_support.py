"""Explicit, isolated configuration environments for native-platform tests."""

from pathlib import Path


def config_environment(home: Path) -> dict[str, str]:
    """Provide both platform anchors without inheriting the runner's settings."""
    return {"HOME": str(home), "APPDATA": str(home / "AppData" / "Roaming")}
