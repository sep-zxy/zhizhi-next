"""Offline port-to-navigation assembly, not a real Windows/browser/auth test."""

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


def launcher(root):
    spec = importlib.util.spec_from_file_location("navigation_launcher", HERE / "runtime/launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    (root / "app/wiki-private").mkdir(parents=True)
    (root / "app/wiki-private/index.html").write_text("SYNTHETIC UI")
    return module


class LearningNavigationTests(unittest.TestCase):
    def test_only_launcher_assigned_integer_port_becomes_canonical_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = launcher(Path(tmp))
            self.assertNotIn("--learning-viewer-url", module.platform_command({})[0])
            for port in (1024, 9876, 65535):
                with self.subTest(port=port):
                    args, platform_port, mode = module.platform_command({}, learning_viewer_port=port)
                    self.assertEqual(args[args.index("--learning-viewer-url") + 1], f"http://127.0.0.1:{port}/")
                    self.assertEqual((platform_port, mode), (8766, "waiting_auth"))
            for port in (True, False, 0, 1023, 65536, 8766, "9876", "http://127.0.0.1:9876/", 9876.0):
                with self.subTest(port=port), self.assertRaises(ValueError):
                    module.platform_command({}, learning_viewer_port=port)

    def test_launch_configuration_cannot_override_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = launcher(Path(tmp))
            for field in ("learning_viewer_url", "learning_viewer_port"):
                for value in (None, 9876, "http://127.0.0.1:9876/", "https://example.invalid/"):
                    with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                        module.platform_command({field: value}, learning_viewer_port=9876)

    def test_local_test_policy_rejects_even_a_valid_navigation_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = launcher(Path(tmp))
            config = {"mode": "local-test", "local_test_identity": "data/local-test/identity.json", "platform_port": 8766}
            with self.assertRaisesRegex(ValueError, "viewer port"):
                module.platform_command(config, local_test=True, learning_viewer_port=9876)

    def test_normal_and_no_identity_start_reuse_one_port_without_claiming_authentication(self):
        for configured in (False, True):
            with self.subTest(configured=configured), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                module = launcher(root)
                config = {}
                if configured:
                    (root / "synthetic-identity.json").write_text("SYNTHETIC; WRAPPER DOES NOT READ")
                    config = {"identities_file": "synthetic-identity.json", "owner": "synthetic-owner"}
                events = []
                stop = root / "data/stop.request"
                def spawn(command, name, *args):
                    events.append((name, command))
                    return Mock()
                with patch.dict(sys.modules, {"msvcrt": Mock()}), \
                        patch.object(module, "load_config", return_value=config), \
                        patch.object(module, "free_port", side_effect=lambda: events.append(("allocate", 9876)) or 9876) as allocate, \
                        patch.object(module, "spawn", side_effect=spawn), \
                        patch.object(module, "await_health") as health, \
                        patch.object(module.time, "sleep", side_effect=lambda _: stop.write_text("stop")), \
                        contextlib.redirect_stdout(io.StringIO()):
                    module.start({})
                allocate.assert_called_once_with()
                self.assertEqual([event[0] for event in events], ["allocate", "platform", "viewer"])
                platform_args, viewer_args = events[1][1], events[2][1]
                self.assertEqual(platform_args[platform_args.index("--learning-viewer-url") + 1], "http://127.0.0.1:9876/")
                self.assertEqual(viewer_args[viewer_args.index("--port") + 1], "9876")
                self.assertEqual([call.args[1:] for call in health.call_args_list],
                                 [(8766, "platform", "configured" if configured else "waiting_auth"), (9876, "viewer")])
                status = json.loads((root / "reports/status.json").read_text())
                self.assertEqual(status["viewer_url"], "http://127.0.0.1:9876/")
                self.assertEqual(status["viewer_mode"], "standalone_not_unified")
                self.assertEqual(status["live_e2e"], "not_run")
                if not configured:
                    self.assertEqual(status["unified_platform"], "waiting_auth")

    def test_port_collision_fails_before_spawn_without_search_or_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = launcher(Path(tmp))
            with patch.dict(sys.modules, {"msvcrt": Mock()}), \
                    patch.object(module, "free_port", return_value=8766) as allocate, \
                    patch.object(module, "spawn") as spawn, self.assertRaises(ValueError):
                module.start({})
            allocate.assert_called_once_with()
            spawn.assert_not_called()

    def test_viewer_health_failure_is_not_reported_as_running_or_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = launcher(root)
            with patch.dict(sys.modules, {"msvcrt": Mock()}), \
                    patch.object(module, "free_port", return_value=9876) as allocate, \
                    patch.object(module, "spawn") as spawn, \
                    patch.object(module, "await_health", side_effect=[None, RuntimeError("SYNTHETIC FAILURE")]), \
                    patch.object(module, "write_json") as write, self.assertRaises(RuntimeError):
                module.start({})
            allocate.assert_called_once_with()
            self.assertEqual([call.args[1] for call in spawn.call_args_list], ["platform", "viewer"])
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
