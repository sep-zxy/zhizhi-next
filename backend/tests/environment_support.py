"""Explicit, isolated configuration environments for native-platform tests."""

import os
import shutil
from pathlib import Path


def config_environment(home: Path) -> dict[str, str]:
    """Provide both platform anchors without inheriting the runner's settings."""
    return {"HOME": str(home), "APPDATA": str(home / "AppData" / "Roaming")}


def bash_executable() -> str:
    """Use Git for Windows Bash rather than the unrelated WSL launcher."""
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            for parent in Path(git).parents:
                for relative in ("bin/bash.exe", "usr/bin/bash.exe"):
                    candidate = parent / relative
                    if candidate.is_file():
                        return str(candidate)
        raise RuntimeError("Git for Windows Bash is required for shell benchmark fixtures")
    return "bash"
