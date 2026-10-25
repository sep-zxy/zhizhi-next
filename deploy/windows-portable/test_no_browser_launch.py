"""Offline launcher mocks only: no Windows, browser, service or identity execution."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_local_test_launch import fixture as local_fixture, launcher
from test_owner_acceptance_launch import fixture as owner_fixture


START_ACTIONS = ('start', 'local-test-start', 'owner-acceptance-start',
                 'owner-model-start', 'owner-pi-start')


class NoBrowserLauncherTests(unittest.TestCase):
    def test_option_is_explicit_and_start_only(self):
        module = launcher(Path(__file__).parent)
        for action in START_ACTIONS:
            self.assertFalse(module.parse_arguments([action]).no_browser)
            self.assertTrue(module.parse_arguments([action, '--no-browser']).no_browser)
        for action in ('stop', 'diagnostics', 'publish', 'owner-pi-once'):
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    module.parse_arguments([action, '--no-browser'])

    def test_main_skips_browser_discovery_and_forwards_option_for_every_start(self):
        for action in START_ACTIONS:
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                module = launcher(Path(directory))
                args = module.parse_arguments([action, '--no-browser'])
                # Keep all native execution, real environment writes and services mocked.
                with patch.object(module, 'parse_arguments', return_value=args), \
                        patch.object(sys, 'platform', 'win32'), \
                        patch.object(module, 'validate_root'), \
                        patch.object(module, 'verify_distribution'), \
                        patch.object(module, 'isolated_environment', return_value={}), \
                        patch.object(module.os, 'environ', {}), \
                        patch.object(module, 'diagnose'), \
                        patch.object(module, 'browser_path') as discover, \
                        patch.object(module, 'start') as start, \
                        patch.dict(sys.modules, {'windows_job': Mock()}), \
                        patch.object(module.subprocess, 'Popen', side_effect=AssertionError('No native processes')):
                    module.main()
                discover.assert_not_called()
                self.assertIsNone(module.BROWSER)
                self.assertTrue(start.call_args.kwargs['no_browser'])
                self.assertEqual(start.call_args.kwargs['owner_pi'], action == 'owner-pi-start')
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_no_browser_start_keeps_services_without_profile_or_cache(self):
        for mode in ('start', 'local-test-start', 'owner-acceptance-start'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if mode == 'local-test-start':
                    local_fixture(root)
                elif mode == 'owner-acceptance-start':
                    owner_fixture(root)
                else:
                    (root / 'app/wiki-private').mkdir(parents=True)
                    (root / 'app/wiki-private/index.html').write_text('SYNTHETIC UI')
                module = launcher(root)
                # Even a stale discovered Edge value must not be used.
                module.BROWSER = root / 'SYNTHETIC_msedge.exe'
                stop = root / 'data/stop.request'
                with patch.dict(sys.modules, {'msvcrt': Mock()}), \
                        patch.object(module, 'spawn') as spawn, \
                        patch.object(module, 'await_health'), \
                        patch.object(module, 'free_port', return_value=9876), \
                        patch.object(module.subprocess, 'Popen', side_effect=AssertionError('No native processes')), \
                        patch.object(module.time, 'sleep', side_effect=lambda _: stop.write_text('stop')), \
                        contextlib.redirect_stdout(io.StringIO()):
                    module.start({}, local_test=mode == 'local-test-start',
                                 owner_acceptance=mode == 'owner-acceptance-start', no_browser=True)
                expected = ['platform'] if mode == 'local-test-start' else ['platform', 'viewer']
                self.assertEqual([call.args[1] for call in spawn.call_args_list], expected)
                if 'viewer' in expected:
                    self.assertIn('--no-browser', spawn.call_args_list[1].args[0])
                self.assertEqual(json.loads((root / 'reports/status.json').read_text())['browser'],
                                 'disabled_by_request')
                self.assertFalse(list(root.rglob('*browser*')))


if __name__ == '__main__':
    unittest.main()
