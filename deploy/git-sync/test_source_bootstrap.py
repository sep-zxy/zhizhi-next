"""Offline source/bootstrap gates. No GitHub writes, credentials, or model calls."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "source_bootstrap", Path(__file__).with_name("source_bootstrap.py"))
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def run(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE).decode().strip()


def init(root):
    root.mkdir()
    run(root, "init", "-b", "main")
    run(root, "config", "user.name", "Offline fixture")
    run(root, "config", "user.email", "fixture@example.invalid")


def commit(root, files):
    for name, (_, data) in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if files[name][0] == "100755" else 0o644)
    run(root, "add", ".")
    run(root, "commit", "-m", "离线测试样本")
    return run(root, "rev-parse", "HEAD")


def prepare(root, files):
    provenance = {
        "source_commit": "a" * 40, "original_git_tree": bootstrap.tree_digest(files),
        "restored_git_tree": bootstrap.tree_digest(files),
        "excluded_nonproduct_paths": [], "history_included": False,
    }
    output = root / "transport"
    plan = bootstrap.prepare_files(files, provenance, output)
    return output, plan


class BootstrapTests(unittest.TestCase):
    def test_git_tree_exact_with_sorting_modes_unicode_and_empty(self):
        files = {"foo.bar": ("100644", b"x\r\n"),
                 "foo/z": ("100755", b"#!/bin/sh\ntrue\n"),
                 "中文/empty": ("100644", b"")}
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            init(repo)
            commit(repo, files)
            self.assertEqual(bootstrap.tree_digest(files), run(repo, "rev-parse", "HEAD^{tree}"))

    def test_complete_roundtrip_and_request_budget(self):
        files = {"source.py": ("100644", b"print('source')\n"),
                 "font.ttf": ("100644", bytes(range(256)) * 1000),
                 "run": ("100755", b"#!/bin/sh\ntrue\n"),
                 "empty": ("100644", b"")}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport, plan = prepare(root, files)
            restored = root / "restored"
            record = bootstrap.restore(transport / "checkout", plan["transport_sha256"], restored)
            self.assertEqual(record["restored_git_tree"], bootstrap.tree_digest(files))
            for name, (mode, data) in files.items():
                self.assertEqual((restored / name).read_bytes(), data)
                self.assertEqual((restored / name).stat().st_mode & 0o777, int(mode[-3:], 8))
            self.assertLessEqual(plan["maximum_request_bytes"], 131_072)
            for request in plan["requests"]:
                self.assertLessEqual((transport / request["file"]).stat().st_size, 131_072)

    def test_corrupt_chunk_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport, plan = prepare(root, {"a": ("100644", b"fixed")})
            obj = next((transport / "checkout" / "objects").iterdir())
            obj.write_bytes(obj.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "integrity"):
                bootstrap.restore(transport / "checkout", plan["transport_sha256"], root / "out")
            self.assertFalse((root / "out").exists())

    def test_wrong_external_transport_hash_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport, _ = prepare(root, {"a": ("100644", b"x")})
            with self.assertRaisesRegex(ValueError, "Unapproved"):
                bootstrap.restore(transport / "checkout", "0" * 64, root / "out")

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport, plan = prepare(root, {"a": ("100644", b"x")})
            out = root / "out"
            out.mkdir()
            with self.assertRaisesRegex(ValueError, "overwrite"):
                bootstrap.restore(transport / "checkout", plan["transport_sha256"], out)

    def test_symlink_chunk_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport, plan = prepare(root, {"a": ("100644", b"x")})
            obj = next((transport / "checkout" / "objects").iterdir())
            replacement = root / "replacement"
            obj.rename(replacement)
            obj.symlink_to(replacement)
            with self.assertRaisesRegex(ValueError, "Links"):
                bootstrap.restore(transport / "checkout", plan["transport_sha256"], root / "out")

    def test_traversal_reserved_and_control_paths(self):
        for name in ("../x", "/x", "./x", "x\\y", "C:/x", ".git/config", "x/.GiT/a", "x\nfoo", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                bootstrap.relative(name)

    def test_duplicate_json_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            bootstrap.load_json(b'{"a": 1, "a": 2}')

    def test_bad_mode_or_path_collision(self):
        with self.assertRaises(ValueError):
            bootstrap.tree_digest({"link": ("120000", b"outside")})
        with self.assertRaises(ValueError):
            bootstrap.tree_digest({"a": ("100644", b"x"), "a/b": ("100644", b"y")})

    def test_case_collision_fails_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport, plan = prepare(root, {"A": ("100644", b"a"), "a": ("100644", b"a")})
            with self.assertRaisesRegex(ValueError, "Case-insensitive"):
                bootstrap.restore(transport / "checkout", plan["transport_sha256"], root / "out")

    def test_size_boundary_fails_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bootstrap, "MAX_UNPACKED", 10):
            with self.assertRaisesRegex(ValueError, "size boundary"):
                prepare(Path(tmp), {"a": ("100644", b"x" * 20)})
            self.assertFalse((Path(tmp) / "transport").exists())

    def test_audit_fixed_commit_excludes_design_not_dirty_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            init(root)
            files = {"deploy/git-sync/reviewed-fixtures.json": ("100644", b"{}"),
                     "deploy/git-sync/reviewed-source-assets.json": ("100644", b"{}"),
                     "source.py": ("100644", b"original\n"),
                     "design-output/design.png": ("100644", b"\0not source")}
            ref = commit(root, files)
            (root / "source.py").write_text("uncommitted draft")
            snapshot, provenance = bootstrap.audit_snapshot(root, ref)
            self.assertEqual(snapshot["source.py"][1], b"original\n")
            self.assertNotIn("design-output/design.png", snapshot)
            self.assertEqual(provenance["excluded_nonproduct_paths"], ["design-output/design.png"])

    def test_unreviewed_binary_and_private_path_fail_closed(self):
        for name, data in (("unknown.bin", b"\0binary"), ("vault/private.md", b"private")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "repo"
                init(root)
                ref = commit(root, {
                    "deploy/git-sync/reviewed-fixtures.json": ("100644", b"{}"),
                    "deploy/git-sync/reviewed-source-assets.json": ("100644", b"{}"),
                    name: ("100644", data)})
                with self.assertRaisesRegex(ValueError, "blocked"):
                    bootstrap.audit_snapshot(root, ref)

    def test_workflow_mismatch_rejected_without_new_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init(repo)
            head = commit(repo, {".github/workflows/ci.yml": ("100644", b"name: old\n")})
            transport, plan = prepare(root, {".github/workflows/ci.yml": ("100644", b"name: new\n")})
            manifest = bootstrap.restore(transport / "checkout", plan["transport_sha256"], root / "restored")
            with self.assertRaisesRegex(ValueError, "Seed exact workflow"):
                bootstrap.commit_restored(repo, root / "restored", manifest, head)
            self.assertEqual(run(repo, "rev-parse", "HEAD"), head)

    def test_local_commit_preserves_parent_and_exact_tree_without_moving_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            init(repo)
            workflow = ("100644", b"name: identical\n")
            head = commit(repo, {".github/workflows/ci.yml": workflow, "README.md": ("100644", b"seed")})
            files = {".github/workflows/ci.yml": workflow, "app.py": ("100644", b"source\n")}
            transport, plan = prepare(root, files)
            manifest = bootstrap.restore(transport / "checkout", plan["transport_sha256"], root / "restored")
            new = bootstrap.commit_restored(repo, root / "restored", manifest, head)
            self.assertEqual(run(repo, "rev-parse", new + "^"), head)
            self.assertEqual(run(repo, "rev-parse", new + "^{tree}"), bootstrap.tree_digest(files))
            self.assertEqual(run(repo, "rev-parse", "HEAD"), head)
            with self.assertRaisesRegex(ValueError, "approved main"):
                bootstrap.commit_restored(repo, root / "restored", manifest, "a" * 40)

    def test_publish_refuses_local_environment(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "manual GitHub"):
            bootstrap.publish(Path("."), Path("."), {}, "a" * 40)


if __name__ == "__main__":
    unittest.main()
