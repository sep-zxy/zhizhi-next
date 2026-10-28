"""Configuration-only tests: no build, namespace creation or CI impersonation."""
import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("c20_prepare", Path(__file__).with_name("prepare_and_run.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class IsolationConfiguration(unittest.TestCase):
    def test_font_helper_can_import_sibling_and_restores_path(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules):
            sys.modules.pop("build_bundle", None)
            root = Path(temporary)
            helper = root / "deploy/windows-portable"
            helper.mkdir(parents=True)
            (helper / "build_bundle.py").write_text("RUNTIMES = {'fixture': True}\n", encoding="utf-8")
            (helper / "build_github_product.py").write_text(
                "from build_bundle import RUNTIMES\ndef fonts(repo, work):\n    return RUNTIMES\n", encoding="utf-8")
            original = sys.path[:]
            preparer = driver.load_font_preparer(root)
            self.assertEqual(preparer(root, root), {"fixture": True})
            self.assertEqual(sys.path, original)

    def test_font_helper_import_failure_also_restores_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "deploy/windows-portable"
            helper.mkdir(parents=True)
            (helper / "build_github_product.py").write_text("raise RuntimeError('fixture failure')\n", encoding="utf-8")
            original = sys.path[:]
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                driver.load_font_preparer(root)
            self.assertEqual(sys.path, original)

    def test_local_guard_rejects_without_setting_fake_ci(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GitHub-hosted"):
                driver.prepare(Path("/missing"), Path("/missing-output"), "a" * 40)

    def test_source_prefixes_exclude_repository_metadata(self):
        self.assertFalse(".git/config".startswith(driver.SOURCE_PREFIXES))
        self.assertFalse(".env".startswith(driver.SOURCE_PREFIXES))
        self.assertFalse("projects/growth/.local/secrets".startswith(driver.SOURCE_PREFIXES))

    def test_executor_is_explicit_and_has_no_host_execution_fallback(self):
        # Inspect configuration only; never launch Docker or fabricate CI.
        text = Path(driver.__file__).read_text()
        self.assertIn("docker_executor", text)
        self.assertNotIn('"bwrap"', text)
        self.assertIn('choices=("prepare", "execute")', text)
        self.assertNotIn('"GITHUB_TOKEN":', text)
        import ast
        tree = ast.parse(text)
        execute = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "execute")
        # A failed executor must propagate; no catch-and-run-on-host fallback.
        self.assertFalse(any(isinstance(item, ast.ExceptHandler) for item in ast.walk(execute)))

    def test_workflow_preserves_failures_and_always_summarizes_upload(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / ".github/workflows/c20-artifact-only.yml").read_text()
        self.assertNotIn("bubblewrap", text)
        self.assertNotIn("apt-get", text)
        self.assertNotIn("continue-on-error", text)
        self.assertIn("id: evidence_artifact", text)
        self.assertIn("steps.evidence_artifact.outcome", text)
        self.assertIn("id: isolated_execution", text)
        self.assertIn("steps.isolated_execution.outcome", text)
        self.assertIn('--source "$GITHUB_SHA" --run-id "$GITHUB_RUN_ID"', text)
        self.assertIn("c20-artifact-only/evidence-export/", text)
        self.assertNotIn("c20-artifact-only/result/", text)
        self.assertNotIn("c20-artifact-only/host-evidence/", text)
        self.assertIn("steps.evidence_export.outcome == 'success'", text)
        self.assertIn("docker_executor.py --cleanup", text)
        self.assertIn("export_evidence.py --work", text)
        self.assertIn('if-no-files-found: error', text)
        self.assertIn('summary.py --work', text)
        self.assertIn('--phase prepare', text)
        self.assertIn('--phase execute', text)
        summary_step = text.split("- name: Summarize bounded evidence", 1)[1]
        self.assertIn("if: always()", summary_step)

    def test_no_publish_permissions_or_model_steps(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / ".github/workflows/c20-artifact-only.yml").read_text()
        self.assertIn("contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("deploy-pages", text)
        self.assertNotIn("publish_candidate", text)
        self.assertIn('"$EXPECTED_SOURCE" == "$GITHUB_SHA"', text)


if __name__ == "__main__":
    unittest.main()
