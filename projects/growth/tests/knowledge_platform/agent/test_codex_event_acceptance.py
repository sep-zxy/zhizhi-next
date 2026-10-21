"""Official JSONL state/policy contract; all model and process inputs are synthetic."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest
from test_codex_runner import runner, task

from ahadiff.knowledge_platform.model._codex_notice_templates import EXACT_NOTIFICATION_TEMPLATES
from ahadiff.knowledge_platform.model.codex_runner import _ExecEvents
from ahadiff.knowledge_platform.model.pi_runner import RpcFailure

NOTICES = {code: text for text, code in EXACT_NOTIFICATION_TEMPLATES.items()}
INFORMATIONAL = (NOTICES["transcript_v2_deprecation"], NOTICES["compaction_accuracy_notice"])
UNKNOWN = "runtime notice with synthetic-private-sentinel"


def emit(events, kind, **fields):
    events.accept(json.dumps({"type": kind, **fields}).encode())


def notice(events, text):
    emit(events, "item.completed", item={"type": "error", "message": text})


def start(events):
    emit(events, "thread.started", thread_id="synthetic-thread")
    emit(events, "turn.started")


def complete(events):
    emit(events, "item.completed", item={"type": "agent_message", "text": "isolated result"})
    emit(events, "turn.completed", usage={"output_tokens": 2})


@pytest.mark.parametrize("details", [False, True])
@pytest.mark.parametrize("text", INFORMATIONAL)
@pytest.mark.parametrize("startup", [False, True])
def test_two_exact_informational_notices_do_not_reject_natural_success(details, text, startup):
    events = _ExecEvents(20, cli_version="0.163.0-alpha.11", notification_details=details)
    if startup:
        notice(events, text)
    start(events)
    if not startup:
        notice(events, text)
    complete(events)
    events.finish(0)
    assert events.completed and events.provider_turn_state == "completed"
    assert events.notification_policy == "informational"
    assert (events.notification is not None) == details


@pytest.mark.parametrize("version", [None, "0.163.0-alpha.10.2", "0.163.0-alpha.12"])
@pytest.mark.parametrize("text", INFORMATIONAL)
def test_informational_permission_does_not_cross_version_boundary(version, text):
    events = _ExecEvents(20, cli_version=version)
    start(events)
    notice(events, text)
    complete(events)
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_REQUIRES_REVIEW"):
        events.finish(0)
    assert events.provider_turn_state == "completed"


@pytest.mark.parametrize("text", [UNKNOWN, "model rerouted: approved -> unapproved (other)",
    "in-process app-server event stream lagged; dropped 1 events",
    "Codex is ignoring 1 unrecognized configuration setting.",
    NOTICES["model_metadata_missing_fixed_model"], NOTICES["code_mode_unsupported_fixed_model"],
    NOTICES["guardian_thread_context_deprecation"], NOTICES["stop_hook_empty_continuation"],
    INFORMATIONAL[0] + "\n", "prefix " + INFORMATIONAL[0]])
@pytest.mark.parametrize("first", [False, True])
def test_unknown_or_sensitive_notice_dominates_benign_in_either_order(text, first):
    events = _ExecEvents(20, cli_version="0.163.0-alpha.11")
    start(events)
    for value in ([text, INFORMATIONAL[0]] if first else [INFORMATIONAL[0], text]):
        notice(events, value)
    complete(events)
    assert events.completed and events.notification_policy == "requires_review"
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_REQUIRES_REVIEW"):
        events.finish(0)


@pytest.mark.parametrize("kind", ["item.completed", "error"])
def test_notification_count_has_independent_bound(kind):
    events = _ExecEvents(20, cli_version="0.163.0-alpha.11")
    start(events)
    for _ in range(16):
        if kind == "error":
            emit(events, kind, message=UNKNOWN)
        else:
            notice(events, INFORMATIONAL[0])
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_LIMIT"):
        if kind == "error":
            emit(events, kind, message=UNKNOWN)
        else:
            notice(events, INFORMATIONAL[0])


@pytest.mark.parametrize("terminal", ["turn.completed", "turn.failed"])
@pytest.mark.parametrize("extra", ["turn.completed", "turn.failed", "turn.started", "notice"])
def test_any_event_after_either_terminal_is_rejected(terminal, extra):
    events = _ExecEvents(20)
    start(events)
    emit(events, terminal, usage={"output_tokens": 1}, error={"message": "synthetic failure"})
    with pytest.raises(RpcFailure, match="CODEX_EVENT_AFTER_COMPLETION"):
        if extra == "notice":
            notice(events, INFORMATIONAL[0])
        else:
            emit(events, extra, usage={"output_tokens": 1}, error={"message": "failure"})


@pytest.mark.parametrize("kind", ["turn.started", "turn.completed", "turn.failed"])
def test_turn_sequence_cannot_be_fabricated_without_thread(kind):
    with pytest.raises(RpcFailure, match="CODEX_UNEXPECTED_EVENT"):
        emit(_ExecEvents(20), kind, usage={"output_tokens": 1}, error={"message": "failure"})


def test_top_level_error_completed_and_exit_zero_is_not_auth_failure_or_success():
    events = _ExecEvents(20, cli_version="0.163.0-alpha.11")
    start(events)
    emit(events, "error", message="401 synthetic transient error")
    notice(events, INFORMATIONAL[0])
    complete(events)
    with pytest.raises(RpcFailure, match="CODEX_STREAM_ERROR_REQUIRES_REVIEW") as failure:
        events.finish(0)
    assert failure.value.status == "blocked"
    assert events.provider_turn_state == "completed"
    assert events.terminal_event == "turn.completed"


@pytest.mark.parametrize(("exit_code", "expected"), [(0, "CODEX_INCOMPLETE_TURN"),
                                                     (3, "CODEX_PROCESS_FAILED")])
def test_unknown_notice_does_not_replace_incomplete_or_natural_process_failure(exit_code, expected):
    events = _ExecEvents(20)
    start(events)
    notice(events, UNKNOWN)
    with pytest.raises(RpcFailure, match=expected):
        events.finish(exit_code)
    assert events.provider_turn_state == "incomplete"
    assert events.notification_policy == "requires_review"


@pytest.mark.parametrize(("scenario", "code", "state", "exit_code"), [
    ("notification_warning", "CODEX_NOTIFICATION_REQUIRES_REVIEW", "completed", 0),
    ("notification_reroute", "CODEX_NOTIFICATION_REQUIRES_REVIEW", "completed", 0),
    ("notification_multiple", "CODEX_NOTIFICATION_REQUIRES_REVIEW", "completed", 0),
    ("notification_failed", "PROVIDER_FAILURE", "failed", 1),
    ("notification_nonzero", "CODEX_PROCESS_FAILED", "completed", 3),
    ("notification_incomplete", "CODEX_INCOMPLETE_TURN", "incomplete", 0),
    ("stream_error_recovered", "CODEX_STREAM_ERROR_REQUIRES_REVIEW", "completed", 0),
])
def test_real_fixture_process_reaches_natural_end_but_quarantine_never_enters_cache(
    tmp_path, scenario, code, state, exit_code
):
    model = runner(tmp_path, scenario)
    model._collect_process_diagnostics = True
    model._collect_notification_diagnostics = True
    result = asyncio.run(model.run(task()))
    assert result.output is None and result.error_code == code and not model._cache
    process = model.process_diagnostics[-1]
    assert process["provider_turn_state"] == state and process["exit_code"] == exit_code
    if state in {"completed", "failed"}:
        assert (tmp_path / "calls.jsonl.finished").read_text() == "natural fixture termination"
    assert process["cleanup"]["status"] == "completed"
    assert "never-log-me" not in json.dumps(
        [asdict(result), model.process_diagnostics], default=str,
    )


def test_allowed_notice_normal_run_works_without_diagnostic_opt_in(tmp_path):
    model = runner(tmp_path, "notification_informational")
    result = asyncio.run(model.run(task()))
    assert result.status == "succeeded" and result.output is not None
    assert model._cache and model.process_diagnostics == []


def test_notification_followed_by_stall_is_bounded_and_locally_interrupted(tmp_path):
    model = runner(tmp_path, "notification_timeout", timeout_seconds=1)
    model._collect_process_diagnostics = True
    result = asyncio.run(model.run(task()))
    assert result.status == "cancelled" and result.error_code == "MODEL_DEADLINE_EXCEEDED"
    assert result.output is None and not model._cache
    process = model.process_diagnostics[-1]
    assert process["provider_turn_state"] == "interrupted"
    assert process["terminal_event"] is None and process["exit_code"] is None
    assert process["notification_policy"] == "requires_review"
    assert process["cleanup"]["status"] == "completed"


@pytest.mark.parametrize(("scenario", "code", "state", "exit_code"), [
    ("notification_informational_failed", "PROVIDER_FAILURE", "failed", 1),
    ("notification_informational_nonzero", "CODEX_PROCESS_FAILED", "completed", 3),
])
def test_benign_notification_never_hides_real_failure(tmp_path, scenario, code, state, exit_code):
    model = runner(tmp_path, scenario)
    model._collect_process_diagnostics = True
    result = asyncio.run(model.run(task()))
    assert result.status == "failed" and result.error_code == code
    assert result.output is None and not model._cache
    process = model.process_diagnostics[-1]
    assert process["notification_policy"] == "informational"
    assert process["provider_turn_state"] == state and process["exit_code"] == exit_code
    assert (tmp_path / "calls.jsonl.finished").exists()


def test_unknown_notice_does_not_delay_immediate_native_tool_rejection(tmp_path):
    model = runner(tmp_path, "notification_tool", timeout_seconds=1)
    model._collect_process_diagnostics = True
    result = asyncio.run(model.run(task()))
    assert result.status == "blocked" and result.error_code == "UNEXPECTED_MODEL_TOOL"
    assert result.output is None and not model._cache
    assert not (tmp_path / "calls.jsonl.finished").exists()
    process = model.process_diagnostics[-1]
    assert process["notification_policy"] == "requires_review"
    assert process["terminal_event"] is None and process["exit_code"] is None
    assert process["cleanup"]["status"] == "completed"
