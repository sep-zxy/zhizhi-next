"""Offline Pi wrapper contracts: no real identity, credentials or process launch."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_owner_acceptance_launch import fixture, launcher
from test_owner_model_launch import RELEASE, SOURCE
import assemble_product


class OwnerPiLauncherTests(unittest.TestCase):
    def test_distinct_wrappers_do_not_grant_approval_or_expand_old_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assemble_product.write_launchers(root)
            self.assertIn('owner-pi-activate %*', (root / 'owner-pi-activate.cmd').read_text())
            self.assertNotIn('%*', (root / 'owner-pi-start.cmd').read_text())
            for path in root.glob('*.cmd'):
                self.assertNotIn('--approve-owner-pi-search', path.read_text())
            for name in ('start.cmd', 'local-test-start.cmd', 'owner-acceptance-start.cmd', 'owner-model-start.cmd'):
                self.assertNotIn('owner-pi', (root / name).read_text())

    def test_pi_approval_is_separate_and_binds_current_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / RELEASE
            root.mkdir()
            (root / 'release.json').write_text(json.dumps({'source_commit': SOURCE, 'release_id': RELEASE}))
            module = launcher(root)
            flags = ['--approve-owner-pi-search', '--expected-source', SOURCE, '--expected-release', RELEASE]
            args = module.parse_arguments(['owner-pi-activate', *flags])
            captured = []
            fake = Mock()
            fake.main.side_effect = lambda: captured.append(sys.argv.copy())
            with patch.object(module.importlib, 'import_module', return_value=fake) as importing:
                module.run_owner_pi_activation(args)
            importing.assert_called_once_with('ahadiff.knowledge_platform.pi_acceptance')
            self.assertEqual(captured[0][1:], flags)
            for argv in (['owner-pi-activate'], ['owner-pi-start', *flags], ['start', *flags],
                         ['owner-model-activate', *flags], ['owner-pi-activate', *flags, '--codex-exe', 'x'],
                         ['owner-pi-activate', *flags, '--approve-owner-acceptance-model']):
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    module.parse_arguments(argv)
            args.expected_source = 'b' * 40
            with patch.object(module.importlib, 'import_module') as importing, self.assertRaises(ValueError):
                module.run_owner_pi_activation(args)
            importing.assert_not_called()

    def test_pi_token_only_reaches_dedicated_process_and_scope_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / RELEASE
            root.mkdir()
            fixture(root)
            (root / 'release.json').write_text(json.dumps({'source_commit': SOURCE, 'release_id': RELEASE}))
            module = launcher(root)
            stop = root / 'data/stop.request'
            values = {'ZHIZHI_AGENT_TOKEN': 'INERT-ONLY-PI'}
            with patch.dict(sys.modules, {'msvcrt': Mock()}), \
                    patch.object(module, 'spawn') as spawn, \
                    patch.object(module, 'await_health'), \
                    patch.object(module, 'free_port', return_value=9876), \
                    patch.object(module, 'owner_pi_environment', return_value=values), \
                    patch.object(module.time, 'sleep', side_effect=lambda _: stop.write_text('stop')), \
                    contextlib.redirect_stdout(io.StringIO()):
                module.start({'SAFE': 'fixture'}, owner_acceptance=True, owner_model=True, owner_pi=True)
            self.assertEqual([call.args[1] for call in spawn.call_args_list], ['platform', 'viewer', 'pi'])
            platform, viewer, pi = spawn.call_args_list
            self.assertIn('--serve-activated-pi-scope', platform.args[0])
            for call in (platform, viewer):
                self.assertNotIn('ZHIZHI_AGENT_TOKEN', call.args[2])
                self.assertNotIn('ZHIZHI_USER_TOKEN', call.args[2])
            self.assertEqual(pi.args[2]['ZHIZHI_AGENT_TOKEN'], 'INERT-ONLY-PI')
            self.assertEqual(pi.args[0][-1], str(root / 'app/pi/launch-codex-interactive.mjs'))
            self.assertEqual(pi.kwargs['cwd'], root / 'data/owner-acceptance/pi')
            report = (root / 'reports/status.json').read_text()
            self.assertNotIn('INERT-ONLY-PI', report)
            self.assertIn('not_verified', report)

    def test_child_environment_rejects_owner_secret_and_arbitrary_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = launcher(root)
            base = {'ZHIZHI_MODEL_PROVIDER': 'zhizhi-codex-cli', 'ZHIZHI_MODEL_ID': 'gpt-6.1-sol',
                    'ZHIZHI_AGENT_TOKEN': 'INERT', 'ZHIZHI_API_URL': 'http://127.0.0.1:8766',
                    'ZHIZHI_ALLOWED_CLASSIFICATIONS': 'public,personal',
                    'ZHIZHI_PI_PYTHON': str(root / 'runtime/python/python.exe'),
                    'PI_CODING_AGENT_DIR': str(root / 'data/owner-acceptance/pi'),
                    'PI_CODING_AGENT_SESSION_DIR': str(root / 'data/owner-acceptance/pi/sessions')}
            fake = Mock()
            with patch.object(module.importlib, 'import_module', return_value=fake):
                fake.child_environment.return_value = base
                self.assertEqual(module.owner_pi_environment(), base)
                for change in ({'ZHIZHI_USER_TOKEN': 'INERT'}, {'ZHIZHI_PI_PYTHON': 'arbitrary'},
                               {'ZHIZHI_MODEL_ID': 'fallback'}):
                    fake.child_environment.return_value = {**base, **change}
                    with self.subTest(change=change), self.assertRaises(ValueError):
                        module.owner_pi_environment()
