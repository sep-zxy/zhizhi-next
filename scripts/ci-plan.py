"""Decide whether a pull request touches the first-release code or tooling."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_DIRS = (
    ".github/", "scripts/", "backend/", "apps/learning-web/", "apps/wiki-private/",
    "deploy/knowledge-platform/", "integrations/pi-zhizhi/", "knowledge-contracts/",
    "tests/e2e/", "tests/repository/",
)
ACTIVE_FILES = {
    ".gitattributes", "config/toolchains.json", "config/examples/platform.example.json",
    "config/examples/models.example.json",
}
REFERENCE_TESTS = {
    "tests/repository/test_design_coverage_matrix.py",
    "tests/repository/test_layout.py",
    "tests/e2e/test_channel_configuration.py",
    "tests/e2e/test_channel_secret_recovery.py",
    "tests/e2e/test_desktop_configuration.py",
}


def needs_product_checks(paths: list[str]) -> bool:
    for name in paths:
        path = PurePosixPath(name)
        if path.name in {"README.md", "AGENTS.md"} or "docs" in path.parts or name in REFERENCE_TESTS:
            continue
        if name in ACTIVE_FILES or name.startswith(ACTIVE_DIRS):
            return True
    return False


def main() -> None:
    verify = True
    base = os.environ.get("BASE_SHA")
    if os.environ["GITHUB_EVENT_NAME"] == "pull_request" and base:
        changed = subprocess.check_output(
            ["git", "diff", "--no-renames", "--name-only", "-z", base, "HEAD"], cwd=ROOT,
        ).decode("utf-8").split("\0")
        verify = needs_product_checks([path for path in changed if path])
    scope = "product" if verify else "metadata"
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        stream.write(f"scope={scope}\n")
    print(f"CI scope: {scope}")


if __name__ == "__main__":
    main()
