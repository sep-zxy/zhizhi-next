"""Create a real isolated Git repository instead of an empty .git placeholder."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def init_test_git(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(["git", "-c", "init.templateDir=", "init", "--quiet",
                    "--initial-branch=main", str(root)], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace", env=env)
