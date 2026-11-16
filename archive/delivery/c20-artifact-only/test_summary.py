"""Summary privacy and strict status tests; no executor/build/network calls."""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("c20_summary", Path(__file__).with_name("summary.py"))
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)

SOURCE = "a" * 40
SECRET = "ARBITRARY_FIXTURE_BODY_DO_NOT_LOG_!"


def good_records():
    receipt = {"status": "verified_site_build", "site_build_executed": True,
               "deploy_executed": False, "isolation_attested": False, "artifact_hash": "b" * 64}
    return {
        "scope.json": {"source_commit": SOURCE, "run_id": "12345", "github_hosted": True,
                       "live_model": "not_run", "live_channels": "not_run",
                       "windows_e2e": "not_run", "deployment": "not_run"},
        "isolation.json": {"source_commit": SOURCE, "executor": "docker-network-none",
                           "isolation_verified": True, "network_none": True, "read_only_root": True,
                           "nonroot": True, "capabilities_dropped": True, "no_new_privileges": True},
        "docker-host-inspect.json": {"source_commit": SOURCE, "run_id": "12345", "image_id": summary.IMAGE_ID,
                                     "host_configuration_verified": True, "host_execution_verified": True},
        "acceptance/acceptance-receipt.json": {
            "source_commit": SOURCE, "status": "synthetic_real_build_verified",
            "double_build_identical": True, "cache_reset_before_each_successful_publication_build": True,
            "leak_scan_negative_gate": True, "tamper_negative_gate": True, "revocation_negative_gate": True,
            "deploy_executed": False, "live_model": "not_run", "live_user_data": "not_run",
            "windows_e2e": "not_run", "product_receipts": [copy.deepcopy(receipt), copy.deepcopy(receipt)],
            "positive_control_receipts": [copy.deepcopy(receipt), copy.deepcopy(receipt)],
            "artifact_files": {"index.html": "c" * 64},
        },
    }


