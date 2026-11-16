"""Tiny synthetic subprocesses only: no Quartz, Docker, network or CI run."""
import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
import bounded_command as diagnostic


class Diagnostics(unittest.TestCase):
    def run_fixture(self, script, **kw):
        return diagnostic.run_synthetic([sys.executable,'-c',script],cwd='/tmp',env={},label='template-1',**kw)

    def test_nonzero_diagnostic_preserves_failure(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaises(subprocess.CalledProcessError):
            self.run_fixture("import sys; print('Cannot find module fixture'); sys.exit(1)")
        record=json.loads(output.getvalue())
        self.assertEqual(record['exit_code'],1)
        self.assertIn('Cannot find module fixture',record['diagnostic_tail'])
        self.assertEqual(record['failure'],'nonzero_exit')

    def test_success_output_not_exported(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):self.run_fixture("print('synthetic success')")
        self.assertEqual(output.getvalue(),'')

    def test_redacts_secrets_and_workflow_commands_stay_json(self):
        secret='ghp_'+'a'*40
        text=diagnostic.sanitize(('Authorization: Bearer abc\n'+secret+'\npassword=abc\nhttps://host/?token=abc\n::error::synthetic\n\x1b[31mordinary error').encode())
        self.assertNotIn(secret,text);self.assertNotIn('abc',text)
        self.assertNotIn('\x1b',text)
        self.assertIn('ordinary error',text)
        self.assertTrue(json.dumps({'tail':text}).startswith('{'))

    def test_output_limit_fails_with_bounded_capture(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaisesRegex(RuntimeError,'output_limit'):
            self.run_fixture("print('x'*20000)",max_bytes=10000)
        record=json.loads(output.getvalue());self.assertLessEqual(record['captured_bytes'],10000)

    def test_timeout_remains_failure(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output),self.assertRaisesRegex(RuntimeError,'timeout'):
            self.run_fixture("import time;time.sleep(10)",timeout=0.1)
        self.assertEqual(json.loads(output.getvalue())['failure'],'timeout')

    def test_descendant_holding_pipe_obeys_deadline(self):
        output = io.StringIO()
        script = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']);sys.exit(1)"
        with contextlib.redirect_stdout(output), self.assertRaisesRegex(RuntimeError, 'timeout'):
            self.run_fixture(script, timeout=0.1)

    def test_nonzero_closed_pipe_descendant_is_killed(self):
        output = io.StringIO()
        script = "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);sys.exit(1)"
        import os
        with patch.object(diagnostic.os, 'killpg', wraps=os.killpg) as kill, contextlib.redirect_stdout(output), self.assertRaises(subprocess.CalledProcessError):
            self.run_fixture(script)
        kill.assert_called_once()

    def test_read_failure_still_kills_child_group(self):
        import os
        with patch.object(diagnostic, 'read_chunk', side_effect=OSError('fixture read failure')), patch.object(diagnostic.os, 'killpg', wraps=os.killpg) as kill:
            with self.assertRaises(OSError):
                self.run_fixture("import time;print('fixture',flush=True);time.sleep(10)")
        kill.assert_called_once()

    def test_rejects_unbounded_or_non_synthetic_label(self):
        with self.assertRaises(ValueError):self.run_fixture('',max_bytes=diagnostic.MAX_BYTES+1)
        with self.assertRaises(ValueError):
            diagnostic.run_synthetic([],cwd='/tmp',env={},label='real-user-data')
