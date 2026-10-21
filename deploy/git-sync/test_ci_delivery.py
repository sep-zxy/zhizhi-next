"""Offline candidate tests. Synthetic files are not executable product evidence."""

from __future__ import annotations

import copy
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ci_delivery as ci
import verify_current as verifier


SOURCE = "c" * 40


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else ci.json_bytes(value))


def fixture(root):
    bundle = root / "bundle"
    receipt = {
        "schema_version": 1, "repository": ci.REPOSITORY, "source_commit": SOURCE,
        "run_id": "123", "run_attempt": "1", "workflow": "Offline fixture",
        "runner": "github-hosted", "event": "workflow_dispatch",
        "status": "ci_candidate_built", "final_delivery": False, "windows_e2e": "not_run",
        "phases": [{"phase": name, "status": "passed"} for name in sorted(ci.REQUIRED_PHASES)],
    }
    for name in ("start.cmd", "stop.cmd", "diagnostics.cmd", "app/launcher.py",
                 "app/viewer/index.html", "runtime/python/python.exe", "runtime/node/node.exe",
                 "runtime/git/cmd/git.exe", "app/backend/LICENSE",
                 "app/pi/component.json", "app/wiki/component.json", "app/wiki-private/index.html",
                 "codex-diagnostic.cmd", "app/backend/ahadiff/knowledge_platform/model/diagnostic_probe.py",
                 "local-test-initialize.cmd", "local-test-pair.cmd", "local-test-start.cmd",
                 "app/backend/ahadiff/knowledge_platform/local_test_identity.py",
                 "app/backend/ahadiff/knowledge_platform/browser_sessions.py"):
        write(bundle / name, b"SYNTHETIC OFFLINE TEST BYTES, NOT A PRODUCT")
    write(bundle / "config/launch.json", {
        "owner": None, "identities_file": None, "model_config": None,
        "desktop_config": None, "pi_environment_file": None,
        "providers": [], "run_worker": False, "auto_commit": False,
    })
    write(bundle / "provenance/assembly.json", {
        "assembly_source_commit": SOURCE, "backend_source_commit": SOURCE,
        "components": {name: {"source_commit": SOURCE} for name in ("pi", "wiki", "wiki-private")},
    })
    write(bundle / "provenance/github-build.json", receipt)
    distribution = {
        "schema_version": 1, "target": "windows-x64",
        "files": {path.relative_to(bundle).as_posix(): ci.sha(path)
                  for path in bundle.rglob("*")
                  if path.is_file() and path.relative_to(bundle).as_posix() != "config/launch.json"},
    }
    write(bundle / "provenance/distribution.json", distribution)
    receipt["distribution_sha256"] = ci.sha(bundle / "provenance/distribution.json")
    return bundle, receipt


