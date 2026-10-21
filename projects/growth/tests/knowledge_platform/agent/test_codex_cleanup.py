"""Owned cleanup fault injection only; no real Windows, credentials or model calls."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.model import _codex_cleanup as cleanup
from ahadiff.knowledge_platform.model import codex_runner
from ahadiff.knowledge_platform.model.codex_runner import CodexModelRunner, CodexRunnerConfig
from ahadiff.knowledge_platform.model.diagnostic_probe import fixed_public_smoke_task, probe_codex
from ahadiff.knowledge_platform.model.pi_runner import SubscriptionRoute


def settings(tmp_path, scenario="arithmetic"):
    return CodexRunnerConfig(
        enabled=True, reuse_existing_login=True, development_transport=True,
        routes=(SubscriptionRoute(provider_id="openai", model_id="gpt-6.1-sol",
                                  account_qualified=True),),
        executable=(sys.executable, str(Path(__file__).with_name("fake_codex_exec.py")),
                    scenario, str(tmp_path / "calls.jsonl")),
    )


def denied():
    error = PermissionError(errno.EACCES, "never-log-secret", "never-log-path")
    error.winerror = 5
    return error


def calls(tmp_path):
    return [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]


def workspace(tmp_path):
    root = tmp_path / "owned"
    (root / "state").mkdir(parents=True)
    (root / "state" / "synthetic-file").write_text("synthetic")
    return root


def test_normal_cleanup_is_scoped_and_never_changes_permissions(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    sibling = tmp_path / "earlier-invocation"
    sibling.write_text("must remain")
    monkeypatch.setattr(os, "chmod", lambda *a, **kw: pytest.fail("No chmod allowed"))
    report = asyncio.run(cleanup.cleanup_owned_workspace(root))
    assert report == {"resource": "temporary_directory", "status": "completed",
                      "attempts": 1, "errors": [], "recovery": "none"}
    assert not root.exists() and sibling.read_text() == "must remain"


@pytest.mark.parametrize("persistent", [False, True])
def test_known_transient_lock_has_only_one_bounded_retry(tmp_path, monkeypatch, persistent):
    root = workspace(tmp_path)
    original = cleanup.shutil.rmtree
    attempts = []

    def locked(path):
        attempts.append(path)
        if persistent or len(attempts) == 1:
            error = OSError(errno.EBUSY, "never-log-secret", "never-log-path")
            error.winerror = 32
            raise error
        original(path)

    monkeypatch.setattr(cleanup.shutil, "rmtree", locked)
    report = asyncio.run(cleanup.cleanup_owned_workspace(root))
    assert attempts == [root, root] and report["attempts"] == 2
    assert report["status"] == ("failed" if persistent else "completed")
    assert len(report["errors"]) == (2 if persistent else 1)
    assert report["errors"][0] == {"failure_type": "os_error", "errno": errno.EBUSY,
                                   "winerror": 32}
    assert root.exists() is persistent
    assert "never-log" not in json.dumps(report)


def test_permission_error_is_retained_without_retry_chmod_or_force(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    attempts = []

    def fail(path):
        attempts.append(path)
        raise denied()

    monkeypatch.setattr(cleanup.shutil, "rmtree", fail)
    monkeypatch.setattr(os, "chmod", lambda *a, **kw: pytest.fail("No chmod allowed"))
    report = asyncio.run(cleanup.cleanup_owned_workspace(root))
    assert attempts == [root] and report["status"] == "failed"
    assert report["recovery"] == "owned_workspace_retained_for_review"
    assert root.is_dir() and "never-log" not in json.dumps(report)


@pytest.mark.parametrize("location", ["root", "child", "ancestor"])
def test_links_are_not_followed_or_deleted(tmp_path, monkeypatch, location):
    root = workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    if location == "root":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        target = link
    elif location == "ancestor":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        target = link / "owned"
    else:
        (root / "linked").symlink_to(outside, target_is_directory=True)
        target = root
    monkeypatch.setattr(cleanup.shutil, "rmtree", lambda *a, **kw: pytest.fail("Unsafe delete"))
    report = asyncio.run(cleanup.cleanup_owned_workspace(target))
    assert report["status"] == "failed" and report["attempts"] == 1
    assert report["errors"][0]["failure_type"] == "unsafe_path"
    assert root.exists() and (outside / "keep").read_text() == "keep"


def test_unknown_windows_reparse_attribute_rejects_before_delete(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    original = Path.lstat

    def metadata(path):
        value = original(path)
        if path == root:
            return SimpleNamespace(st_mode=value.st_mode,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return value

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(cleanup.shutil, "rmtree", lambda *a, **kw: pytest.fail("Unsafe delete"))
    report = asyncio.run(cleanup.cleanup_owned_workspace(root))
    assert report["status"] == "failed"
    assert report["errors"][0]["failure_type"] == "unsafe_path"


def test_inspection_failure_is_not_permission_to_delete(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    monkeypatch.setattr(cleanup.os, "scandir", lambda *a: (_ for _ in ()).throw(denied()))
    monkeypatch.setattr(cleanup.shutil, "rmtree", lambda *a, **kw: pytest.fail("Unsafe delete"))
    report = asyncio.run(cleanup.cleanup_owned_workspace(root))
    assert report["status"] == "failed" and report["attempts"] == 1
    assert report["errors"][0]["winerror"] == 5


@pytest.fixture
def retained_workspace(tmp_path, monkeypatch):
    import tempfile

    original = tempfile.mkdtemp
    created = []

    def create(*, prefix):
        result = original(prefix=prefix, dir=tmp_path)
        created.append(Path(result))
        return result

    async def failed_cleanup(root):
        assert root in created
        return {"resource": "temporary_directory", "status": "failed", "attempts": 1,
                "errors": [cleanup.cleanup_failure(denied())],
                "recovery": "owned_workspace_retained_for_review"}

    monkeypatch.setattr(tempfile, "mkdtemp", create)
    monkeypatch.setattr(codex_runner, "cleanup_owned_workspace", failed_cleanup)
    return created


@pytest.mark.parametrize(("scenario", "code"), [
    ("notification_boundary", "CODEX_RUNTIME_BOUNDARY_VIOLATION"),
    ("wrong_version", "CODEX_VERSION_MISMATCH"),
    ("auth_missing", "SUBSCRIPTION_AUTH"),
    ("provider_failure_transport", "PROVIDER_FAILURE"),
])
def test_workspace_cleanup_never_replaces_primary_failure(
    tmp_path, retained_workspace, scenario, code
):
    report = asyncio.run(probe_codex(settings(tmp_path, scenario), model_smoke=True))
    assert report["model"]["error_code"] == code
    assert report["model"]["answer_matches_fixed_schema"] is False
    assert report["cleanup"][-1]["resource"] == "temporary_directory"
    assert report["cleanup"][-1]["status"] == "failed"
    assert report["cleanup"][-1]["errors"][0]["winerror"] == 5
    assert all(p.exists() for p in retained_workspace)
    assert "never-log" not in json.dumps(report) and str(tmp_path) not in json.dumps(report)


def test_successful_answer_with_failed_cleanup_is_not_success_or_cached(
    tmp_path, retained_workspace
):
    runner = CodexModelRunner(settings(tmp_path))

    async def run():
        first = await runner.run(fixed_public_smoke_task())
        before = calls(tmp_path)
        second = await runner.run(fixed_public_smoke_task())
        assert calls(tmp_path) == before
        return first, second

    first, second = asyncio.run(run())
    assert first.status == second.status == "blocked"
    assert first.error_code == "CODEX_CLEANUP_FAILED" and first.output is None
    assert first.failure.stage == "cleanup"
    assert second.error_code == "CODEX_CLEANUP_PENDING" and second.output is None
    assert not runner._cache


def test_host_preflight_success_cannot_hide_failed_workspace_cleanup(tmp_path, retained_workspace):
    report = asyncio.run(probe_codex(settings(tmp_path)))
    assert report["status"] == "blocked" and report["error_code"] == "CODEX_CLEANUP_FAILED"
    assert report["failure"]["stage"] == "cleanup" and report["model_attempted"] is False


def test_already_queued_call_cannot_bypass_new_cleanup_failure(tmp_path, retained_workspace):
    runner = CodexModelRunner(settings(tmp_path))

    async def run():
        return await asyncio.gather(
            runner.run(fixed_public_smoke_task()), runner.run(fixed_public_smoke_task())
        )

    results = asyncio.run(run())
    assert [r.error_code for r in results] == ["CODEX_CLEANUP_FAILED", "CODEX_CLEANUP_PENDING"]
    assert len(retained_workspace) == 1
    assert sum("exec" in item["args"] for item in calls(tmp_path)) == 1


def test_missing_owned_directory_is_idempotent_but_relative_path_is_rejected(tmp_path):
    report = asyncio.run(cleanup.cleanup_owned_workspace(tmp_path / "already-removed"))
    assert report["status"] == "completed" and report["attempts"] == 1
    report = asyncio.run(cleanup.cleanup_owned_workspace(Path("relative-path")))
    assert report["status"] == "failed"
    assert report["errors"][0]["failure_type"] == "unsafe_path"


def test_primary_cancellation_survives_workspace_cleanup_failure(tmp_path, retained_workspace):
    runner = CodexModelRunner(settings(tmp_path))

    async def run():
        async with runner._workspace("zhizhi-codex-"):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
    assert runner.cleanup_diagnostics[-1]["status"] == "failed"


@pytest.mark.parametrize("scenario", ["notification_boundary", "arithmetic"])
def test_process_cleanup_is_independent_and_blocks_workspace_deletion(
    tmp_path, monkeypatch, scenario
):
    import tempfile

    original_temp = tempfile.mkdtemp
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *, prefix: original_temp(prefix=prefix, dir=tmp_path)
    )
    original_stop = codex_runner._stop
    stopped = []

    async def stop(process, job=None):
        await original_stop(process, job)
        stopped.append(process.pid)
        if len(stopped) == 3:
            raise denied()

    monkeypatch.setattr(codex_runner, "_stop", stop)
    monkeypatch.setattr(codex_runner, "cleanup_owned_workspace",
                        lambda *a: pytest.fail("Process cleanup not confirmed"))
    report = asyncio.run(probe_codex(settings(tmp_path, scenario), model_smoke=True))
    expected = ("CODEX_RUNTIME_BOUNDARY_VIOLATION" if scenario == "notification_boundary"
                else "CODEX_CLEANUP_FAILED")
    assert report["model"]["error_code"] == expected
    assert report["model"]["answer_matches_fixed_schema"] is False
    process = report["processes"][-1]
    assert process["cleanup"]["status"] == "failed"
    assert process["cleanup"]["errors"][0]["winerror"] == 5
    assert process["failure_type"] == (
        "adapter_error" if scenario == "notification_boundary" else None
    )
    assert process["errno"] is None and process["winerror"] is None
    assert report["cleanup"][-1]["attempts"] == 0
    assert sum("exec" in item["args"] for item in calls(tmp_path)) == 1
    assert "never-log" not in json.dumps(report)


@pytest.mark.parametrize("join_error", ["cancelled", "os_error"])
@pytest.mark.parametrize("stop_error", [False, True])
@pytest.mark.parametrize("primary_notice", [False, True])
def test_reader_join_failure_still_stops_and_preserves_primary(
    tmp_path, monkeypatch, join_error, stop_error, primary_notice
):
    """Fully synthetic process: cancellation must not permit workspace deletion."""
    runner = CodexModelRunner(settings(tmp_path), collect_process_diagnostics=True,
                              collect_notification_diagnostics=True)
    protocol = [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
    ]
    if primary_notice:
        protocol.append({"type": "item.completed", "item": {
            "type": "error", "message": codex_runner._SANDBOX_DISABLED_NOTICE,
        }})
    protocol.extend([
        {"type": "item.completed", "item": {"type": "agent_message", "text": "isolated"}},
        {"type": "turn.completed", "usage": {"output_tokens": 1}},
    ])
    lines = iter([json.dumps(item).encode() + b"\n" for item in protocol] + [b""])
    stopped = []

    async def readline():
        return next(lines, b"")

    async def empty(*_):
        return b""

    async def wait():
        return 0

    process = SimpleNamespace(
        pid=99999999, returncode=None,
        stdin=SimpleNamespace(write=lambda _: None, drain=empty, close=lambda: None),
        stdout=SimpleNamespace(readline=readline, read=empty),
        stderr=SimpleNamespace(read=empty), wait=wait,
    )

    async def spawn(*_, **__):
        return process

    async def stop(*_):
        stopped.append(True)
        if stop_error:
            raise asyncio.CancelledError()

    original_gather = asyncio.gather

    async def failed_join():
        if join_error == "cancelled":
            raise asyncio.CancelledError()
        raise denied()

    def gather(*args, **kwargs):
        if kwargs.get("return_exceptions"):
            return failed_join()
        return original_gather(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "gather", gather)
    monkeypatch.setattr(codex_runner, "_stop", stop)
    monkeypatch.setattr(codex_runner, "cleanup_owned_workspace",
                        lambda *a: pytest.fail("Unknown cleanup must retain directory"))
    import tempfile
    original_temp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp",
                        lambda *, prefix: original_temp(prefix=prefix, dir=tmp_path))

    async def run():
        async with runner._workspace("zhizhi-codex-") as cwd:
            await runner._process([], cwd, events=codex_runner._ExecEvents(20,
                                                                          notification_details=True))

    from ahadiff.knowledge_platform.model.pi_runner import RpcFailure
    expected_error = (RpcFailure if primary_notice or join_error == "os_error"
                      else asyncio.CancelledError)
    expected_code = ("CODEX_RUNTIME_BOUNDARY_VIOLATION" if primary_notice
                     else "CODEX_CLEANUP_FAILED" if join_error == "os_error" else None)
    with pytest.raises(expected_error, match=expected_code):
        asyncio.run(run())
    assert stopped == [True] and runner._cleanup_pending
    process_report = runner.process_diagnostics[0]
    assert process_report["failure_type"] == ("adapter_error" if primary_notice else None)
    if primary_notice:
        assert process_report["event_counts"]["item.error"] == 1
        assert process_report["notification"]["code"] == "credential_masking_sandbox_disabled"
    else:
        assert process_report["event_counts"] == {
            "thread.started": 1, "turn.started": 1, "item.completed": 1, "turn.completed": 1,
        }
    assert process_report["provider_turn_state"] == "completed"
    assert process_report["terminal_event"] == "turn.completed"
    errors = process_report["cleanup"]["errors"]
    assert errors[0]["operation"] == "reader_join"
    assert errors[0]["failure_type"] == join_error
    assert len(errors) == (2 if stop_error else 1)
    if stop_error:
        assert errors[1]["operation"] == "owned_stop"
        assert errors[1]["failure_type"] == "cancelled"
    assert runner.cleanup_diagnostics[-1]["attempts"] == 0
    assert "never-log" not in json.dumps(runner.process_diagnostics)


def test_previous_success_cache_is_inaccessible_after_later_cleanup_failure(tmp_path, monkeypatch):
    import tempfile
    from dataclasses import replace
    original_temp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp",
                        lambda *, prefix: original_temp(prefix=prefix, dir=tmp_path))
    runner = CodexModelRunner(settings(tmp_path))
    first_task = fixed_public_smoke_task()
    second_task = replace(first_task, task_id="synthetic-second", dedupe_key="synthetic-second")

    async def failed_cleanup(_):
        return {"resource": "temporary_directory", "status": "failed", "attempts": 1,
                "errors": [cleanup.cleanup_failure(denied())],
                "recovery": "owned_workspace_retained_for_review"}

    async def run():
        first = await runner.run(first_task)
        assert first.status == "succeeded" and first_task.dedupe_key in runner._cache
        monkeypatch.setattr(codex_runner, "cleanup_owned_workspace", failed_cleanup)
        second = await runner.run(second_task)
        before = calls(tmp_path)
        replay = await runner.run(first_task)
        assert calls(tmp_path) == before
        return second, replay

    second, replay = asyncio.run(run())
    assert second.error_code == "CODEX_CLEANUP_FAILED"
    assert replay.error_code == "CODEX_CLEANUP_PENDING" and replay.output is None
    assert first_task.dedupe_key in runner._cache


def test_setup_primary_and_handle_cleanup_are_independent_and_latched(tmp_path, monkeypatch):
    import tempfile
    original_temp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp",
                        lambda *, prefix: original_temp(prefix=prefix, dir=tmp_path))
    runner = CodexModelRunner(settings(tmp_path), collect_process_diagnostics=True)
    process = SimpleNamespace(pid=99999999, returncode=None)
    stopped = []
    primary = denied()
    secondary = OSError(6, "never-log-secret", "never-log-path")
    secondary.winerror = 6
    primary.codex_cleanup_error = secondary

    def failed_job():
        raise primary

    async def spawn(*_, **__):
        return process

    async def stop(*_):
        stopped.append(True)

    monkeypatch.setattr(codex_runner, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(codex_runner, "CodexOwnedJob", failed_job)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(codex_runner, "_stop", stop)
    monkeypatch.setattr(codex_runner, "cleanup_owned_workspace",
                        lambda *a: pytest.fail("Unclosed owned handle must retain directory"))

    async def run():
        async with runner._workspace("zhizhi-codex-") as cwd:
            await runner._process([], cwd)

    with pytest.raises(OSError) as caught:
        asyncio.run(run())
    assert caught.value is primary and stopped == [True] and runner._cleanup_pending
    report = runner.process_diagnostics[0]
    assert report["winerror"] == 5 and report["failure_type"] == "os_error"
    assert report["cleanup"]["errors"] == [{
        "operation": "job_setup_handle_close", "failure_type": "os_error",
        "errno": 6, "winerror": 6,
    }]
    assert runner.cleanup_diagnostics[-1]["attempts"] == 0
    assert "never-log" not in json.dumps(runner.process_diagnostics)
