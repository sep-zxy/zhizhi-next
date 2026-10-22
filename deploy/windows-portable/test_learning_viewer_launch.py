"""Cloud-only launcher fixtures: no Windows execution or user credential access."""

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


class LearningViewerLaunchTests(unittest.TestCase):
    def start_fixture(self, *, owner_pi=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / RELEASE
        root.mkdir()
        fixture(root)
        (root / 'release.json').write_text(json.dumps({'source_commit': SOURCE, 'release_id': RELEASE}))
        module = launcher(root)
        stop = root / 'data/stop.request'
        token = 'fixture-viewer-token-000000000001'
        with patch.dict(sys.modules, {'msvcrt': Mock()}), \
                patch.object(module, 'spawn') as spawn, \
                patch.object(module, 'await_health'), \
                patch.object(module, 'free_port', return_value=9876), \
                patch.object(module.secrets, 'token_urlsafe', return_value=token) as random, \
                patch.object(module, 'owner_pi_environment', return_value={'ZHIZHI_AGENT_TOKEN': 'domain-fixture'}), \
                patch.object(module.time, 'sleep', side_effect=lambda _: stop.write_text('stop')), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            module.start({'SAFE': 'fixture', 'AHADIFF_SERVE_SESSION_TOKEN': 'untrusted-inherited',
                          'ZHIZHI_LEARNING_VIEWER_TOKEN': 'untrusted-inherited',
                          'ZHIZHI_LEARNING_VIEWER_URL': 'https://untrusted.invalid'},
                         owner_acceptance=True, owner_model=True, owner_pi=owner_pi)
        return root, spawn.call_args_list, random.call_count, token, stdout.getvalue()

    def test_one_token_only_reaches_viewer_and_human_interactive_pi(self):
        root, calls, count, token, output = self.start_fixture()
        self.assertEqual(count, 1)
        platform, viewer, pi = calls
        self.assertEqual(viewer.args[2]['AHADIFF_SERVE_SESSION_TOKEN'], token)
        self.assertEqual(pi.args[2]['ZHIZHI_LEARNING_VIEWER_TOKEN'], token)
        self.assertEqual(pi.args[2]['ZHIZHI_LEARNING_VIEWER_URL'], 'http://127.0.0.1:9876')
        self.assertNotIn('AHADIFF_SERVE_SESSION_TOKEN', pi.args[2])
        self.assertNotIn('ZHIZHI_LEARNING_VIEWER_TOKEN', viewer.args[2])
        self.assertNotIn('ZHIZHI_AGENT_TOKEN', viewer.args[2])
        for key in ('AHADIFF_SERVE_SESSION_TOKEN', 'ZHIZHI_LEARNING_VIEWER_TOKEN',
                    'ZHIZHI_LEARNING_VIEWER_URL'):
            self.assertNotIn(key, platform.args[2])
        for call in calls:
            self.assertNotIn(token, str(call.args[0]))
            self.assertNotIn('untrusted-inherited', str(call))
        self.assertNotIn(token, output)
        for path in root.rglob('*'):
            if path.is_file():
                self.assertNotIn(token.encode(), path.read_bytes())

    def test_non_pi_owner_modes_do_not_allocate_or_forward_token(self):
        _, calls, count, _, _ = self.start_fixture(owner_pi=False)
        self.assertEqual(count, 0)
        self.assertEqual([call.args[1] for call in calls], ['platform', 'viewer'])
        for call in calls:
            for key in ('AHADIFF_SERVE_SESSION_TOKEN', 'ZHIZHI_LEARNING_VIEWER_TOKEN',
                        'ZHIZHI_LEARNING_VIEWER_URL'):
                self.assertNotIn(key, call.args[2])

    def test_unrelated_launch_modes_cannot_enable_human_learning(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = launcher(Path(temporary))
            with patch.dict(sys.modules, {'msvcrt': Mock()}), self.assertRaises(ValueError):
                module.start({}, owner_pi=True)

    def test_fixed_once_environment_stays_without_viewer_capability(self):
        # Once obtains only this exact fixed environment, not start()'s human branch.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = launcher(root)
            values = {
                'ZHIZHI_MODEL_PROVIDER': 'zhizhi-codex-cli', 'ZHIZHI_MODEL_ID': 'gpt-6.1-sol',
                'ZHIZHI_AGENT_TOKEN': 'domain-fixture', 'ZHIZHI_API_URL': 'http://127.0.0.1:8766',
                'ZHIZHI_ALLOWED_CLASSIFICATIONS': 'public,personal',
                'ZHIZHI_PI_PYTHON': str(root / 'runtime/python/python.exe'),
                'PI_CODING_AGENT_DIR': str(root / 'data/owner-acceptance/pi'),
                'PI_CODING_AGENT_SESSION_DIR': str(root / 'data/owner-acceptance/pi/sessions'),
            }
            fake = Mock()
            fake.child_environment.return_value = values
            with patch.object(module.importlib, 'import_module', return_value=fake):
                actual = module.owner_pi_environment()
            self.assertEqual(actual, values)
            self.assertFalse(any('VIEWER' in key for key in actual))

    def test_startup_isolation_removes_viewer_capability_before_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = launcher(Path(temporary))
            incoming = {'AHADIFF_SERVE_SESSION_TOKEN': 'fixture-secret',
                        'ZHIZHI_LEARNING_VIEWER_TOKEN': 'fixture-secret',
                        'ZHIZHI_LEARNING_VIEWER_URL': 'http://127.0.0.1:9876'}
            with patch.dict(module.os.environ, incoming):
                isolated = module.isolated_environment()
            self.assertFalse(any(key in isolated for key in incoming))
