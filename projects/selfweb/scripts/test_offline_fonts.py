"""Synthetic security fixtures for the network-free font asset verifier."""
import hashlib
import json
import pathlib
import runpy
import tempfile
import unittest
import zipfile

prepare = runpy.run_path(str(pathlib.Path(__file__).with_name("offline-fonts.py")))["prepare"]


class OfflineFontsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.manifest_path = self.root / "fonts-manifest.json"
        self.license = b"synthetic license fixture"
        self.font = b"synthetic font fixture"
        self.manifest = {"format_version": 1, "fonts": [{"cache_name": "Fixture-400", "archive_path": "fonts/Fixture-400.ttf", "bytes": len(self.font), "sha256": hashlib.sha256(self.font).hexdigest()}], "license": {"bytes": len(self.license), "sha256": hashlib.sha256(self.license).hexdigest()}}
        self.manifest_path.write_text(json.dumps(self.manifest))
        (self.root / "NotoSansSC-OFL.txt").write_bytes(self.license)
        self.archive = self.root / "fonts.zip"
        self.site = self.root / "site"

    def bundle(self, extra=False, altered=False):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("manifest.json", json.dumps(self.manifest))
            archive.writestr("OFL.txt", self.license)
            archive.writestr("fonts/Fixture-400.ttf", b"x" * len(self.font) if altered else self.font)
            if extra:
                archive.writestr("../escape.ttf", self.font)

    def test_restore_and_verify_without_network(self):
        self.bundle()
        self.assertEqual(prepare(self.site, self.manifest_path, self.archive), {"fonts_verified": 1, "network_used": False})
        self.assertEqual(prepare(self.site, self.manifest_path)["fonts_verified"], 1)

    def test_missing_cache_fails(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            prepare(self.site, self.manifest_path)

    def test_mutated_cache_fails(self):
        self.bundle()
        prepare(self.site, self.manifest_path, self.archive)
        (self.site / "quartz/.quartz-cache/fonts/Fixture-400").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "differs"):
            prepare(self.site, self.manifest_path)

    def test_extra_traversal_path_fails_before_writing(self):
        self.bundle(extra=True)
        with self.assertRaisesRegex(ValueError, "paths"):
            prepare(self.site, self.manifest_path, self.archive)
        self.assertFalse(self.site.exists())

    def test_changed_font_fails_before_writing(self):
        self.bundle(altered=True)
        with self.assertRaisesRegex(ValueError, "hash"):
            prepare(self.site, self.manifest_path, self.archive)
        self.assertFalse(self.site.exists())

    def test_changed_license_fails(self):
        (self.root / "NotoSansSC-OFL.txt").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "license"):
            prepare(self.site, self.manifest_path)

    def test_symlink_cache_directory_fails(self):
        self.bundle()
        self.site.mkdir()
        (self.site / "quartz").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            prepare(self.site, self.manifest_path, self.archive)


if __name__ == "__main__":
    unittest.main()
