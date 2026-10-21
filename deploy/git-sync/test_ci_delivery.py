"""Offline candidate tests. Synthetic files are not executable product evidence."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
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


if __name__ == "__main__":
    unittest.main()
