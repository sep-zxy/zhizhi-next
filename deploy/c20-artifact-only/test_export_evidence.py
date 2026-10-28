"""Host export safety tests on synthetic temporary files; no container or build."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("c20_export", Path(__file__).with_name("export_evidence.py"))
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)


class ExportTests(unittest.TestCase):
    def setup_tree(self, root, cleaned=True):
        (root / "host-evidence").mkdir(); (root / "result").mkdir()
        (root / "host-evidence/execution-state.json").write_text(json.dumps({"cleanup_verified": cleaned}))
        (root / "result/public.html").write_text("synthetic public")

    def test_clean_result_export_is_new_and_does_not_claim_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.setup_tree(root)
            e.export(root)
            self.assertEqual((root / "evidence-export/result/public.html").read_text(), "synthetic public")
            receipt = json.loads((root / "evidence-export/export-status.json").read_text())
            self.assertTrue(receipt["payload_result_exported"])
            self.assertFalse(receipt["acceptance_verified"])
            with self.assertRaises(ValueError): e.export(root)

    def test_uncertain_cleanup_exports_host_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.setup_tree(root, cleaned=False)
            (root / "result/leak").symlink_to("/etc/passwd")
            e.export(root)
            self.assertFalse((root / "evidence-export/result").exists())
            self.assertFalse(json.loads((root / "evidence-export/export-status.json").read_text())["payload_result_exported"])

    def test_symlink_hardlink_and_fifo_are_rejected(self):
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); self.setup_tree(root)
                target = root / "result/unsafe"
                if kind == "symlink": target.symlink_to("/etc/passwd")
                elif kind == "hardlink": os.link(root / "result/public.html", target)
                else: os.mkfifo(target)
                with self.assertRaises(ValueError): e.export(root)
                self.assertFalse((root / "evidence-export/result/unsafe").exists())

    def test_root_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.setup_tree(root)
            (root / "link").symlink_to(root)
            with self.assertRaises(ValueError): e.export(root / "link")

    def test_entry_and_byte_budgets_fail_closed(self):
        for bound, value in (("MAX_FILES", 1), ("MAX_BYTES", 1)):
            with self.subTest(bound=bound), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); self.setup_tree(root)
                with patch.object(e, bound, value), self.assertRaises(ValueError): e.export(root)


if __name__ == "__main__": unittest.main()
