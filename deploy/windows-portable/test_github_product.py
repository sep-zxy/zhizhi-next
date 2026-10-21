"""Static/offline orchestrator checks, not a GitHub or Windows build."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("github_product", HERE / "build_github_product.py")
product = importlib.util.module_from_spec(spec)
spec.loader.exec_module(product)


class GitHubProductTests(unittest.TestCase):
    def test_no_local_build_or_identity_spoofing_fallback(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "GitHub-hosted"):
            product.github_identity(HERE)

    def test_no_unapproved_download_origin(self):
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            product.read_official("https://example.invalid/not-an-official-asset", 1000)

    def test_pytest_not_unittest_only_for_mixed_test_suites(self):
        text = (HERE / "build_github_product.py").read_text()
        self.assertIn('python("-m", "pytest", "deploy/windows-portable")', text)
        self.assertIn('python("-m", "pytest", "deploy/git-sync")', text)

    def test_required_native_and_licensed_assets_are_kept(self):
        text = (HERE / "build_github_product.py").read_text()
        self.assertIn("fonts-manifest.json", text)
        self.assertIn("NotoSansSC-OFL.txt", text)
        self.assertIn('sorted(vendored["plugins"])', text)
        self.assertIn("@img/sharp-win32-x64@0.34.5", text)
        self.assertIn("@napi-rs/simple-git-win32-x64-msvc@0.1.22", text)
        self.assertIn("wiki-dynamic-fixture-gates", text)

    def test_private_wiki_is_independent_required_build(self):
        text = (HERE / "build_github_product.py").read_text()
        for phase in ("private-wiki-locked-install", "private-wiki-types",
                      "private-wiki-tests", "private-wiki-production-build",
                      "private-wiki-domain-capture", "private-wiki-domain-contract",
                      "private-wiki-browser-install", "private-wiki-browser-fixtures"):
            self.assertIn(phase, text)
        self.assertIn('"--private-wiki", private_wiki / "dist"', text)
        assembly = (HERE / "assemble_product.py").read_text()
        self.assertIn("app/'wiki-private'", assembly)
        self.assertIn("'http':'/wiki/'", assembly)

    def test_template_fixtures_are_distinct_synthetic_projections(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = product.template_fixtures(
                HERE.resolve().parents[1], Path(temporary), "f" * 40)
            self.assertNotEqual((first / "architecture/template.md").read_bytes(),
                                (second / "architecture/template.md").read_bytes())
            for path in (first, second):
                manifest = json.loads((path / "publish-manifest.json").read_text())
                self.assertEqual(manifest["counts"]["content_pages"], 1)
                self.assertEqual(manifest["counts"]["source_index_pages"], 0)
                self.assertEqual(manifest["revision"], "f" * 40)
                self.assertFalse(manifest["requires_domain_revalidation"])
                self.assertTrue((path / "index.md").is_file())


if __name__ == "__main__":
    unittest.main()
