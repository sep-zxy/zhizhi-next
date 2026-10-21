"""Offline wrapper contracts only; never create a Windows identity or DPAPI token."""

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
    spec = importlib.util.spec_from_file_location("local_test_launcher", HERE / "runtime/launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    return module


def fixture(root):
    (root / "app/wiki-private").mkdir(parents=True)
    (root / "app/wiki-private/index.html").write_text("SYNTHETIC UI")
    (root / "data/local-test/platform").mkdir(parents=True)
    (root / "data/local-test/identity.json").write_text("SYNTHETIC NON-CREDENTIAL")
    config = {
        "mode": "local-test", "local_test_identity": "data/local-test/identity.json",
        "platform_port": 8766,
    }
    (root / "data/local-test/launch.json").write_text(json.dumps(config))
    return config


class LocalTestLauncherTests(unittest.TestCase):
    def test_generated_launchers_do_not_implicitly_approve_or_initialize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assemble_product.write_launchers(root)
            for path in root.glob("*.cmd"):
                text = path.read_text()
                self.assertNotIn("--approve-local-test-identity", text)
                if path.name == "start.cmd":
                    self.assertNotIn("local-test", text)
                    self.assertNotIn("%*", text)
            for action in ("local-test-initialize", "local-test-pair"):
                text = (root / (action + ".cmd")).read_text()
                self.assertIn(action + " %*", text)
                self.assertIn('app\\launcher.py"', text)
            self.assertNotIn("%*", (root / "local-test-start.cmd").read_text())

    def test_explicit_approval_and_action_specific_arguments(self):
        module = launcher(HERE)
        rejected = [
            ["local-test-initialize"], ["start", "--approve-local-test-identity"],
            ["local-test-start", "--approve-local-test-identity"],
            ["start", "--port", "8766"], ["local-test-pair"],
            ["local-test-pair", "--pairing-code", "not-a-code"],
            ["local-test-initialize", "--approve-local-test-identity", "--port", "80"],
            ["local-test-start", "--pairing-code", "A" * 20],
        ]
        for args in rejected:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    module.parse_arguments(args)
        approved = module.parse_arguments(
            ["local-test-initialize", "--approve-local-test-identity", "--port", "9988"])
        self.assertTrue(approved.approve_local_test_identity)
        self.assertEqual(approved.port, 9988)

    def test_only_fixed_identity_module_receives_explicit_arguments(self):
        module = launcher(HERE)
        original = sys.argv
        for action, args, expected in [
            ("local-test-initialize", ["--approve-local-test-identity"],
             ["initialize", "--approve-local-test-identity", "--port", "8766"]),
            ("local-test-pair", ["--pairing-code", "A" * 20],
             ["pair", "--pairing-code", "A" * 20]),
        ]:
            captured = []
            fake = Mock()
            fake.main.side_effect = lambda: captured.append(sys.argv.copy())
            with patch.object(module.importlib, "import_module", return_value=fake) as importing:
                module.run_local_test_identity(module.parse_arguments([action, *args]))
                importing.assert_called_once_with("ahadiff.knowledge_platform.local_test_identity")
            self.assertEqual(captured[0][1:], expected)
            self.assertIs(sys.argv, original)

    def test_local_test_ignores_default_config_and_uses_exact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fixture(root)
            (root / "config").mkdir()
            (root / "config/launch.json").write_text("INVALID PRODUCTION CONFIG; DO NOT READ")
            module = launcher(root)
            self.assertEqual(module.load_config(True), config)
            args, port, mode = module.platform_command(config, True)
            self.assertEqual((port, mode), (8766, "configured"))
            self.assertEqual(args[args.index("--state-directory") + 1],
                             str(root / "data/local-test/platform"))
            self.assertEqual(args[args.index("--local-test-identity") + 1],
                             str(root / "data/local-test/identity.json"))
            for flag in ("--identities-file", "--owner", "--model-config", "--provider",
                         "--run-worker", "--auto-commit", "--channel-config"):
                self.assertNotIn(flag, args)

    def test_test_config_rejects_production_mixing_and_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = fixture(root)
            module = launcher(root)
            for field in ("owner", "identities_file", "model_config", "channel_config",
                          "desktop_config", "pi_environment_file", "providers", "run_worker",
                          "auto_commit", "publication_config"):
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "production"):
                    module.platform_command({**config, field: None}, True)
            for identity in ("data/other.json", "../identity.json"):
                with self.subTest(identity=identity), self.assertRaises(ValueError):
                    module.platform_command({**config, "local_test_identity": identity}, True)
            with self.assertRaisesRegex(ValueError, "separate explicit"):
                module.platform_command(config)
            with self.assertRaisesRegex(ValueError, "port"):
                module.platform_command({**config, "platform_port": True}, True)
            (root / "data/local-test/identity.json").unlink()
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                module.platform_command(config, True)

    def test_local_start_only_spawns_private_service_and_isolated_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            module = launcher(root)
            module.BROWSER = root / "SYNTHETIC_BROWSER_NOT_EXECUTED"
            fake_msvcrt = Mock()
            stop = root / "data/stop.request"
            with patch.dict(sys.modules, {"msvcrt": fake_msvcrt}), \
                    patch.object(module, "spawn") as spawn, \
                    patch.object(module, "await_health") as health, \
                    patch.object(module, "free_port") as allocate, \
                    patch.object(module.time, "sleep", side_effect=lambda _: stop.write_text("stop")), \
                    contextlib.redirect_stdout(io.StringIO()):
                module.start({}, True)
            self.assertEqual([call.args[1] for call in spawn.call_args_list], ["platform", "browser"])
            allocate.assert_not_called()
            self.assertNotIn("--learning-viewer-url", spawn.call_args_list[0].args[0])
            health.assert_called_once_with(spawn.return_value, 8766, "platform", "configured")
            browser = spawn.call_args_list[1].args[0]
            self.assertIn("--user-data-dir=" + str(root / "data/local-test/browser-profile"), browser)
            status = json.loads((root / "reports/status.json").read_text())
            self.assertTrue(status["fixture"])
            self.assertEqual(status["viewer"], "disabled_in_local_test")
            self.assertEqual(status["pi"], "disabled_in_local_test")
            self.assertEqual(status["browser_authentication"], "pairing_required")
            self.assertEqual(status["live_e2e"], "not_run")


if __name__ == "__main__":
    unittest.main()
