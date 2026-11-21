"""Select affected component owners; always keep a repository check and final verdict."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ALL = ["backend", "learning-web", "wiki-private", "pi-zhizhi"]
WEB_CLIENTS = {"learning-web", "wiki-private"}
OWNERS = {
    "backend/": set(ALL),
    "deploy/knowledge-platform/": set(ALL),
    "apps/learning-web/": {"learning-web"},
    "apps/wiki-private/": {"wiki-private"},
    "integrations/pi-zhizhi/": {"pi-zhizhi"},
    "knowledge-contracts/": set(ALL),
    "tests/": {"backend"},
}
HISTORICAL_TESTS = {
    "tests/repository/test_design_coverage_matrix.py",
    "tests/repository/test_layout.py",
    "tests/e2e/test_channel_configuration.py",
    "tests/e2e/test_channel_secret_recovery.py",
    "tests/e2e/test_desktop_configuration.py",
}
DOCUMENT_NAMES = {"README.md", "AGENTS.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "LICENSE.md"}


def select_modules(changed: list[str]) -> list[str]:
    """Select first-release components and the clients of shared service code."""
    selected: set[str] = set()
    for name in changed:
        path = PurePosixPath(name)
        if (
            path.name in DOCUMENT_NAMES
            or path.parts[0] in {".agents", "archive"}
            or "docs" in path.parts
            or name in HISTORICAL_TESTS
        ):
            continue
        if name.startswith(("scripts/", ".github/")) or name in {
            "config/toolchains.json", "config/examples/platform.example.json", "config/examples/models.example.json",
        }:
            selected.update(ALL)
        for prefix, owners in OWNERS.items():
            if name.startswith(prefix):
                selected.update(owners)
    return [name for name in ALL if name in selected]


def component_matrix(modules: list[str]) -> dict:
    return {"include": [{"target": module} for module in modules]}


def main() -> None:
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("GITHUB_SHA", "HEAD")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    full = event == "workflow_dispatch" and os.environ.get("CI_PROFILE") == "full"
    if event == "workflow_dispatch" or not base or set(base) == {"0"}:
        modules = list(ALL)
    else:
        changed = subprocess.check_output(["git", "diff", "--name-only", base, head], cwd=ROOT, text=True).splitlines()
        modules = select_modules(changed)
    browsers = [name for name in modules if name in WEB_CLIENTS]
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("modules=" + json.dumps(modules, separators=(",", ":")) + "\n")
            stream.write("matrix=" + json.dumps(component_matrix(modules), separators=(",", ":")) + "\n")
            stream.write("browsers=" + json.dumps(browsers, separators=(",", ":")) + "\n")
            stream.write("full=" + str(full).lower() + "\n")
    print(json.dumps({"selected": modules, "profile": "full" if full else "standard", "real_model_calls": False}))


if __name__ == "__main__":
    main()
