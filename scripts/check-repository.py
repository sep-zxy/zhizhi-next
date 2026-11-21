"""Check first-release repository metadata; no application or real account is started."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    toolchains = json.loads((ROOT / "config/toolchains.json").read_text(encoding="utf-8"))
    assert toolchains["schema_version"] == 1
    assert toolchains["python"].startswith("3.13.")
    assert toolchains["node"].startswith("22.")
    for name in ("backend", "apps", "integrations", "knowledge-contracts"):
        assert (ROOT / name).is_dir(), name
    assert not (ROOT / "projects/growth").exists()
    for relative in (
        "backend/pyproject.toml", "backend/uv.lock", "backend/LICENSE",
        "apps/learning-web/pnpm-lock.yaml", "apps/wiki-private/package-lock.json",
        "integrations/pi-zhizhi/package-lock.json",
    ):
        assert (ROOT / relative).is_file(), relative
    managers = {
        "apps/learning-web": "pnpm",
        "apps/wiki-private": "npm",
        "integrations/pi-zhizhi": "npm",
    }
    for relative, manager in managers.items():
        manifest = json.loads((ROOT / relative / "package.json").read_text(encoding="utf-8"))
        assert manifest["packageManager"] == f"{manager}@{toolchains[manager]}", relative
    examples = ROOT / "config/examples"
    for name in ("models.example.json", "platform.example.json"):
        example = examples / name
        value = json.loads(example.read_text(encoding="utf-8"))
        assert isinstance(value, dict), example
        assert not re.search(r'"(?:api_key|token|password)"\s*:\s*"[^"\s]+"', example.read_text(encoding="utf-8")), example
    maintained = [ROOT / name for name in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md")]
    maintained += list((ROOT / "docs").rglob("*.md"))
    excluded = ("archive/", "docs/migration/", "apps/desktop/", "apps/public-wiki/", "optional/", "deploy/windows-portable/")
    for document in maintained:
        if document.relative_to(ROOT).as_posix().startswith(excluded):
            continue
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^\s)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            destination = (document.parent / target.split("#", 1)[0]).resolve()
            if destination.is_relative_to(ROOT) and destination.relative_to(ROOT).as_posix().startswith(excluded):
                continue
            assert destination.exists(), f"{document.relative_to(ROOT)} -> {target}"
    workflows = {file.name for file in (ROOT / ".github/workflows").glob("*.yml")}
    assert workflows == {"ci.yml", "build.yml", "release.yml", "live-acceptance.yml"}, workflows
    print("Repository layout, maintained links, lockfile ownership and config examples passed")


if __name__ == "__main__":
    main()
