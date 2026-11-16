"""Select affected component owners; always keep a repository check and final verdict."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALL = ["backend", "learning-web", "wiki-private", "desktop", "public-wiki", "pi-zhizhi", "vault-manager", "continuity-widget", "obsidian-ai-writer"]
OWNERS = {
    "backend/": {"backend", "learning-web", "wiki-private", "pi-zhizhi", "desktop"},
    "apps/learning-web/": {"learning-web", "backend", "desktop"},
    "apps/wiki-private/": {"wiki-private", "backend"},
    "apps/desktop/": {"desktop", "backend", "learning-web"},
    "apps/public-wiki/": {"public-wiki", "backend"},
    "integrations/pi-zhizhi/": {"pi-zhizhi", "backend"},
    "optional/vault-manager/": {"vault-manager"},
    "optional/continuity-widget/": {"continuity-widget"},
    "optional/obsidian-ai-writer/": {"obsidian-ai-writer"},
    "knowledge-contracts/": {"backend", "wiki-private", "public-wiki", "vault-manager", "continuity-widget"},
    "tests/": {"backend", "pi-zhizhi"},
}


def component_matrix(modules: list[str]) -> dict:
    return {"include": [
        {"target": module, "os": runner}
        for module in modules
        for runner in (["ubuntu-24.04", "windows-latest", "macos-15-intel"] if module == "backend" else ["ubuntu-24.04"])
    ]}


def main() -> None:
    base = os.environ.get("BASE_SHA", "")
    head = os.environ.get("GITHUB_SHA", "HEAD")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch" or not base or set(base) == {"0"}:
        selected = set(ALL)
    else:
        changed = subprocess.check_output(["git", "diff", "--name-only", base, head], cwd=ROOT, text=True).splitlines()
        selected: set[str] = set()
        for name in changed:
            if name.startswith(("scripts/", ".github/", "config/")) or name == "AGENTS.md":
                selected.update(ALL)
            for prefix, owners in OWNERS.items():
                if name.startswith(prefix):
                    selected.update(owners)
    modules = [name for name in ALL if name in selected]
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("modules=" + json.dumps(modules, separators=(",", ":")) + "\n")
            stream.write("matrix=" + json.dumps(component_matrix(modules), separators=(",", ":")) + "\n")
    print(json.dumps({"selected": modules, "real_model_calls": False}))


if __name__ == "__main__":
    main()
