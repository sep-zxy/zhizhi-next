"""Offline probe/OS-shim tests, not real authentication or Windows execution."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.model import CodexRunnerConfig, SubscriptionRoute
from ahadiff.knowledge_platform.model.diagnostic_probe import SMOKE_MODEL, probe_codex


def config(tmp_path, scenario="arithmetic"):
    return CodexRunnerConfig(
        enabled=True,
        reuse_existing_login=True,
        routes=(
            SubscriptionRoute(
                provider_id="openai", model_id=SMOKE_MODEL, account_qualified=True,
                allowed_classifications=("public",),
            ),
        ),
        executable=(
            sys.executable,
            str(Path(__file__).with_name("fake_codex_exec.py")),
            scenario,
            str(tmp_path / "calls.jsonl"),
        ),
        development_transport=True,
    )


def test_default_probe_never_executes_model_or_persists_raw_data(tmp_path):
    report = asyncio.run(probe_codex(config(tmp_path)))
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["args"] for call in calls] == [["--version"], ["login", "status"]]
    assert report["status"] == "host_preflight_passed"
    assert report["mode"] == "host_only" and report["model_attempted"] is False
    assert report["model"] is None and report["fixture"] is True
    assert report["platform_e2e"] == "not_run"
    assert report["preflight"] == {"version": "verified", "login": "chatgpt"}
    for process in report["processes"]:
        assert process["exit_code"] == 0
        assert process["stdout_bytes"] + process["stderr_bytes"] > 0
        assert process["event_counts"] == {} and process["terminal_event"] is None
        assert process["steps"][-1]["name"] == "cleanup_complete"
    encoded = json.dumps(report)
    for secret in (str(tmp_path), sys.executable, "Logged in using", "untrusted_payload"):
        assert secret not in encoded


def test_model_smoke_is_explicit_fixed_and_fixture_labeled(tmp_path):
    report = asyncio.run(probe_codex(config(tmp_path), model_smoke=True))
    assert report["status"] == "fixture_model_smoke_passed"
    assert report["model_attempted"] is True
    assert report["model"]["answer_matches_fixed_schema"] is True
    assert report["model"]["actual_model_id"] is None
    assert report["model"]["fixture"] is True
    assert report["platform_e2e"] == "not_run"
    execute = report["processes"][-1]
    assert execute["phase"] == "execution"
    assert execute["event_counts"] == {
        "thread.started": 1, "turn.started": 1, "item.completed": 1, "turn.completed": 1,
    }
    assert execute["terminal_event"] == "turn.completed"
    assert execute["exit_code"] == 0
    assert "result_json" not in json.dumps(report)


@pytest.mark.parametrize(
    ("scenario", "status", "version", "login"),
    [
        ("wrong_version", "blocked", "mismatch", "not_checked"),
        ("auth_missing", "waiting_auth", "verified", "not_authenticated"),
        ("api_key", "blocked", "verified", "api_key_rejected"),
    ],
)
def test_default_preflight_does_not_probe_a_model_on_failure(
    tmp_path, scenario, status, version, login
):
    report = asyncio.run(probe_codex(config(tmp_path, scenario)))
    assert report["status"] == status
    assert report["preflight"] == {"version": version, "login": login}
    assert report["model_attempted"] is False
    assert '"exec"' not in (tmp_path / "calls.jsonl").read_text()
    assert "never-a-real-key" not in json.dumps(report)


def test_probe_captures_failed_terminal_counts_without_provider_text(tmp_path):
    report = asyncio.run(
        probe_codex(config(tmp_path, "provider_failure_transport"), model_smoke=True)
    )
    assert report["status"] == "model_smoke_not_passed"
    assert report["failure"]["summary"] == "transport_connection_failed"
    execute = report["processes"][-1]
    assert execute["terminal_event"] == "turn.failed"
    assert execute["event_counts"]["turn.failed"] == 1
    assert execute["failure_type"] == "adapter_error"
    assert execute["exit_code"] in {None, 1}
    assert "never-log-me" not in json.dumps(report)


def test_probe_opt_in_disabled_means_no_process(tmp_path):
    report = asyncio.run(probe_codex(replace(config(tmp_path), reuse_existing_login=False)))
    assert report["status"] == "blocked" and report["processes"] == []
    assert not (tmp_path / "calls.jsonl").exists()


def test_probe_separates_nonfatal_notification_rejection_from_provider_terminal_failure(tmp_path):
    report = asyncio.run(
        probe_codex(config(tmp_path, "notification_warning"), model_smoke=True)
    )
    assert report["status"] == "model_smoke_not_passed"
    assert report["model"]["status"] == "blocked"
    assert report["model"]["error_code"] == "CODEX_NOTIFICATION_REQUIRES_REVIEW"
    assert report["failure"]["summary"] == "cli_notification_requires_review"
    execute = report["processes"][-1]
    assert execute["event_counts"]["item.error"] == 1
    assert execute["terminal_event"] is None
    assert "never-log-me" not in json.dumps(report)


@pytest.mark.parametrize("failing_step", ["job_create", "job_assign"])
def test_windows_job_failure_phase_and_errno_use_a_safe_os_shim(
    tmp_path, monkeypatch, failing_step
):
    from ahadiff.knowledge_platform.model import codex_runner

    class SimulatedJob:
        def __init__(self):
            if failing_step == "job_create":
                self.fail()

        def assign(self, pid):
            self.fail()

        def close(self):
            # The stdin-gated helper has not started a CLI. _stop closes stdin,
            # which makes the helper exit without starting any child.
            pass

        @staticmethod
        def fail():
            error = OSError(5, "never-log-me", "never-log-private-path")
            error.winerror = 5
            raise error

    monkeypatch.setattr(codex_runner, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(codex_runner, "CodexOwnedJob", SimulatedJob)
    report = asyncio.run(probe_codex(config(tmp_path)))
    assert report["status"] == "blocked" and report["model_attempted"] is False
    observed = report["processes"][0]
    assert observed["steps"][-1]["name"] == failing_step
    assert observed["failure_type"] == "os_error"
    assert observed["errno"] == 5 and observed["winerror"] == 5
    assert "stdin_released" not in [step["name"] for step in observed["steps"]]
    assert not (tmp_path / "calls.jsonl").exists()
    assert "never-log" not in json.dumps(report)
