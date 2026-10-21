"""Offline thin-wrapper checks, never activate identity or invoke a real model."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_owner_acceptance_launch import fixture, launcher
import assemble_product

SOURCE = 'a' * 40
RELEASE = SOURCE[:12] + '-123-1'


class OwnerModelLauncherTests(unittest.TestCase):
    def test_generated_wrappers_never_embed_approval_or_model_on_base_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assemble_product.write_launchers(root)
            self.assertIn('owner-model-activate %*', (root / 'owner-model-activate.cmd').read_text())
            for path in root.glob('*.cmd'):
                self.assertNotIn('--approve-owner-acceptance-model', path.read_text())
            self.assertNotIn('%*', (root / 'owner-model-start.cmd').read_text())
            self.assertNotIn('owner-model', (root / 'owner-acceptance-start.cmd').read_text())

    def test_activation_flags_are_explicit_and_cannot_be_passed_to_other_modes(self):
        module = launcher(Path(__file__).parent)
        flags = ['--approve-owner-acceptance-model', '--expected-source', SOURCE,
                 '--expected-release', RELEASE, '--codex-exe', 'synthetic.exe', '--codex-home', 'synthetic']
        parsed = module.parse_arguments(['owner-model-activate', *flags])
        self.assertTrue(parsed.approve_owner_acceptance_model)
        for args in (['owner-model-activate'], ['owner-model-activate', *flags[1:]],
                     ['start', *flags], ['owner-acceptance-start', *flags], ['owner-model-start', *flags],
                     ['owner-model-activate', *flags, '--port', '8766']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                module.parse_arguments(args)

    def test_scope_serve_uses_fixed_release_and_same_assigned_viewer_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / RELEASE
            root.mkdir()
            config, _ = fixture(root)
            (root / 'release.json').write_text(json.dumps({'source_commit': SOURCE, 'release_id': RELEASE}))
            module = launcher(root)
            command, port, authentication = module.platform_command(
                config, owner_acceptance=True, learning_viewer_port=9000, owner_model=True)
            self.assertIn('ahadiff.knowledge_platform.owner_acceptance', command)
            self.assertIn('--serve-activated-scope', command)
            self.assertEqual(command[command.index('--expected-source') + 1], SOURCE)
            self.assertEqual(command[command.index('--expected-release') + 1], RELEASE)
            self.assertEqual(command[command.index('--learning-viewer-url') + 1], 'http://127.0.0.1:9000/')
            self.assertEqual((port, authentication), (8766, 'configured'))
            self.assertNotIn('--approve-owner-acceptance-model', command)
            self.assertNotIn('--codex-home', command)
            self.assertNotIn('owner_acceptance', ' '.join(module.platform_command(config, owner_acceptance=True)[0]))
            with self.assertRaises(ValueError):
                module.platform_command({}, owner_model=True)

    def test_activation_passthrough_requires_expected_current_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / RELEASE
            root.mkdir()
            (root / 'release.json').write_text(json.dumps({'source_commit': SOURCE, 'release_id': RELEASE}))
            module = launcher(root)
            flags = ['owner-model-activate', '--approve-owner-acceptance-model', '--expected-source', SOURCE,
                     '--expected-release', RELEASE, '--codex-exe', 'synthetic.exe', '--codex-home', 'synthetic-home']
            captured = []
            fake = Mock()
            fake.main.side_effect = lambda: captured.append(sys.argv.copy())
            with patch.object(module.importlib, 'import_module', return_value=fake) as importing:
                module.run_owner_model_activation(module.parse_arguments(flags))
            importing.assert_called_once_with('ahadiff.knowledge_platform.owner_acceptance')
            self.assertEqual(captured[0][1:], flags[1:])
            args = module.parse_arguments(flags)
            args.expected_release = SOURCE[:12] + '-124-1'
            with patch.object(module.importlib, 'import_module') as importing, self.assertRaises(ValueError):
                module.run_owner_model_activation(args)
            importing.assert_not_called()
