"""Select affected component owners; always keep a repository check and final verdict."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ALL = ["backend", "learning-web", "wiki-private", "desktop", "public-wiki", "pi-zhizhi", "vault-manager", "continuity-widget", "obsidian-ai-writer"]
OWNERS = {
    "backend/": {"backend"},
    "apps/learning-web/": {"learning-web"},
    "apps/wiki-private/": {"wiki-private"},
    "apps/desktop/": {"desktop"},
    "apps/public-wiki/": {"public-wiki"},
    "integrations/pi-zhizhi/": {"pi-zhizhi"},
    "optional/vault-manager/": {"vault-manager"},
    "optional/continuity-widget/": {"continuity-widget"},
    "optional/obsidian-ai-writer/": {"obsidian-ai-writer"},
    "knowledge-contracts/": {"backend", "wiki-private", "public-wiki", "vault-manager", "continuity-widget"},
    "tests/": {"backend"},
}
DOCUMENT_NAMES = {"README.md", "AGENTS.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "LICENSE", "LICENSE.md"}


def select_modules(changed: list[str]) -> list[str]:
    """Select owners, not every client; shared knowledge contracts keep consumer coverage."""
    selected: set[str] = set()
    for name in changed:
        path = PurePosixPath(name)
        if path.name in DOCUMENT_NAMES or path.parts[0] in {"docs", ".agents", "archive"} or "docs" in path.parts:
            continue
        if name.startswith(("scripts/", ".github/", "config/")):
            selected.update(ALL)
        for prefix, owners in OWNERS.items():
            if name.startswith(prefix):
                selected.update(owners)
    return [name for name in ALL if name in selected]


def component_matrix(modules: list[str], full: bool = False) -> dict:
    return {"include": [
        {"target": module, "os": runner}
        for module in modules
        for runner in (["ubuntu-24.04", "windows-latest", "macos-15-intel"] if full and module == "backend" else ["ubuntu-24.04"])
    ]}


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
    browsers = [name for name in modules if name in {"learning-web", "wiki-private"}]
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("modules=" + json.dumps(modules, separators=(",", ":")) + "\n")
            stream.write("matrix=" + json.dumps(component_matrix(modules, full), separators=(",", ":")) + "\n")
            stream.write("browsers=" + json.dumps(browsers, separators=(",", ":")) + "\n")
            stream.write("full=" + str(full).lower() + "\n")
    print(json.dumps({"selected": modules, "profile": "full" if full else "standard", "real_model_calls": False}))


if __name__ == "__main__":
    main()
