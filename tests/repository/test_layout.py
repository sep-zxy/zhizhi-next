"""Layout checks only; not live-account or runtime acceptance."""
import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


class LayoutTests(unittest.TestCase):
    def test_navigation(self):
        for target in re.findall(r"\]\(([^)]+)\)", (ROOT / "README.md").read_text(encoding="utf-8-sig")):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            self.assertTrue((ROOT / unquote(parsed.path)).exists(), target)

    def test_provenance(self):
        data = json.loads((ROOT / "archive/import/provenance.json").read_text(encoding="utf-8-sig"))
        self.assertRegex(data["restoration_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual({p["name"] for p in data["projects"]}, {"growth", "selfweb", "vault-manager", "continuity-widget", "obsidian-ai-writer"})

    def test_required_entrypoints(self):
        for path in ("backend/pyproject.toml", "apps/public-wiki/site/package.json", "integrations/pi-zhizhi/package.json", "optional/vault-manager/Cargo.toml", "optional/continuity-widget/Cargo.toml", "knowledge-contracts/SCHEMA.md", "optional/obsidian-ai-writer/package.json", "optional/obsidian-ai-writer/THIRD_PARTY_NOTICES.md"):
            self.assertTrue((ROOT / path).is_file(), path)

    def test_optional_internal_paths(self):
        base = ROOT / "optional/obsidian-ai-writer"
        for path in ("src/main.ts", "tests/fixtures/../../styles.css", "esbuild.config.mjs", "tsconfig.json"):
            self.assertTrue((base / path).is_file(), path)
        self.assertFalse((ROOT / "projects/obsidian-ai-writer").exists())

    def test_old_inventories_removed(self):
        for name in ("SOURCE_MANIFEST.json", "ACCEPTANCE_MANIFEST.json", "FAVICON_ASSET_MANIFEST.json", "SUPPLEMENT_MANIFEST.json", "ACCEPTANCE_FILE_LIST.txt", "SHA256SUMS", "SHA256SUMS_ACCEPTANCE", "SHA256SUMS_FAVICON", "SHA256SUMS_SUPPLEMENT"):
            self.assertFalse((ROOT / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
