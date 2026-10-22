"""Deterministic subprocess fixtures only; no model or Windows E2E claim."""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from ahadiff.knowledge_platform import git_process as gp


def run(script, tmp_path, *, input_data=None, timeout=2):
    return gp.run_git([sys.executable, '-c', script], cwd=tmp_path,
                      env=os.environ.copy(), input_data=input_data, timeout=timeout)


@pytest.mark.parametrize('payload', [None, b'', b'fixture\0input\n' * 20000], ids=['none', 'empty', 'large'])
def test_explicit_input_and_no_inherited_protocol_input(tmp_path, payload):
    result = run('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())',
                 tmp_path, input_data=payload)
    assert result.returncode == 0
    assert result.stdout == (payload or b'')
    assert result.stderr == b''


def not_running(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split()[2] == 'Z'
    except FileNotFoundError:
        return True


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-group fixture')
@pytest.mark.parametrize('parent_exits', [False, True])
def test_descendant_holding_inherited_handles_is_owned_and_reaped(tmp_path, parent_exits):
    marker = tmp_path / 'fixture-child-pid'
    child = f"import os,time;open({str(marker)!r},'w').write(str(os.getpid()));time.sleep(60)"
    script = (f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{child!r}]);'
              + ('time.sleep(.15)' if parent_exits else 'time.sleep(60)'))
    start = time.monotonic()
    if parent_exits:
        assert run(script, tmp_path).returncode == 0
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            run(script, tmp_path, timeout=.5)
    assert time.monotonic() - start < 2
    pid = int(marker.read_text())
    limit = time.monotonic() + 1
    while not not_running(pid) and time.monotonic() < limit:
        time.sleep(.01)
    assert not_running(pid)


def test_nonreading_child_large_input_is_bounded(tmp_path):
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run('import time; time.sleep(60)', tmp_path, input_data=b'x' * 2000000, timeout=.2)
    assert time.monotonic() - start < 2


def test_windows_job_assignment_precedes_gate_and_preserves_payload(tmp_path, monkeypatch):
    events = []
    class Pipe:
        def write(self, data):
            events.append(('write', bytes(data)))
            return len(data)
        def close(self):
            events.append(('stdin_close', None))
    process = Mock(pid=123, returncode=0, stdin=Pipe())
    process.poll.return_value = 0
    process.wait.side_effect = lambda **kwargs: events.append(('wait', None))
    def popen(command, **kwargs):
        assert kwargs['bufsize'] == 0
        assert kwargs['start_new_session'] is False
        assert kwargs['stdin'] == subprocess.PIPE
        assert "subprocess.DEVNULL" in command[4]
        events.append(('spawn', None))
        return process
    job = Mock()
    job.assign.side_effect = lambda pid: events.append(('assign', pid))
    job.close.side_effect = lambda: events.append(('job_close', None))
    monkeypatch.setattr(gp.os, 'name', 'nt')
    monkeypatch.setattr(gp, 'CodexOwnedJob', lambda: job)
    monkeypatch.setattr(gp.subprocess, 'Popen', popen)
    result = gp.run_git(['fake-git'], cwd=tmp_path, env={}, input_data=b'fixture')
    assert result.returncode == 0
    assert events.index(('assign', 123)) < events.index(('write', b'\0fixture'))
    assert ('job_close', None) in events


def test_windows_assignment_failure_never_releases_gate(tmp_path, monkeypatch):
    process = Mock(pid=123, returncode=0)
    process.poll.return_value = None
    job = Mock()
    job.assign.side_effect = OSError('fixture only')
    monkeypatch.setattr(gp.os, 'name', 'nt')
    monkeypatch.setattr(gp, 'CodexOwnedJob', lambda: job)
    monkeypatch.setattr(gp.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(OSError):
        gp.run_git(['fake-git'], cwd=tmp_path, env={})
    process.stdin.write.assert_not_called()
    job.close.assert_called_once()
    process.kill.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)


def test_windows_close_failure_terminates_same_job_and_still_fails(tmp_path, monkeypatch):
    process = Mock(pid=123, returncode=0)
    process.poll.return_value = 0
    process.stdin.write.side_effect = lambda value: len(value)
    job = Mock()
    job.close.side_effect = [OSError('synthetic private exception must not escape'), None]
    monkeypatch.setattr(gp.os, 'name', 'nt')
    monkeypatch.setattr(gp, 'CodexOwnedJob', lambda: job)
    monkeypatch.setattr(gp.subprocess, 'Popen', lambda *a, **k: process)
    with pytest.raises(RuntimeError, match='^Git process cleanup failed$'):
        gp.run_git(['fake-git'], cwd=tmp_path, env={})
    job.terminate.assert_called_once_with()
    assert job.close.call_count == 2
