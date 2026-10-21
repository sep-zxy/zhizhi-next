"""Static/offline orchestrator checks, not a GitHub or Windows build."""

import importlib.util
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("github_product", HERE / "build_github_product.py")
product = importlib.util.module_from_spec(spec)
spec.loader.exec_module(product)


def learning_report_fixture():
    return {
        "errors": [], "stats": {"expected": sum(product.LEARNING_JOURNEYS.values()),
                                 "skipped": 0, "unexpected": 0, "flaky": 0},
        "suites": [{"specs": [{
            "file": name, "ok": True, "title": f"synthetic journey {index}",
            "tests": [{"projectName": "chromium-desktop", "expectedStatus": "passed",
                       "status": "expected", "results": [{"status": "passed"}]}],
        } for index in range(count)]} for name, count in product.LEARNING_JOURNEYS.items()],
    }


class GitHubProductTests(unittest.TestCase):
    def test_no_local_build_or_identity_spoofing_fallback(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "GitHub-hosted"):
            product.github_identity(HERE)

    def test_no_unapproved_download_origin(self):
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            product.read_official("https://example.invalid/not-an-official-asset", 1000)

    def test_pytest_not_unittest_only_for_mixed_test_suites(self):
        text = (HERE / "build_github_product.py").read_text()
        self.assertIn('python("-m", "pytest", "deploy/windows-portable")', text)
        self.assertIn('python("-m", "pytest", "deploy/git-sync")', text)

    def test_dependency_bootstrap_does_not_build_project_before_real_viewer(self):
        workflow = (HERE.parents[1] / ".github/workflows/windows-git-candidate.yml").read_text()
        commands = [shlex.split(line.strip().rstrip("\\")) for line in workflow.splitlines()
                    if line.strip().startswith("uv ")]
        sync, = [command for command in commands if command[1] == "sync"]
        self.assertEqual(sync, ["uv", "sync", "--project", "projects/growth", "--frozen",
                               "--group", "dev", "--extra", "wechat", "--extra", "captions",
                               "--no-install-project"])
        # Neither later invocation may silently undo the dependency-only install.
        runs = [command for command in commands if command[1] == "run"]
        self.assertEqual(len(runs), 2)
        self.assertIn("--no-sync", runs[0])
        self.assertIn("--no-project", runs[1])
        text = (HERE / "build_github_product.py").read_text()
        self.assertIn('env["PYTHONPATH"] = str(growth / "src")', text)
        self.assertIn('run("viewer-production-build",', text)
        self.assertLess(text.index('run("viewer-production-build",'),
                        text.index('run("assemble-windows-product",'))
        assembly = (HERE / "assemble_product.py").read_text()
        self.assertIn("viewer=repo/'projects/growth/viewer/dist'", assembly)
        self.assertIn("git','ls-files','-z','projects/growth/src/ahadiff'", assembly)

    def test_required_native_and_licensed_assets_are_kept(self):
        text = (HERE / "build_github_product.py").read_text()
        self.assertIn("fonts-manifest.json", text)
        self.assertIn("NotoSansSC-OFL.txt", text)
        self.assertIn('sorted(vendored["plugins"])', text)
        self.assertIn("@img/sharp-win32-x64@0.34.5", text)
        self.assertIn("@napi-rs/simple-git-win32-x64-msvc@0.1.22", text)
        self.assertIn("wiki-dynamic-fixture-gates", text)

    def test_locked_viewer_browser_is_required_before_any_build_or_tests(self):
        repo = HERE.resolve().parents[1]
        # Offline control-flow test only: never spoof CI or invoke an installer.
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "product"
            failure = subprocess.CalledProcessError(1, "synthetic-browser-install")
            with patch.object(product, "github_identity", return_value={}), \
                    patch.object(product.subprocess, "run", side_effect=failure) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    product.build(repo, output)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], [
                "pnpm", "--dir", str(repo / "projects/growth/viewer"), "exec",
                "playwright", "install", "--with-deps", "chromium",
            ])
            phases = json.loads((output / "evidence/build-phases.json").read_text())
            self.assertEqual(phases, [{"phase": "viewer-browser-install", "status": "failed",
                                      "error_type": "CalledProcessError", "exit_code": 1}])
            self.assertFalse((output / "bundle").exists())
        text = (HERE / "build_github_product.py").read_text()
        self.assertLess(text.index('run("viewer-browser-install",'),
                        text.index('run("viewer-tests",'))
        # The other app must continue to install its own locked browser, even
        # while both current locks happen to resolve to the same Playwright.
        self.assertIn('["npx", "--no-install", "playwright", "install", "--with-deps", "chromium"]', text)

    def test_private_wiki_is_independent_required_build(self):
        text = (HERE / "build_github_product.py").read_text()
        for phase in ("private-wiki-locked-install", "private-wiki-types",
                      "private-wiki-tests", "private-wiki-production-build",
                      "private-wiki-domain-capture", "private-wiki-domain-contract",
                      "private-wiki-browser-install", "private-wiki-browser-fixtures"):
            self.assertIn(phase, text)
        self.assertIn('"--private-wiki", private_wiki / "dist"', text)
        assembly = (HERE / "assemble_product.py").read_text()
        self.assertIn("app/'wiki-private'", assembly)
        self.assertIn("'http':'/wiki/'", assembly)

    def test_learning_report_requires_every_selected_journey_and_real_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            good = learning_report_fixture()
            path.write_text(json.dumps(good), encoding="utf-8")
            product.verify_learning_report(path)
            mutations = {
                "no suites": lambda r: r.update(suites=[]),
                "missing file": lambda r: r["suites"].pop(),
                "missing journey": lambda r: r["suites"][0]["specs"].pop(),
                "wrong total": lambda r: r["stats"].update(expected=1 + r["stats"]["expected"]),
                "skip": lambda r: r["stats"].update(skipped=1),
                "failure": lambda r: r["stats"].update(unexpected=1),
                "flaky": lambda r: r["stats"].update(flaky=1),
                "runner error": lambda r: r.update(errors=[{"message": "synthetic failure"}]),
                "unexpected file": lambda r: r["suites"][0]["specs"][0].update(file="legacy.spec.ts"),
                "failed spec": lambda r: r["suites"][0]["specs"][0].update(ok=False),
                "uncollected spec": lambda r: r["suites"][0]["specs"][0].update(tests=[]),
            }
            for name, mutate in mutations.items():
                report = copy.deepcopy(good)
                mutate(report)
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.subTest(name=name), self.assertRaises(ValueError):
                    product.verify_learning_report(path)
            for mutation in ({"projectName": "firefox-desktop"}, {"expectedStatus": "failed"},
                             {"status": "skipped"}, {"results": []},
                             {"results": [{"status": "failed"}, {"status": "passed"}]},
                             {"results": [{"status": "skipped"}]}):
                report = copy.deepcopy(good)
                report["suites"][0]["specs"][0]["tests"][0].update(mutation)
                path.write_text(json.dumps(report), encoding="utf-8")
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    product.verify_learning_report(path)

    def test_additional_reviewed_learning_journeys_are_not_capped_at_ten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            report = learning_report_fixture()
            extra = copy.deepcopy(report["suites"][0]["specs"][0])
            extra["title"] = "synthetic additional reviewed journey"
            report["suites"][0]["specs"].append(extra)
            report["stats"]["expected"] += 1
            path.write_text(json.dumps(report), encoding="utf-8")
            product.verify_learning_report(path)

    def test_learning_journey_failure_stops_before_product_build(self):
        repo = HERE.resolve().parents[1]
        for failure in ("command", "missing report", "skipped report"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "product"
                calls = []

                def run(command, **kwargs):
                    calls.append(command)
                    if "--project=chromium-desktop" not in command:
                        return
                    if failure == "command":
                        raise subprocess.CalledProcessError(1, "synthetic learning fixture")
                    if failure == "skipped report":
                        report = learning_report_fixture()
                        report["stats"]["skipped"] = 1
                        Path(kwargs["env"]["PLAYWRIGHT_JSON_OUTPUT_FILE"]).write_text(
                            json.dumps(report), encoding="utf-8")

                with patch.object(product, "github_identity", return_value={}), \
                        patch.object(product.subprocess, "run", side_effect=run):
                    with self.assertRaises((OSError, ValueError, subprocess.CalledProcessError)):
                        product.build(repo, output)
                command = calls[-1]
                self.assertEqual(command[:6], ["pnpm", "--dir",
                                 str(repo / "projects/growth/viewer"), "exec", "playwright", "test"])
                self.assertIn("tests/e2e/zhizhi-domain.spec.ts", command)
                self.assertIn("tests/e2e/growth-topics.spec.ts", command)
                self.assertIn("--retries=0", command)
                self.assertIn("--forbid-only", command)
                self.assertIn("--reporter=list,json", command)
                phases = json.loads((output / "evidence/build-phases.json").read_text())
                self.assertEqual(phases[-1]["phase"], "viewer-learning-browser-fixtures")
                self.assertEqual(phases[-1]["status"], "failed")
                self.assertNotIn("viewer-production-build", [phase["phase"] for phase in phases])
                self.assertFalse((output / "bundle").exists())

    def test_template_fixtures_are_distinct_synthetic_projections(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = product.template_fixtures(
                HERE.resolve().parents[1], Path(temporary), "f" * 40)
            self.assertNotEqual((first / "architecture/template.md").read_bytes(),
                                (second / "architecture/template.md").read_bytes())
            for path in (first, second):
                manifest = json.loads((path / "publish-manifest.json").read_text())
                self.assertEqual(manifest["counts"]["content_pages"], 1)
                self.assertEqual(manifest["counts"]["source_index_pages"], 0)
                self.assertEqual(manifest["revision"], "f" * 40)
                self.assertFalse(manifest["requires_domain_revalidation"])
                self.assertTrue((path / "index.md").is_file())


if __name__ == "__main__":
    unittest.main()
