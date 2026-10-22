"""Offline owner wrapper contracts only; no identity, DPAPI or native process is run."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import assemble_product


def launcher(root):
    spec = importlib.util.spec_from_file_location("owner_acceptance_launcher", HERE / "runtime/launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    return module


def fixture(root):
    (root / "app/wiki-private").mkdir(parents=True)
    (root / "app/wiki-private/index.html").write_text("SYNTHETIC UI")
    directory = root / "data/owner-acceptance"
    (directory / "platform").mkdir(parents=True)
    (directory / "desktop").mkdir()
    (directory / "identity.json").write_text("SYNTHETIC NON-CREDENTIAL; WRAPPER MUST NOT READ")
    config = {"mode": "owner-acceptance", "owner_dpapi_identity": "data/owner-acceptance/identity.json",
              "platform_port": 8766}
    desktop = {"service_url": "http://127.0.0.1:8766", "identity_file": "identity.json",
               "identity_format": "zhizhi-owner-acceptance-dpapi-v1", "device_id": "a" * 32,
               "input_classification": "personal", "providers": [], "model": {"enabled": False}}
    (directory / "launch.json").write_text(json.dumps(config))
    (directory / "desktop.json").write_text(json.dumps(desktop))
    return config, desktop


class OwnerAcceptanceLauncherTests(unittest.TestCase):
    def test_generated_launchers_never_embed_approval_or_pairing_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assemble_product.write_launchers(root)
            for path in root.glob("*.cmd"):
                text = path.read_text()
                self.assertNotIn("--approve-owner-acceptance-identity", text)
                self.assertNotIn("--approve-owner-identity-reuse", text)
                self.assertNotIn("--new-acceptance-run", text)
                if path.name in {"start.cmd", "local-test-start.cmd"}:
                    self.assertNotIn("owner-acceptance", text)
            self.assertIn("owner-acceptance-initialize %*", (root / "owner-acceptance-initialize.cmd").read_text())
            reuse = (root / "owner-acceptance-reuse.cmd").read_bytes()
            self.assertIn(b'"%~dp0app\\launcher.py" owner-acceptance-reuse %*\r\n', reuse)
            self.assertIn(b'exit /b %EXITCODE%', reuse)
            self.assertNotIn(b'--anchor-release', reuse)
            self.assertNotIn(b'owner-acceptance-initialize', reuse)
            # Unknown args are passed only to be rejected; the code itself is stdin-only.
            self.assertIn("owner-acceptance-pair %*", (root / "owner-acceptance-pair.cmd").read_text())
            self.assertNotIn("--pairing-code", (root / "owner-acceptance-pair.cmd").read_text())
            self.assertNotIn("%*", (root / "owner-acceptance-start.cmd").read_text())

    def test_approval_and_arguments_are_action_specific(self):
        module = launcher(HERE)
        rejected = [
            ["owner-acceptance-initialize"],
            ["owner-acceptance-initialize", "--approve-local-test-identity"],
            ["owner-acceptance-initialize", "--approve-owner-acceptance-identity", "--approve-local-test-identity"],
            ["local-test-initialize", "--approve-local-test-identity", "--approve-owner-acceptance-identity"],
            ["owner-acceptance-initialize", "--approve-owner-acceptance-identity", "--port", "80"],
            ["owner-acceptance-start", "--approve-owner-acceptance-identity"],
            ["start", "--approve-owner-acceptance-identity"],
            ["owner-acceptance-pair", "--pairing-code", "A" * 20],
            ["owner-acceptance-pair", "--port", "8766"],
            ["owner-acceptance-start", "--model-config", "arbitrary.json"],
        ]
        for args in rejected:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    module.parse_arguments(args)
        accepted = module.parse_arguments(["owner-acceptance-initialize", "--approve-owner-acceptance-identity", "--port", "9988"])
        self.assertTrue(accepted.approve_owner_acceptance_identity)
        self.assertEqual(accepted.port, 9988)
        self.assertEqual(module.parse_arguments(["owner-acceptance-pair"]).action, "owner-acceptance-pair")

    def test_helper_imports_only_fixed_module_and_preserves_stdin(self):
        module = launcher(HERE)
        original_argv, original_stdin = sys.argv, sys.stdin
        for action, flags, name, expected in [
            ("owner-acceptance-initialize", ["--approve-owner-acceptance-identity"], "owner_identity",
             ["--approve-owner-acceptance-identity", "--port", "8766"]),
            ("owner-acceptance-reuse", ["--approve-owner-identity-reuse", "--new-acceptance-run"], "owner_anchor",
             ["--approve-owner-identity-reuse", "--new-acceptance-run"]),
            ("owner-acceptance-reuse", ["--approve-owner-identity-reuse", "--new-acceptance-run", "--anchor-release", "abcdef123456-18-42"], "owner_anchor",
             ["--approve-owner-identity-reuse", "--new-acceptance-run", "--anchor-release", "abcdef123456-18-42"]),
            ("owner-acceptance-pair", [], "owner_pairing",
             ["--owner-dpapi-identity", str(HERE / "data/owner-acceptance/identity.json")]),
        ]:
            captured = []
            fake = Mock()
            fake.main.side_effect = lambda: captured.append((sys.argv.copy(), sys.stdin))
            with patch.object(module.importlib, "import_module", return_value=fake) as importing:
                module.run_owner_acceptance_identity(module.parse_arguments([action, *flags]))
            importing.assert_called_once_with("ahadiff.knowledge_platform." + name)
            self.assertEqual(captured[0][0][1:], expected)
            self.assertIs(captured[0][1], original_stdin)
            self.assertIs(sys.argv, original_argv)

    def test_identity_helpers_run_only_after_integrity_and_environment_checks(self):
        module = launcher(HERE)
        for action, flags in [("owner-acceptance-initialize", ["--approve-owner-acceptance-identity"]),
                              ("owner-acceptance-pair", []),
                              ("owner-acceptance-reuse", ["--approve-owner-identity-reuse", "--new-acceptance-run"])]:
            events = []
            with patch.object(module, "parse_arguments", return_value=module.parse_arguments([action, *flags])), \
                    patch.object(module.sys, "platform", "win32"), \
                    patch.object(module, "within"), patch.object(module, "validate_root"), \
                    patch.object(module, "verify_distribution", side_effect=lambda: events.append("verified")), \
                    patch.object(module, "isolated_environment", side_effect=lambda: events.append("isolated") or {"SAFE": "fixture"}), \
                    patch.object(module, "run_owner_acceptance_identity", side_effect=lambda _: events.append("explicit-action")), \
                    patch.object(module, "browser_path") as browser, \
                    patch.object(module, "start") as start, patch.dict(module.os.environ, {}, clear=True):
                module.main()
                self.assertEqual(dict(module.os.environ), {"SAFE": "fixture"})
            self.assertEqual(events, ["verified", "isolated", "explicit-action"])
            browser.assert_not_called()
            start.assert_not_called()

    def test_reuse_requires_both_approvals_and_strict_optional_anchor(self):
        module = launcher(HERE)
        valid = ["owner-acceptance-reuse", "--approve-owner-identity-reuse", "--new-acceptance-run"]
        self.assertIsNone(module.parse_arguments(valid).anchor_release)
        self.assertEqual(module.parse_arguments([*valid, "--anchor-release", "abcdef123456-18-42"]).anchor_release,
                         "abcdef123456-18-42")
        rejected = [["owner-acceptance-reuse"], valid[:2], [valid[0], valid[2]]]
        for flag in ["--approve-local-test-identity", "--approve-owner-acceptance-identity",
                     "--approve-owner-acceptance-model", "--approve-owner-pi-search"]:
            rejected.append([*valid, flag])
        for flag, value in [("--port", "8766"), ("--pairing-code", "A" * 20),
                            ("--expected-source", "a" * 40), ("--expected-release", "abcdef123456-18-42"),
                            ("--codex-exe", "arbitrary"), ("--codex-home", "arbitrary")]:
            rejected.append([*valid, flag, value])
        for anchor in ["", "../abcdef123456-18-42", "ABCDEF123456-18-42", "abcdef123456-18-42\n",
                       "abcdef123456-18", "abcdef123456-18-42/other", "abcdef123456-١٨-42", "a" * 40 + "-18-42"]:
            rejected.append([*valid, "--anchor-release", anchor])
        for action in ["start", "owner-acceptance-start", "owner-acceptance-pair", "owner-pi-once"]:
            for flags in [["--approve-owner-identity-reuse"], ["--new-acceptance-run"],
                          ["--anchor-release", "abcdef123456-18-42"]]:
                rejected.append([action, *flags])
        for args in rejected:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                module.parse_arguments(args)

    def test_reuse_cannot_dispatch_before_integrity_or_isolation(self):
        module = launcher(HERE)
        args = module.parse_arguments(["owner-acceptance-reuse", "--approve-owner-identity-reuse", "--new-acceptance-run"])
        for failing in ["verify_distribution", "isolated_environment"]:
            with self.subTest(failing=failing), patch.object(module, "parse_arguments", return_value=args), \
                    patch.object(module.sys, "platform", "win32"), \
                    patch.object(module, "within"), patch.object(module, "validate_root"), \
                    patch.object(module, "verify_distribution", side_effect=RuntimeError("fixture") if failing == "verify_distribution" else None), \
                    patch.object(module, "isolated_environment", side_effect=RuntimeError("fixture") if failing == "isolated_environment" else None), \
                    patch.object(module, "run_owner_acceptance_identity") as helper, \
                    patch.object(module, "browser_path") as browser, patch.object(module, "start") as start:
                with self.assertRaisesRegex(RuntimeError, "fixture"):
                    module.main()
                helper.assert_not_called()
                browser.assert_not_called()
                start.assert_not_called()

    def test_start_uses_fixed_owner_paths_and_never_reads_default_or_test_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _ = fixture(root)
            for path in ("config/launch.json", "data/local-test/launch.json"):
                file = root / path
                file.parent.mkdir(parents=True)
                file.write_text("INVALID; MUST NOT READ")
            module = launcher(root)
            self.assertEqual(module.load_config(owner_acceptance=True), config)
            args, port, mode = module.platform_command(config, owner_acceptance=True)
            self.assertEqual((port, mode), (8766, "configured"))
            self.assertEqual(args[args.index("--state-directory") + 1], str(root / "data/owner-acceptance/platform"))
            self.assertEqual(args[args.index("--owner-dpapi-identity") + 1], str(root / "data/owner-acceptance/identity.json"))
            self.assertEqual(args[args.index("--owner") + 1], "portable-operational-acceptance")
            self.assertEqual(args[args.index("--owner-browser-actor") + 1], "portable-acceptance-person")
            for flag in ("--local-test-identity", "--identities-file", "--run-worker", "--auto-commit", "--model-config", "--provider", "--channel-config"):
                self.assertNotIn(flag, args)
            self.assertEqual(module.platform_command({})[2], "waiting_auth")

    def test_owner_launch_rejects_mixed_policies_paths_and_initialization_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _ = fixture(root)
            module = launcher(root)
            for extra in ({"mode": "local-test"}, {"owner_dpapi_identity": "data/local-test/identity.json"},
                          {"owner_dpapi_identity": "../identity.json"}, {"platform_port": True},
                          {"model_config": None}, {"owner": "other"}, {"run_worker": False}):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    module.platform_command({**config, **extra}, owner_acceptance=True)
            with self.assertRaises(ValueError):
                module.platform_command(config)
            with self.assertRaises(ValueError):
                module.platform_command(config, local_test=True, owner_acceptance=True)
            with self.assertRaises(ValueError):
                module.load_config(local_test=True, owner_acceptance=True)
            (root / "data/owner-acceptance/identity.json").unlink()
            with self.assertRaises(RuntimeError):
                module.platform_command(config, owner_acceptance=True)
            self.assertFalse((root / "data/owner-acceptance/identity.json").exists())

    def test_base_owner_desktop_rejects_rebinding_or_implicit_model_enable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, desktop = fixture(root)
            module = launcher(root)
            file = root / "data/owner-acceptance/desktop.json"
            for extra in ({"service_url": "http://127.0.0.1:8767"}, {"identity_file": "other.json"},
                          {"identity_format": "protected-json"}, {"device_id": "bad"},
                          {"providers": ["openai"]}, {"model": {"enabled": True}},
                          {"model": {"enabled": 0}}, {"input_classification": "employer"},
                          {"token": "INERT-NOT-A-CREDENTIAL"}):
                file.write_text(json.dumps({**desktop, **extra}))
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    module.platform_command(config, owner_acceptance=True)

    def test_owner_start_spawns_only_platform_viewer_and_separate_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root)
            module = launcher(root)
            module.BROWSER = root / "SYNTHETIC_BROWSER_NOT_EXECUTED"
            stop = root / "data/stop.request"
            with patch.dict(sys.modules, {"msvcrt": Mock()}), \
                    patch.object(module, "spawn") as spawn, \
                    patch.object(module, "await_health") as health, \
                    patch.object(module, "free_port", return_value=9876) as allocate, \
                    patch.object(module.time, "sleep", side_effect=lambda _: stop.write_text("stop")), \
                    contextlib.redirect_stdout(io.StringIO()):
                module.start({}, owner_acceptance=True)
            self.assertEqual([call.args[1] for call in spawn.call_args_list], ["platform", "viewer", "browser"])
            allocate.assert_called_once_with()
            platform_args = spawn.call_args_list[0].args[0]
            self.assertEqual(platform_args[platform_args.index("--learning-viewer-url") + 1], "http://127.0.0.1:9876/")
            viewer = spawn.call_args_list[1]
            self.assertEqual(viewer.args[0][viewer.args[0].index("--port") + 1], "9876")
            self.assertEqual([call.args[1:] for call in health.call_args_list],
                             [(8766, "platform", "configured"), (9876, "viewer")])
            self.assertEqual(viewer.args[3], root / "data/owner-acceptance/desktop")
            self.assertEqual(viewer.args[2]["AHADIFF_KNOWLEDGE_CONFIG"], str(root / "data/owner-acceptance/desktop.json"))
            browser = spawn.call_args_list[2].args[0]
            self.assertIn("--user-data-dir=" + str(root / "data/owner-acceptance/browser-profile"), browser)
            status = json.loads((root / "reports/status.json").read_text())
            self.assertIs(status["fixture"], False)
            self.assertEqual(status["mode"], "owner-acceptance")
            self.assertEqual(status["pi"], "disabled_in_owner_acceptance")
            self.assertEqual(status["model"], "disabled")
            self.assertEqual(status["worker"], "disabled")
            self.assertEqual(status["browser_authentication"], "pairing_required")
            self.assertEqual(status["live_e2e"], "not_run")

    def test_reused_identity_health_rejects_old_or_unbound_platform(self):
        for actual in (None, {"release_id": "old", "source_commit": "b" * 40},
                       {"release_id": "a" * 12 + "-19-1", "source_commit": "a" * 40}):
            with self.subTest(actual=actual), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / ("a" * 12 + "-19-1")
                root.mkdir()
                fixture(root)
                (root / "release.json").write_text(json.dumps({"release_id": root.name, "source_commit": "a" * 40}))
                (root / "data/owner-acceptance/identity.json").write_text(json.dumps({"format": "zhizhi-owner-anchor-reference-v1"}))
                module = launcher(root)
                response = Mock(status=200)
                response.read.return_value = json.dumps({"service": "zhizhi-knowledge", "status": "ready",
                    "authentication_status": "configured", "owner_runtime_binding": actual}).encode()
                handle = contextlib.nullcontext(response)
                with patch.object(module.urllib.request, "urlopen", return_value=handle), patch.object(module.time, "sleep"):
                    proc = Mock()
                    proc.poll.return_value = None
                    if actual is None or actual["release_id"] == "old":
                        with self.assertRaisesRegex(RuntimeError, "different owner acceptance run"):
                            module.await_health(proc, 8766, "platform")
                    else:
                        module.await_health(proc, 8766, "platform")



if __name__ == "__main__":
    unittest.main()
