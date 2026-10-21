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
    assert report["expected_cli_version"] == "0.163.0-alpha.11"
    assert report["preflight"] == {"version": "verified", "login": "chatgpt"}
    for process in report["processes"]:
        assert process["exit_code"] == 0
        assert process["stdout_bytes"] + process["stderr_bytes"] > 0
        assert process["event_counts"] == {} and process["terminal_event"] is None
        assert process["steps"][-1]["name"] == "cleanup_complete"
    encoded = json.dumps(report)
    for secret in (str(tmp_path), sys.executable, "Logged in using", "untrusted_payload"):
        assert secret not in encoded


def test_explicit_previous_pin_is_reported_without_authentication_or_model_fallback(tmp_path):
    settings = replace(config(tmp_path, "reviewed_previous_version"),
                       expected_version="0.163.0-alpha.10.2")
    report = asyncio.run(probe_codex(settings))
    assert report["expected_cli_version"] == "0.163.0-alpha.10.2"
    assert report["status"] == "host_preflight_passed" and report["fixture"] is True
    assert report["model_attempted"] is False and report["platform_e2e"] == "not_run"
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [call["args"] for call in calls] == [["--version"], ["login", "status"]]


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


@pytest.mark.parametrize(
    ("scenario", "code"),
    [("notification_warning", "unknown_notification"),
     ("notification_reroute", "model_reroute_notice")],
)
def test_fixed_smoke_notification_hints_do_not_accept_later_valid_answer(tmp_path, scenario, code):
    report = asyncio.run(probe_codex(config(tmp_path, scenario), model_smoke=True))
    assert report["model"]["status"] == "blocked"
    assert report["model"]["error_code"] == "CODEX_NOTIFICATION_REQUIRES_REVIEW"
    assert report["model"]["answer_matches_fixed_schema"] is False
    assert report["model"]["actual_model_id"] is None
    process = report["processes"][-1]
    notice = process["notification"]
    assert notice == {
        "classifier_version": 2, "rule_id": "codex_exec_notice_v2:" + code,
        "input_utf8_bytes": notice["input_utf8_bytes"],
        "inspected_utf8_bytes": notice["input_utf8_bytes"],
        "code": code, "evidence": "bounded_text_pattern_hint",
        "upstream_kind": "not_preserved_by_exec_jsonl",
        "message_present": True, "truncated": False,
    }
    assert process["terminal_event"] is None
    assert process["steps"][-1]["name"] == "cleanup_complete"
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert sum("exec" in call["args"] for call in calls) == 1
    encoded = json.dumps(report)
    for forbidden in ("never-log-me", "fallback-model", "private detail", str(tmp_path)):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("Credential masking is inactive because sandboxing is disabled. secret",
         "credential_masking_sandbox_disabled"),
        ("Credential masking is inactive because the network proxy is disabled. secret",
         "credential_masking_proxy_disabled"),
        ("Credential masking is inactive because shell environment overrides contain secret",
         "credential_masking_environment_conflict"),
        ("Configured value for `secret` is disallowed by requirements; falling back to secret",
         "required_configuration_override"),
        ("Under-development features enabled: secret", "unstable_features_notice"),
        ("in-process app-server event stream lagged; dropped 7 events", "event_stream_loss_notice"),
        ("`--dangerously-bypass-hook-trust` is enabled. secret", "hook_trust_bypass_notice"),
        ("invalid global instructions: secret", "invalid_global_instructions_notice"),
        ("`secret` is deprecated. secret", "deprecation_text_notice"),
        ("provider-code=secret; URL=https://secret.invalid; user=secret", "unknown_notification"),
        ({"message": "secret"}, "unknown_notification"),
    ],
)
def test_notification_classifier_has_no_provider_text_or_dynamic_code(message, code):
    from ahadiff.knowledge_platform.model.diagnostics import notification_diagnostic
    observed = notification_diagnostic(message)
    assert observed["code"] == code
    assert observed["upstream_kind"] == "not_preserved_by_exec_jsonl"
    assert "secret" not in json.dumps(observed)
    assert observed["message_present"] is isinstance(message, str)


def test_notification_classifier_is_bounded_and_parser_collection_default_off():
    from ahadiff.knowledge_platform.model.codex_runner import _ExecEvents
    from ahadiff.knowledge_platform.model.diagnostics import notification_diagnostic
    from ahadiff.knowledge_platform.model.pi_runner import RpcFailure
    observed = notification_diagnostic("x" * 8192 + " is deprecated")
    assert observed["code"] == "unknown_notification" and observed["truncated"] is True
    events = _ExecEvents(4096)
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_REQUIRES_REVIEW"):
        events.accept(b'{"type":"item.completed","item":{"type":"error","message":"secret"}}')
    assert events.notification is None and events.completed is False


@pytest.mark.parametrize("message", [
    "SECRET prefix; model rerouted: benign",
    "MODEL REROUTED: private-account -> private-model (private-reason)",
    "Under-development features enabled: benign; credential=never-log-me",
    "is deprecated; token=never-log-me",
    "私" * 8192 + " is deprecated",
])
def test_malicious_text_and_byte_bound_cannot_authorize_a_turn(message):
    from ahadiff.knowledge_platform.model.codex_runner import _ExecEvents
    from ahadiff.knowledge_platform.model.pi_runner import RpcFailure
    events = _ExecEvents(4096, notification_details=True)
    raw = json.dumps(
        {"type": "item.completed", "item": {"type": "error", "message": message}}
    ).encode()
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_REQUIRES_REVIEW"):
        events.accept(raw)
    notice = events.notification
    assert notice is not None
    assert notice["input_utf8_bytes"] == len(message.encode())
    assert notice["inspected_utf8_bytes"] <= 8192
    assert notice["truncated"] is (len(message.encode()) > 8192)
    assert events.completed is False and events.text is None
    assert "never-log-me" not in json.dumps(notice) and "private-" not in json.dumps(notice)
    assert set(notice) == {"classifier_version", "rule_id", "code", "evidence", "upstream_kind",
                           "message_present", "input_utf8_bytes",
                           "inspected_utf8_bytes", "truncated"}


def test_first_of_multiple_notifications_still_stops_smoke(tmp_path):
    report = asyncio.run(probe_codex(config(tmp_path, "notification_multiple"), model_smoke=True))
    process = report["processes"][-1]
    assert report["model"]["status"] == "blocked"
    assert process["notification"]["code"] == "unknown_notification"
    assert process["event_counts"]["item.error"] == 1
    assert process["terminal_event"] is None
    assert report["model"]["answer_matches_fixed_schema"] is False
