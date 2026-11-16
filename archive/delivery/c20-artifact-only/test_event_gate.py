"""Pure fixture/mocked-git tests; no CI, Docker, build, network or publication."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import event_gate as gate


class EventGate(unittest.TestCase):
    def fixture(self):
        return ({'GITHUB_EVENT_NAME': 'push', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': 'a'*40},
            {'ref': 'refs/heads/main', 'before': gate.BASE, 'after': 'a'*40,
             'repository': {'full_name': 'sep-zxy/zhizhi-platform'},
             'forced': False, 'created': False, 'deleted': False})

    def test_valid_transition(self):
        gate.validate_event(*self.fixture())

    def test_replays_and_other_events_rejected(self):
        for key, value in [('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_EVENT_NAME', 'pull_request'),
                           ('GITHUB_REF', 'refs/heads/other'), ('GITHUB_SHA', 'b'*40)]:
            env,event=self.fixture();env[key]=value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'Unapproved'):
                gate.validate_event(env,event)

    def test_wrong_transition_flags_or_identity_rejected(self):
        for key,value in [('before','b'*40),('after','b'*40),('forced',True),('created',True),
                          ('deleted',True),('forced',None),('repository',{'full_name':'other/repo'})]:
            env,event=self.fixture();event[key]=value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'Unapproved'):
                gate.validate_event(env,event)

    def test_manual_keeps_separate_expected_source_binding(self):
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_SHA':'a'*40,
                                     'EXPECTED_SOURCE':'b'*40}, clear=True), \
             patch.object(gate.subprocess, 'check_output', side_effect=['a'*40+'\n','']):
            with self.assertRaisesRegex(ValueError,'Manual expected source'):
                gate.bind(Path('/fixture'))

    def test_single_parent_tree_paths_and_marker(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);marker=root/gate.MARKER;marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps(gate.MARKER_VALUE))
            env,event=self.fixture();env['GITHUB_WORKSPACE']=directory
            event_path=root/'event.json';event_path.write_text(json.dumps(event));env['GITHUB_EVENT_PATH']=str(event_path)
            good=['a'*40,'',gate.BASE,gate.BASE_TREE,'\n'.join(sorted(gate.CHANGES))]
            for index,bad in [(0,'b'*40),(1,' M unexpected'),(2,gate.BASE+' '+'b'*40),
                              (3,'b'*40),(4,'unexpected')]:
                answers=good.copy();answers[index]=bad
                with self.subTest(index=index),patch.dict(os.environ,env,clear=True), \
                     patch.object(gate.subprocess,'check_output',side_effect=answers),self.assertRaises(ValueError):
                    gate.bind(root)
            with patch.dict(os.environ,env,clear=True),patch.object(gate.subprocess,'check_output',side_effect=good):
                gate.bind(root)
            marker.write_text('{}')
            with patch.dict(os.environ,env,clear=True),patch.object(gate.subprocess,'check_output',side_effect=good), \
                 self.assertRaisesRegex(ValueError,'marker'):
                gate.bind(root)

    def test_workflow_filters_unique_marker_and_blocks_reruns(self):
        text=(Path(__file__).resolve().parents[2]/'.github/workflows/c20-artifact-only.yml').read_text()
        for token in (gate.MARKER,gate.BASE,'github.run_attempt == 1','fetch-depth: 2',
                      'uv run --no-project --offline python deploy/c20-artifact-only/event_gate.py','persist-credentials: false'):
            self.assertIn(token,text)
        self.assertLess(text.index('event_gate.py'),text.index('actions/setup-node'))