def save(root, records):
    for relative, value in records.items():
        path = root / ("host-evidence" if relative == "docker-host-inspect.json" else "result") / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_complete_evidence_and_upload_succeed_with_bounded_label(self):
        save(self.root, good_records())
        text, verified = summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")
        self.assertTrue(verified)
        self.assertIn("bounded synthetic scope only", text)
        self.assertIn("No user-data, model, Windows or deployment E2E claim", text)

    def test_missing_files_never_verify(self):
        for missing in good_records():
            with self.subTest(missing=missing):
                records = good_records()
                del records[missing]
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    save(root, records)
                    text, verified = summary.summarize(root, "success", "success", SOURCE, "12345", "success", "success")
                    self.assertFalse(verified)
                    self.assertIn("NOT VERIFIED", text)

    def test_file_presence_empty_objects_and_claimed_status_do_not_verify(self):
        save(self.root, {name: {"status": "passed"} for name in good_records()})
        self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_upload_failure_quota_cancel_skip_unknown_cannot_be_green(self):
        save(self.root, good_records())
        for outcome in ("failure", "cancelled", "skipped", "", SECRET):
            with self.subTest(outcome=outcome):
                text, verified = summary.summarize(self.root, outcome, "success", SOURCE, "12345", "success", "success")
                self.assertFalse(verified)
                self.assertNotIn(SECRET, text)
                self.assertIn("Artifact upload outcome", text)

    def test_arbitrary_fields_file_contents_and_filenames_are_not_logged(self):
        records = good_records()
        for record in records.values():
            record["raw_text"] = SECRET
        records["acceptance/acceptance-receipt.json"]["artifact_files"] = {SECRET: "c" * 64}
        records["isolation.json"]["mountinfo"] = SECRET
        save(self.root, records)
        (self.root / "result" / "fixture-private.txt").write_text(SECRET)
        text, verified = summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")
        self.assertTrue(verified)
        self.assertNotIn(SECRET, text)

    def test_invalid_identity_is_not_echoed(self):
        records = good_records()
        records["scope.json"].update(source_commit=SECRET, run_id=SECRET)
        save(self.root, records)
        text, verified = summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")
        self.assertFalse(verified)
        self.assertNotIn(SECRET, text)

    def test_malformed_json_and_symlink_are_rejected_without_content(self):
        save(self.root, good_records())
        file = self.root / "result/isolation.json"
        file.write_text(SECRET)
        text, verified = summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")
        self.assertFalse(verified)
        self.assertNotIn(SECRET, text)
        file.unlink()
        external = self.root / "external.json"
        external.write_text(json.dumps(good_records()["isolation.json"]))
        file.symlink_to(external)
        self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_gates_require_exact_boolean_not_truthy_strings(self):
        for field in ("double_build_identical", "leak_scan_negative_gate", "tamper_negative_gate", "revocation_negative_gate"):
            records = good_records()
            records["acceptance/acceptance-receipt.json"][field] = "true"
            save(self.root, records)
            self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_rejected_product_receipt_or_positive_control_never_verifies(self):
        for field, value in (("deploy_executed", True), ("isolation_attested", True),
                             ("site_build_executed", False), ("artifact_hash", "d" * 64)):
            records = good_records()
            records["acceptance/acceptance-receipt.json"]["positive_control_receipts"][0][field] = value
            save(self.root, records)
            self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_exact_source_binding_across_records(self):
        for file in ("isolation.json", "docker-host-inspect.json", "acceptance/acceptance-receipt.json"):
            records = good_records()
            records[file]["source_commit"] = "e" * 40
            save(self.root, records)
            self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_payload_self_report_without_successful_host_inspect_never_verifies(self):
        for field, value in (("host_configuration_verified", False), ("host_execution_verified", False),
                             ("host_execution_verified", "true"), ("image_id", "sha256:" + "0" * 64)):
            records = good_records()
            records["docker-host-inspect.json"][field] = value
            save(self.root, records)
            self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_isolation_requires_every_explicit_security_gate(self):
        for field in ("isolation_verified", "network_none", "read_only_root", "nonroot",
                      "capabilities_dropped", "no_new_privileges"):
            records = good_records()
            records["isolation.json"][field] = False
            save(self.root, records)
            self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_cli_preserves_failure_status_and_appends_only_safe_summary(self):
        save(self.root, good_records())
        output = self.root / "step-summary.md"
        for outcome, expected in (("failure", 1), ("success", 0)):
            stream = io.StringIO()
            with patch("sys.argv", ["summary.py", "--work", str(self.root),
                                    "--artifact-outcome", outcome, "--execution-outcome", "success",
                                    "--source", SOURCE, "--run-id", "12345", "--cleanup-outcome", "success",
                                    "--export-outcome", "success", "--step-summary", str(output)]), redirect_stdout(stream):
                self.assertEqual(summary.main(), expected)
            self.assertNotIn(SECRET, stream.getvalue())
        self.assertEqual(output.read_text().count("## C20 bounded synthetic verification"), 2)

    def test_forged_payload_host_evidence_is_ignored_and_execution_must_succeed(self):
        save(self.root, good_records())
        authoritative = self.root / "host-evidence/docker-host-inspect.json"
        forged = self.root / "result/docker-host-inspect.json"
        forged.write_bytes(authoritative.read_bytes())
        authoritative.unlink()
        self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])
        save(self.root, good_records())
        for outcome in ("failure", "cancelled", "skipped", "unknown", SECRET):
            text, verified = summary.summarize(self.root, "success", outcome, SOURCE, "12345", "success", "success")
            self.assertFalse(verified)
            self.assertNotIn(SECRET, text)

    def test_expected_identity_is_external_not_selected_by_payload_scope(self):
        records = good_records()
        for record in records.values():
            record["source_commit"] = "f" * 40
        records["scope.json"]["run_id"] = "999"
        records["docker-host-inspect.json"]["run_id"] = "999"
        save(self.root, records)
        text, verified = summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")
        self.assertFalse(verified)
        self.assertIn(SOURCE, text)
        self.assertNotIn("f" * 40, text)

    def test_run_drift_in_scope_or_host_is_rejected(self):
        for name in ("scope.json", "docker-host-inspect.json"):
            records = good_records()
            records[name]["run_id"] = "999"
            save(self.root, records)
            self.assertFalse(summary.summarize(self.root, "success", "success", SOURCE, "12345", "success", "success")[1])

    def test_cleanup_and_export_must_both_succeed(self):
        save(self.root, good_records())
        for cleanup, exported in (("failure", "success"), ("success", "failure"),
                                  ("skipped", "success"), ("success", SECRET)):
            text, verified = summary.summarize(self.root, "success", "success", SOURCE, "12345", cleanup, exported)
            self.assertFalse(verified)
            self.assertNotIn(SECRET, text)

    def test_hardlink_fifo_and_symlink_ancestors_are_never_read(self):
        save(self.root, good_records())
        path = self.root / "result/isolation.json"
        external = self.root / "host-secret.json"
        external.write_text(SECRET)
        path.unlink()
        os.link(external, path)
        self.assertIsNone(summary.read_record(self.root, "result/isolation.json"))
        path.unlink()
        os.mkfifo(path)
        self.assertIsNone(summary.read_record(self.root, "result/isolation.json"))
        path.unlink()
        path.symlink_to(external)
        with patch.object(summary.os, "read", side_effect=AssertionError("Secret bytes must not be read")):
            self.assertIsNone(summary.read_record(self.root, "result/isolation.json"))
        alias = self.root / "linked-root"
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(summary.os, "read", side_effect=AssertionError("Symlink ancestor must not be traversed")):
            self.assertIsNone(summary.read_record(alias, "result/scope.json"))


if __name__ == "__main__":
    unittest.main()
