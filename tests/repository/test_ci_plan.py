"""Keep the first-release CI scope covered without installing project dependencies."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_plan", ROOT / "scripts/ci-plan.py")
assert SPEC and SPEC.loader
PLAN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLAN)


class ScopeTests(unittest.TestCase):
    def test_reference_changes_only_need_metadata(self):
        for path in (
            "README.md", "docs/testing.md", "backend/README.md", ".agents/notes/proposed/design.md",
            "archive/import/provenance.json", "apps/desktop/main.mjs", "apps/public-wiki/site/quartz.config.ts",
            "optional/obsidian-ai-writer/src/main.ts", "deploy/windows-portable/runtime/launcher.py",
            "config/examples/channels.example.json", "tests/repository/test_design_coverage_matrix.py",
            "tests/e2e/test_desktop_configuration.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(PLAN.needs_product_checks([path]))

    def test_product_and_ci_changes_use_all_product_checks(self):
        for path in (
            "backend/src/ahadiff/serve.py", "backend/src/ahadiff/prompts/system.md",
            "apps/learning-web/src/App.tsx", "apps/wiki-private/src/App.tsx",
            "deploy/knowledge-platform/service.env", "integrations/pi-zhizhi/src/index.ts",
            "knowledge-contracts/schema.json", ".github/workflows/ci.yml",
            "config/toolchains.json", "tests/e2e/test_knowledge_flow.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(PLAN.needs_product_checks(["archive/notes.md", path]))


if __name__ == "__main__":
    unittest.main()