class CandidateTests(unittest.TestCase):
    def test_pristine_stage_roundtrip_preserves_bytes_and_nonfinal_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            result = ci.stage(bundle, receipt, SOURCE, root / "stage")
            checked = verifier.verify(root / "stage")
            self.assertEqual(result["release_id"], "cccccccccccc-123-1")
            self.assertFalse(result["final_delivery"])
            self.assertFalse(checked["final_delivery"])
            self.assertEqual(checked["windows_e2e"], "not_established")
            for path in bundle.rglob("*"):
                if path.is_file():
                    copied = root / "stage/releases" / result["release_id"] / path.relative_to(bundle)
                    self.assertEqual(path.read_bytes(), copied.read_bytes())

    def test_missing_failed_or_duplicate_gate_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, original = fixture(Path(tmp))
            for kind in ("missing", "failed", "duplicate"):
                receipt = copy.deepcopy(original)
                if kind == "missing":
                    receipt["phases"].pop()
                elif kind == "failed":
                    receipt["phases"][0]["status"] = "failed"
                else:
                    receipt["phases"].append(receipt["phases"][0])
                with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "gate"):
                    ci.validate_bundle(bundle, receipt, SOURCE)

    def test_viewer_browser_install_cannot_be_missing_or_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, original = fixture(Path(tmp))
            self.assertIn("viewer-browser-install", ci.REQUIRED_PHASES)
            for status in (None, "failed"):
                receipt = copy.deepcopy(original)
                receipt["phases"] = [phase for phase in receipt["phases"]
                                     if phase["phase"] != "viewer-browser-install"]
                if status is not None:
                    receipt["phases"].append({"phase": "viewer-browser-install", "status": status})
                with self.subTest(status=status), self.assertRaisesRegex(ValueError, "gate"):
                    ci.validate_bundle(bundle, receipt, SOURCE)

    def test_runtime_private_directories_are_ignored_by_real_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            result = ci.stage(bundle, receipt, SOURCE, root / "stage")
            ci.validate_stage_files(root / "stage", result["release_id"])
            subprocess.run(["git", "init", "-q", str(root / "stage")], check=True)
            for directory in ci.PRIVATE_DIRECTORIES:
                for prefix in ("", "releases/" + result["release_id"] + "/"):
                    name = prefix + directory + "/nested/private.json"
                    checked = subprocess.run(
                        ["git", "check-ignore", "--no-index", name], cwd=root / "stage",
                        capture_output=True, text=True, check=True,
                    )
                    self.assertEqual(checked.stdout.strip(), name)
            visible = subprocess.run(
                ["git", "check-ignore", "--no-index",
                 "releases/" + result["release_id"] + "/app/launcher.py"],
                cwd=root / "stage", capture_output=True,
            )
            self.assertEqual(visible.returncode, 1)

    def test_sealed_release_cannot_publish_surrounding_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            result = ci.stage(bundle, receipt, SOURCE, root / "stage")
            for relative in ("data/local-test/identity.json", "reports/status.json", "extra.txt"):
                with self.subTest(relative=relative):
                    file = root / "stage" / relative
                    write(file, b"SYNTHETIC PRIVATE STATE")
                    with self.assertRaisesRegex(ValueError, "outside the sealed"):
                        ci.validate_stage_files(root / "stage", result["release_id"])
                    file.unlink()

    def test_old_cloud_artifact_cannot_claim_new_github_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, receipt = fixture(Path(tmp))
            receipt["runner"] = "isolated-cloud"
            with self.assertRaisesRegex(ValueError, "attributable"):
                ci.validate_bundle(bundle, receipt, SOURCE)

    def test_current_pointer_cannot_claim_final_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            ci.stage(bundle, receipt, SOURCE, root / "stage")
            current = root / "stage/current.json"
            pointer = json.loads(current.read_text())
            pointer["final_delivery"] = True
            write(current, pointer)
            with self.assertRaisesRegex(ValueError, "pointer provenance"):
                verifier.verify(root / "stage")

    def test_different_source_commit_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, receipt = fixture(Path(tmp))
            with self.assertRaisesRegex(ValueError, "attributable"):
                ci.validate_bundle(bundle, receipt, "b" * 40)

    def test_changed_payload_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, receipt = fixture(Path(tmp))
            (bundle / "app/launcher.py").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                ci.validate_bundle(bundle, receipt, SOURCE)

    def test_extra_private_state_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, receipt = fixture(Path(tmp))
            write(bundle / "data/private.txt", b"do not ship")
            with self.assertRaisesRegex(ValueError, "Private"):
                ci.validate_bundle(bundle, receipt, SOURCE)

    def test_configured_identity_refused_even_though_mutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, receipt = fixture(Path(tmp))
            config = json.loads((bundle / "config/launch.json").read_text())
            config["owner"] = "not-authorized-for-distribution"
            write(bundle / "config/launch.json", config)
            with self.assertRaisesRegex(ValueError, "credential-free"):
                ci.validate_bundle(bundle, receipt, SOURCE)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            path = bundle / "app/launcher.py"
            path.rename(root / "outside.py")
            path.symlink_to(root / "outside.py")
            with self.assertRaisesRegex(ValueError, "Links"):
                ci.validate_bundle(bundle, receipt, SOURCE)

    def test_unlisted_file_and_oversize_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle, receipt = fixture(Path(tmp))
            write(bundle / "app/unlisted.py", b"extra")
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                ci.validate_bundle(bundle, receipt, SOURCE)
            (bundle / "app/unlisted.py").unlink()
            with patch.object(ci, "MAX_FILE_BYTES", 2), self.assertRaisesRegex(ValueError, "size"):
                ci.validate_bundle(bundle, receipt, SOURCE)

    def test_stage_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            ci.stage(bundle, receipt, SOURCE, root / "stage")
            with self.assertRaisesRegex(ValueError, "overwrite"):
                ci.stage(bundle, receipt, SOURCE, root / "stage")

    def test_dispatch_size_limit_refused_before_stage_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            with self.assertRaisesRegex(ValueError, "dispatch-approved"):
                ci.stage(bundle, receipt, SOURCE, root / "stage", 1)
            self.assertFalse((root / "stage").exists())

    def test_pointer_or_release_tamper_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            result = ci.stage(bundle, receipt, SOURCE, root / "stage")
            manifest = root / "stage/releases" / result["release_id"] / "release.json"
            manifest.write_bytes(manifest.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "manifest"):
                verifier.verify(root / "stage")

    def test_local_publish_refused_before_any_git_call(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(ci, "git") as calls:
            with self.assertRaisesRegex(ValueError, "GitHub-hosted"):
                ci.publish(Path("."), "a" * 40, SOURCE)
            calls.assert_not_called()

    def test_moved_delivery_ref_never_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle, receipt = fixture(root)
            ci.stage(bundle, receipt, SOURCE, root / "stage")
            env = {
                "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                "GITHUB_REPOSITORY": ci.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SOURCE,
                "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_TOKEN": "not-a-real-credential",
            }
            operations = []
            def git(*args, **kwargs):
                operations.append(args[1])
                return (b"d" * 40 + b"\trefs/heads/windows-delivery\n") if args[1] == "ls-remote" else b""
            with patch.dict(os.environ, env, clear=True), patch.object(ci, "git", side_effect=git):
                with self.assertRaisesRegex(ValueError, "moved"):
                    ci.publish(root / "stage", "a" * 40, SOURCE)
            self.assertNotIn("push", operations)
            self.assertNotIn("fetch", operations)

    def test_no_e2e_claims_or_automatic_trigger_in_workflow(self):
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/windows-git-candidate.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("id-token: write", workflow)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertIn("needs: build", workflow)
        self.assertIn("persist-credentials: false", workflow)


class PublicationOutcomeTests(unittest.TestCase):
    """Mocked Git only: establish error ordering, not a real publication."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        bundle, receipt = fixture(self.root)
        self.staged = self.root / "stage"
        ci.stage(bundle, receipt, SOURCE, self.staged)
        self.receipt = self.root / "publication.json"
        self.operations = []
        self.cleanup_error = None
        self.readback = "d" * 40
        self.initial = "a" * 40
        self.cleanup_receipt = None
        self.stderr, self.stdout = io.StringIO(), io.StringIO()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stderr(self.stderr))
        self.stack.enter_context(contextlib.redirect_stdout(self.stdout))
        self.stack.enter_context(patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_REPOSITORY": ci.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": SOURCE,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_TOKEN": "not-a-real-credential",
        }, clear=True))
        self.stack.enter_context(patch.object(ci.tempfile, "TemporaryDirectory", return_value=
            SimpleNamespace(name=str(self.root / "throwaway.git"), cleanup=self.cleanup)))
        self.stack.enter_context(patch.object(ci, "git", side_effect=self.git))
        self.push = self.stack.enter_context(patch.object(ci, "push_exact_parent"))

    def git(self, repo, *args, **kwargs):
        self.operations.append(args)
        if args[0] == "ls-remote":
            calls = sum(operation[0] == "ls-remote" for operation in self.operations)
            value = self.initial if calls == 1 else self.readback
            return (value + "\trefs/heads/windows-delivery\n").encode()
        if args[0] in {"hash-object", "write-tree", "commit-tree"}:
            return (("d" if args[0] == "commit-tree" else "b") * 40 + "\n").encode()
        return b""

    def cleanup(self):
        if self.receipt.exists():
            self.cleanup_receipt = json.loads(self.receipt.read_text())
        if self.cleanup_error is not None:
            raise self.cleanup_error

    def publish(self):
        return ci.publish(self.staged, "a" * 40, SOURCE, receipt_path=self.receipt)

    def test_confirmed_publication_is_recorded_before_successful_cleanup(self):
        result = self.publish()
        self.assertEqual(self.cleanup_receipt["status"], "candidate_git_published")
        self.assertEqual(self.cleanup_receipt["cleanup"], {"status": "pending"})
        self.assertEqual(result["cleanup"], {"status": "completed"})
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.push.assert_called_once()
        fetch = next(i for i, op in enumerate(self.operations) if op[0] == "fetch")
        self.assertIn(("config", "gc.auto", "0"), self.operations[:fetch])
        self.assertIn(("config", "maintenance.auto", "false"), self.operations[:fetch])

    def test_cleanup_failure_preserves_confirmed_state_without_republishing(self):
        self.cleanup_error = OSError(errno.ENOTEMPTY, "sensitive-local-path")
        result = self.publish()
        self.assertEqual(self.cleanup_receipt["delivery_commit"], "d" * 40)
        self.assertEqual(result["status"], "candidate_git_published")
        self.assertEqual(result["cleanup"], {
            "status": "warning", "error_type": "OSError", "errno": errno.ENOTEMPTY})
        self.assertFalse(result["final_delivery"])
        self.assertEqual(result["windows_e2e"], "not_run")
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.push.assert_called_once()
        evidence = self.receipt.read_text() + self.stderr.getvalue() + self.stdout.getvalue()
        self.assertNotIn("sensitive-local-path", evidence)
        self.assertNotIn("not-a-real-credential", evidence)
        self.assertIn('"event": "delivery_cleanup"', self.stderr.getvalue())

    def test_push_failure_is_not_masked_by_cleanup_failure(self):
        original = RuntimeError("primary push failure")
        self.push.side_effect = original
        self.cleanup_error = OSError(errno.ENOTEMPTY, "secondary cleanup failure")
        with self.assertRaises(RuntimeError) as caught:
            self.publish()
        self.assertIs(caught.exception, original)
        self.assertFalse(self.receipt.exists())
        self.push.assert_called_once()

    def test_uncertain_readback_stays_uncertain_despite_cleanup_failure(self):
        self.readback = "e" * 40
        self.cleanup_error = OSError(errno.ENOTEMPTY, "secondary cleanup failure")
        with self.assertRaisesRegex(ValueError, "push uncertain"):
            self.publish()
        self.assertFalse(self.receipt.exists())
        self.push.assert_called_once()

    def test_moved_ref_and_cleanup_failure_never_publish(self):
        self.initial = "e" * 40
        self.cleanup_error = OSError(errno.ENOTEMPTY, "secondary cleanup failure")
        with self.assertRaisesRegex(ValueError, "branch moved"):
            self.publish()
        self.assertFalse(self.receipt.exists())
        self.push.assert_not_called()
        self.assertFalse(any(op[0] == "fetch" for op in self.operations))

    def test_receipt_failure_keeps_safe_confirmed_log_and_original_error(self):
        self.receipt.mkdir()  # Atomic replacement must fail, not hide an IO error.
        self.cleanup_error = OSError(errno.ENOTEMPTY, "secondary cleanup failure")
        # Avoid reading the deliberate directory in the cleanup test hook.
        with patch.object(ci.tempfile, "TemporaryDirectory", return_value=SimpleNamespace(
                name=str(self.root / "throwaway.git"),
                cleanup=lambda: (_ for _ in ()).throw(self.cleanup_error))):
            with self.assertRaises(IsADirectoryError):
                self.publish()
        event = json.loads(self.stdout.getvalue())
        self.assertEqual(event["status"], "candidate_git_published")
        self.assertEqual(event["receipt_status"], "write_failed")
        self.assertEqual(event["delivery_commit"], "d" * 40)
        self.push.assert_called_once()

    def test_final_receipt_write_failure_retains_early_confirmed_receipt(self):
        original_replace = Path.replace
        calls = 0

        def replace(path, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EIO, "synthetic receipt write failure")
            return original_replace(path, target)

        with patch.object(Path, "replace", replace), self.assertRaises(OSError):
            self.publish()
        saved = json.loads(self.receipt.read_text())
        self.assertEqual(saved["status"], "candidate_git_published")
        self.assertEqual(saved["cleanup"], {"status": "pending"})
        event = json.loads(self.stdout.getvalue())
        self.assertEqual(event["receipt_status"], "write_failed")
        self.assertEqual(event["cleanup"], {"status": "completed"})
        self.push.assert_called_once()


if __name__ == "__main__":
    unittest.main()
