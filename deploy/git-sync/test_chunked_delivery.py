"""Offline exact-byte restoration tests; does not execute delivered application."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


pack = load("chunk_pack", "prepare_chunked_delivery.py")
restore = load("chunk_restore", "restore_delivery.py")
REPO = Path(__file__).resolve().parents[2]


class ChunkTests(unittest.TestCase):
    def fixture(self, tmp):
        original = Path(tmp) / "original"
        original.mkdir()
        data = ("中文精确字节\r\n" * 30_000).encode()
        (original / "large.txt").write_bytes(data)
        pack.dump(original / "delivery.json", {"files": {"large.txt": pack.sha(data)}})
        output = Path(tmp) / "transport"
        pack.build(REPO, original, output)
        return original, output / "checkout"

    def test_exact_restore_and_idempotent_recheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, root = self.fixture(tmp)
            self.assertEqual(restore.restore(root)["status"], "delivery_restored_and_verified")
            self.assertEqual((root / "ready/large.txt").read_bytes(), (original / "large.txt").read_bytes())
            self.assertEqual((root / "ready/delivery.json").read_bytes(), (original / "delivery.json").read_bytes())
            self.assertEqual(restore.restore(root)["status"], "delivery_already_ready")
            for path in (root.parent / "requests").iterdir():
                self.assertLessEqual(len(path.read_bytes()), 131_072)

    def test_changed_chunk_refused_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, root = self.fixture(tmp)
            chunk = next((root / "objects").iterdir())
            chunk.write_bytes(chunk.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "Transport checksum"):
                restore.restore(root)
            self.assertFalse((root / "ready").exists())

    def test_existing_modified_result_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, root = self.fixture(tmp)
            restore.restore(root)
            (root / "ready/large.txt").write_bytes(b"user change")
            with self.assertRaises(ValueError):
                restore.restore(root)
            self.assertEqual((root / "ready/large.txt").read_bytes(), b"user change")

    def test_traversal_and_links_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("../outside", "C:/outside", "\\outside", "/outside"):
                with self.assertRaises(ValueError):
                    restore.safe(root, name)
            (root / "outside").mkdir()
            (root / "link").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaises(ValueError):
                restore.safe(root, "link/file")

    def test_decompression_size_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, root = self.fixture(tmp)
            plan = json.loads((root / "transport.json").read_text())
            plan["files"]["large.txt"]["bytes"] -= 1
            pack.dump(root / "transport.json", plan)
            with self.assertRaises(ValueError):
                restore.restore(root)
            self.assertFalse((root / "ready").exists())


if __name__ == "__main__":
    unittest.main()
