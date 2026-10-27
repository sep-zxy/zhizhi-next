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

    def test_sandbox_has_no_host_root_home_or_socket_mount(self):
        # This inspects code, not a fabricated runner environment or execution.
        text = Path(driver.__file__).read_text()
        for flag in ("--unshare-all", "--clearenv", "--die-with-parent", "--new-session"):
            self.assertIn(flag, text)
        self.assertNotIn('"--ro-bind", "/", "/"', text)
        self.assertNotIn('"--bind", "/home"', text)
        self.assertNotIn('"--bind", "/run"', text)
        self.assertIn('os.environ["GITHUB_ACTIONS"]', text)
        self.assertIn('os.environ["RUNNER_ENVIRONMENT"]', text)
        self.assertNotIn('"GITHUB_TOKEN":', text)

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
