"""Offline packaging safety tests; no provider, credentials or Windows execution."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


delivery = module("delivery", "prepare_git_delivery.py")
verify = module("verify", "verify_release.py")


class DeliveryTests(unittest.TestCase):
    def test_reject_private_paths(self):
        for path in (".env", ".env.production", ".codex/auth.json", "RAW/article.md",
                     "reports/e2e.json", "runtime/python.exe", "secrets/key.pem",
                     "projects/selfweb/site/content/private.md", "backup.bundle"):
            with self.subTest(path=path):
                self.assertFalse(delivery.source_path_allowed(path))

    def test_keep_required_source(self):
        for path in ("projects/growth/src/ahadiff/knowledge_platform/secrets.py",
                     "knowledge-contracts/templates/raw-article-template.md",
                     "deploy/windows-portable/runtime/launcher.py",
                     "deploy/windows-portable/runtime/windows_job.py", ".env.example"):
            with self.subTest(path=path):
                self.assertTrue(delivery.source_path_allowed(path))

    def test_reject_traversal(self):
        for path in ("../x", "/x", "x/../y", "C:/x", "x\\y", "./x", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                delivery.safe_relative(path)

    def test_unknown_secret_fails_closed(self):
        value = ("ghp_" + "abcdefghij" * 4).encode()
        self.assertEqual(delivery.scan("new.py", value, {}), ["github-token"])

    def test_binary_detection(self):
        self.assertTrue(delivery.is_binary(b"\0image"))
        self.assertTrue(delivery.is_binary(b"\xffimage"))
        self.assertFalse(delivery.is_binary("中文 source\n".encode()))

    def test_fixture_exception_is_exact_bytes_and_rules(self):
        value = ("ghp_" + "abcdefghij" * 4).encode()
        reviewed = {"fixture.py": {"sha256": delivery.digest(value), "rules": ["github-token"]}}
        self.assertEqual(delivery.scan("fixture.py", value, reviewed), [])
        self.assertEqual(delivery.scan("fixture.py", value + b"\n", reviewed), ["github-token"])
        self.assertEqual(delivery.scan("other.py", value, reviewed), ["github-token"])

    def test_export_preserves_text_binary_and_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            files = {"a.cmd": ("100644", b"@echo off\r\nexit /b 0\r\n"), "b.png": ("100644", b"\0\xff"),
                     "run.sh": ("100755", b"#!/bin/sh\nexit 0\n")}
            summary = delivery.export_requests(files, out)
            self.assertEqual(summary["files"], 3)
            self.assertEqual(len(summary["blob_requests"]), 1)
            batch = json.loads((out / "trees/000.json").read_text())["tree_elements"]
            self.assertEqual(batch[0]["content"].encode(), files["a.cmd"][1])
            self.assertEqual(batch[2]["mode"], "100755")
            self.assertFalse(summary["contains_git_history"])
            self.assertFalse(summary["remote_mutations_performed"])

    def test_no_overwrite_of_export(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            delivery.export_requests({}, Path(tmp))

    def test_archive_hash_and_path_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "test.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../bad.py", b"bad")
            with self.assertRaises(ValueError):
                delivery.read_archive(archive, "incorrect", "patch")
            with self.assertRaises(ValueError):
                delivery.read_archive(archive, delivery.digest(archive.read_bytes()), "patch")

    def test_archive_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "test.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("a", b"a")
                stream.writestr("a", b"b")
            with self.assertRaises(ValueError):
                delivery.read_archive(archive, delivery.digest(archive.read_bytes()), "patch")

    def test_release_verify_detects_tamper_and_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_bytes(b"fixed")
            delivery.dump(root / "delivery.json", {"schema_version": 1, "files": {"file.txt": delivery.digest(b"fixed")}})
            self.assertEqual(verify.verify(root), 1)
            (root / "extra").write_bytes(b"extra")
            with self.assertRaises(ValueError):
                verify.verify(root)
            (root / "extra").unlink()
            (root / "file.txt").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                verify.verify(root)

    def test_release_ignores_only_root_git_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            delivery.dump(root / "delivery.json", {"schema_version": 1, "files": {}})
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("metadata", encoding="utf-8")
            self.assertEqual(verify.verify(root), 0)
            (root / "nested" / ".git").mkdir(parents=True)
            (root / "nested" / ".git" / "unexpected.py").write_text("extra", encoding="utf-8")
            with self.assertRaises(ValueError):
                verify.verify(root)


if __name__ == "__main__":
    unittest.main()
