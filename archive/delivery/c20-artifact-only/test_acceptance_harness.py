"""Stdlib harness tests only; no Quartz, hosted-runner or E2E claim."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("c20_acceptance", Path(__file__).with_name("acceptance.py"))
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class HarnessTests(unittest.TestCase):
    def test_cache_reset_removes_build_state_preserves_seed_and_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, seed = root / "cache", root / "seed"
            cache.mkdir(); seed.mkdir()
            (seed / "font").write_text("verified font")
            (cache / "old-build").write_text("old")
            inode = cache.stat().st_ino
            acceptance.reset_cache(cache, seed)
            self.assertEqual(cache.stat().st_ino, inode)
            self.assertEqual(acceptance.tree_hashes(cache), acceptance.tree_hashes(seed))
            self.assertFalse((cache / "old-build").exists())

    def test_cache_reset_rejects_link_before_deleting_anything(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, seed = root / "cache", root / "seed"
            cache.mkdir(); seed.mkdir()
            (cache / "keep").write_text("keep")
            (cache / "redirect").symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                acceptance.reset_cache(cache, seed)
            self.assertEqual((cache / "keep").read_text(), "keep")

    def test_tree_hash_detects_bytes_and_ignores_creation_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("a", "b"):
                (root / name).mkdir()
            for name in ("z.html", "index.html"):
                (root / "a" / name).write_text(name)
            for name in ("index.html", "z.html"):
                (root / "b" / name).write_text(name)
            self.assertEqual(acceptance.tree_hashes(root / "a"), acceptance.tree_hashes(root / "b"))
            (root / "b/index.html").write_text("changed")
            self.assertNotEqual(acceptance.tree_hashes(root / "a"), acceptance.tree_hashes(root / "b"))

    def test_tree_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "link").symlink_to("absent")
            with self.assertRaises(ValueError):
                acceptance.tree_hashes(root)

    def test_negative_gate_rejects_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(AssertionError):
                acceptance.rejected(lambda: None, Path(temporary) / "absent", ValueError, "expected")

    def test_negative_gate_rejects_residual_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def failed():
                raise ValueError("expected")
            self.assertTrue(acceptance.rejected(failed, root / "absent", ValueError, "expected"))
            with self.assertRaises(ValueError):
                acceptance.rejected(failed, root, ValueError, "expected")

    def test_negative_gate_rejects_unrelated_error_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            def failed():
                raise ValueError("QUARTZ_BUILD_FAILED")
            with self.assertRaisesRegex(AssertionError, "unexpected reason"):
                acceptance.rejected(failed, Path(temporary) / "absent", ValueError, "DOMAIN_PROJECTION_CHANGED")

    def test_negative_gate_rejects_wrong_exception_type_even_same_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            class OtherError(ValueError):
                pass
            def failed():
                raise OtherError("expected")
            with self.assertRaisesRegex(AssertionError, "unexpected reason"):
                acceptance.rejected(failed, Path(temporary) / "absent", ValueError, "expected")


if __name__ == "__main__":
    unittest.main()
