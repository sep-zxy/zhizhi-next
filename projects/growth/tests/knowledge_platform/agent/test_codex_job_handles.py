"""Windows handle ABI failure simulation only, never real handles or OS mutation."""
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.model import _codex_job as job_module


class Call:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.value


@pytest.fixture
def kernel(monkeypatch):
    api = SimpleNamespace(
        CreateJobObjectW=Call(101), SetInformationJobObject=Call(True),
        OpenProcess=Call(202), AssignProcessToJobObject=Call(True), CloseHandle=Call(True),
    )
    monkeypatch.setattr(job_module.ctypes, "WinDLL", lambda *a, **kw: api, raising=False)
    error_numbers = iter(range(5, 50))
    monkeypatch.setattr(job_module.ctypes, "get_last_error", lambda: next(error_numbers),
                        raising=False)

    def winerror(number):
        error = OSError(number, "never-log-secret", "never-log-path")
        error.winerror = number
        return error

    monkeypatch.setattr(job_module.ctypes, "WinError", winerror, raising=False)
    return api


def test_close_false_preserves_handle_and_reports_immediate_error(kernel):
    job = job_module.CodexOwnedJob()
    kernel.CloseHandle.value = False
    with pytest.raises(OSError) as caught:
        job.close()
    assert caught.value.winerror == 5 and job.handle == 101
    assert kernel.CloseHandle.calls == [(101,)]
    kernel.CloseHandle.value = True
    job.close()
    assert job.handle is None
    job.close()
    assert kernel.CloseHandle.calls == [(101,), (101,)]


def test_setup_error_remains_primary_when_handle_close_also_fails(kernel):
    kernel.SetInformationJobObject.value = False
    kernel.CloseHandle.value = False
    with pytest.raises(OSError) as caught:
        job_module.CodexOwnedJob()
    assert caught.value.winerror == 5
    assert caught.value.codex_cleanup_error.winerror == 6
    assert kernel.CloseHandle.calls == [(101,)]


def test_assignment_error_remains_primary_when_process_handle_close_fails(kernel):
    job = job_module.CodexOwnedJob()
    kernel.AssignProcessToJobObject.value = False
    kernel.CloseHandle.value = False
    with pytest.raises(OSError) as caught:
        job.assign(33)
    assert caught.value.winerror == 5
    assert caught.value.codex_cleanup_error.winerror == 6
    assert job.handle == 101
    assert kernel.CloseHandle.calls == [(202,)]


def test_successful_assignment_is_not_clean_when_process_handle_close_fails(kernel):
    job = job_module.CodexOwnedJob()
    kernel.CloseHandle.value = False
    with pytest.raises(OSError) as caught:
        job.assign(33)
    assert caught.value.winerror == 5
    assert caught.value.codex_cleanup_error.winerror == 5
    assert job.handle == 101
