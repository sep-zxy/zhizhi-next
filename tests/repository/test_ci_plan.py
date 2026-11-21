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
    (["apps/desktop/main.mjs", "apps/public-wiki/site/quartz.config.ts", "optional/obsidian-ai-writer/src/main.ts"], []),
    (["archive/import/provenance.json", "deploy/windows-portable/runtime/launcher.py", "config/examples/channels.example.json"], []),
    (["tests/repository/test_design_coverage_matrix.py", "tests/e2e/test_desktop_configuration.py"], []),
    (["backend/src/ahadiff/serve.py"], PLAN.ALL),
    (["deploy/knowledge-platform/service.env"], PLAN.ALL),
    (["integrations/pi-zhizhi/src/index.ts"], ["pi-zhizhi"]),
    (["backend/src/ahadiff/prompts/system.md"], PLAN.ALL),
    (["knowledge-contracts/schema.json"], PLAN.ALL),
    (["tests/e2e/test_knowledge_flow.py"], ["backend"]),
    (["config/toolchains.json"], PLAN.ALL),
    ([".github/workflows/ci.yml"], PLAN.ALL),
])
def test_changed_paths_select_their_actual_owners(changed, expected):
    assert PLAN.select_modules(changed) == expected
