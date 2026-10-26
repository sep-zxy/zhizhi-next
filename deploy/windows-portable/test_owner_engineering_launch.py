"""Offline launcher mocks only; no model, browser, process or real activation."""
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

SOURCE='a'*40
RELEASE=SOURCE[:12]+'-123-1'


class EngineeringLaunchTests(unittest.TestCase):
    def test_exact_preview_and_activation_flags(self):
        module=launcher(Path(__file__).parent)
        common=['--expected-source',SOURCE,'--expected-release',RELEASE,
                '--codex-exe','synthetic.exe','--codex-home','synthetic',
                '--engineering-spec','data/spec.json']
        module.parse_arguments(['owner-engineering-preview',*common])
        activation=[*common,'--approve-owner-engineering','--approved-engineering-scope-hash','b'*64]
        module.parse_arguments(['owner-engineering-activate',*activation])
        invalid=[['owner-engineering-activate',*common],['owner-engineering-preview',*activation],
                 ['start',*activation],['owner-model-activate',*activation],
                 ['owner-engineering-start',*activation], ['owner-engineering-preview',*common,'--approve-owner-pi-search']]
        for args in invalid:
            with self.subTest(args=args),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                module.parse_arguments(args)
        self.assertTrue(module.parse_arguments(['owner-engineering-start','--no-browser']).no_browser)

    def test_activation_dispatch_never_starts_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/RELEASE;root.mkdir()
            (root/'release.json').write_text(json.dumps({'source_commit':SOURCE,'release_id':RELEASE}))
            module=launcher(root)
            args=module.parse_arguments(['owner-engineering-activate','--expected-source',SOURCE,
                '--expected-release',RELEASE,'--codex-exe','fixture.exe','--codex-home','fixture-home',
                '--engineering-spec','data/spec.json','--approve-owner-engineering',
                '--approved-engineering-scope-hash','b'*64])
            captured=[]; fake=Mock();fake.main.side_effect=lambda:captured.append(sys.argv.copy())
            with patch.object(module.importlib,'import_module',return_value=fake),patch.object(module,'spawn') as spawn:
                module.run_owner_engineering(args)
            self.assertIn('--approved-scope-hash',captured[0]);spawn.assert_not_called()
            args.expected_source='c'*40
            with self.assertRaises(ValueError):module.run_owner_engineering(args)

    def test_start_validates_activation_before_spawn_and_keeps_base_model_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root); module=launcher(root)
            config=root/'data/owner-acceptance/engineering-desktop.json'
            stop=root/'data/stop.request'
            from ahadiff.knowledge_platform import owner_engineering
            with patch.dict(sys.modules,{'msvcrt':Mock()}),patch.object(owner_engineering,'desktop_config_path',return_value=config),\
                 patch.object(module,'spawn') as spawn,patch.object(module,'await_health'),\
                 patch.object(module,'free_port',return_value=9876),patch.object(module.time,'sleep',side_effect=lambda _:stop.write_text('stop')),\
                 contextlib.redirect_stdout(io.StringIO()):
                module.start({},owner_acceptance=True,owner_engineering=True,no_browser=True)
            self.assertEqual([c.args[1] for c in spawn.call_args_list],['platform','viewer'])
            platform,viewer=spawn.call_args_list
            self.assertNotIn('ahadiff.knowledge_platform.owner_acceptance',platform.args[0])
            self.assertEqual(viewer.args[2]['AHADIFF_KNOWLEDGE_CONFIG'],str(config))
            self.assertIn('--no-browser',viewer.args[0])
            self.assertEqual(json.loads((root/'data/owner-acceptance/desktop.json').read_text())['model'],{'enabled':False})
            with patch.dict(sys.modules,{'msvcrt':Mock()}),patch.object(owner_engineering,'desktop_config_path',side_effect=ValueError('not activated')),\
                 patch.object(module,'spawn') as spawn,self.assertRaises(ValueError):
                module.start({},owner_acceptance=True,owner_engineering=True,no_browser=True)
            spawn.assert_not_called()

    def test_engineering_mode_cannot_mix_scopes(self):
        module=launcher(Path(__file__).parent)
        for values in ({},{'local_test':True},{'owner_acceptance':True,'owner_model':True}):
            with patch.dict(sys.modules,{'msvcrt':Mock()}),self.assertRaises(ValueError):
                module.start({},owner_engineering=True,**values)

    def test_new_wrappers_forward_flags_but_embed_no_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);assemble_product.write_launchers(root)
            for action in ('owner-engineering-preview','owner-engineering-activate','owner-engineering-start'):
                text=(root/(action+'.cmd')).read_text()
                self.assertIn(action+' %*',text)
                self.assertNotIn('--approve-owner-engineering',text)
            self.assertNotIn('engineering',(root/'owner-acceptance-start.cmd').read_text())

if __name__=='__main__':unittest.main()
