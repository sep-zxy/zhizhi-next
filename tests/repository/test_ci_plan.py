"""Keep routine changes scoped without losing shared-contract or full regression checks."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_plan", ROOT / "scripts/ci-plan.py")
assert SPEC and SPEC.loader
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)


@pytest.mark.parametrize(("changed", "expected"), [
    (["README.md", "docs/assets/vision/image.png", "backend/README.md", ".agents/notes/proposed/design.md"], []),
    (["apps/learning-web/src/App.tsx"], ["learning-web"]),
    (["apps/wiki-private/src/App.tsx"], ["wiki-private"]),
    (["apps/desktop/main.mjs"], ["desktop"]),
    (["backend/src/ahadiff/serve.py"], ["backend"]),
    (["integrations/pi-zhizhi/src/index.ts"], ["pi-zhizhi"]),
    (["backend/src/ahadiff/prompts/system.md"], ["backend"]),
    (["knowledge-contracts/schema.json"], ["backend", "wiki-private", "public-wiki", "vault-manager", "continuity-widget"]),
    (["config/toolchains.json"], PLAN.ALL),
    ([".github/workflows/ci.yml"], PLAN.ALL),
])
def test_changed_paths_select_their_actual_owners(changed, expected):
    assert PLAN.select_modules(changed) == expected
