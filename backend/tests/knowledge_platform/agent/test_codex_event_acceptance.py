"""Official nonfatal protocol semantics; every process/model input is synthetic."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, replace

import pytest
from test_codex_runner import runner, task

from ahadiff.knowledge_platform.generation.service import result_metadata
from ahadiff.knowledge_platform.model._codex_notice_templates import EXACT_NOTIFICATION_TEMPLATES
from ahadiff.knowledge_platform.model.codex_runner import _SANDBOX_DISABLED_NOTICE, _ExecEvents
from ahadiff.knowledge_platform.model.diagnostics import sanitized_warning_codes
from ahadiff.knowledge_platform.model.pi_runner import RpcFailure

NOTICES = {code: text for text, code in EXACT_NOTIFICATION_TEMPLATES.items()}
UNKNOWN = "runtime notice with synthetic-private-sentinel"
STREAM_LOSS = "in-process app-server event stream lagged; dropped 1 events"
NONFATAL = [
    UNKNOWN, *EXACT_NOTIFICATION_TEMPLATES,
    "model rerouted: approved -> another-model (service routing)",
    "Codex is ignoring 1 unrecognized configuration setting.",
    "`--dangerously-bypass-hook-trust` is enabled. Enabled hooks may run without review "
    "for this invocation.",
    "Credential masking is inactive because the network proxy is disabled. Commands will "
    "receive unmasked credentials. Enable `features.network_proxy` or configure managed "
    "network requirements to use credential masking.",
    "prefix " + _SANDBOX_DISABLED_NOTICE, _SANDBOX_DISABLED_NOTICE + "\n",
    "prefix " + STREAM_LOSS, STREAM_LOSS + "\n",
]


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
@pytest.mark.parametrize("text", NONFATAL)
def test_nonfatal_notices_do_not_require_a_template_or_raw_text_disclosure(details, text):
    events = _ExecEvents(20, notification_details=details)
    notice(events, text)  # Startup warnings may precede thread.started.
    start(events)
    complete(events)
    events.finish(0)
    assert events.completed and events.provider_turn_state == "completed"
    assert events.notification_policy == "nonfatal"
    assert (events.notification is not None) == details
    assert UNKNOWN not in json.dumps(events.notification)


@pytest.mark.parametrize("version", [None, "0.163.0-alpha.10.2", "0.163.0-alpha.11", "0.163.0-alpha.11.2"])
def test_protocol_semantics_do_not_depend_on_a_two_template_version_allowlist(version):
    # Actual runner version verification is separately mandatory before exec.
    events = _ExecEvents(20, cli_version=version)
    start(events)
    notice(events, UNKNOWN)
    complete(events)
    events.finish(0)
    assert events.notification_policy == "nonfatal"


@pytest.mark.parametrize("violation", [_SANDBOX_DISABLED_NOTICE, STREAM_LOSS])
@pytest.mark.parametrize("first", [False, True])
def test_confirmed_boundary_violation_dominates_other_notices_in_either_order(violation, first):
    events = _ExecEvents(20)
    start(events)
    for value in ([violation, UNKNOWN] if first else [UNKNOWN, violation]):
        notice(events, value)
    complete(events)
    assert events.completed and events.notification_policy == "boundary_violation"
    with pytest.raises(RpcFailure, match="CODEX_RUNTIME_BOUNDARY_VIOLATION"):
        events.finish(0)


@pytest.mark.parametrize("kind", ["item.completed", "error"])
def test_notification_count_has_independent_bound(kind):
    events = _ExecEvents(20)
    start(events)
    for _ in range(16):
        if kind == "error":
            emit(events, kind, message=UNKNOWN)
        else:
            notice(events, UNKNOWN)
    with pytest.raises(RpcFailure, match="CODEX_NOTIFICATION_LIMIT"):
        if kind == "error":
            emit(events, kind, message=UNKNOWN)
        else:
            notice(events, UNKNOWN)


@pytest.mark.parametrize("terminal", ["turn.completed", "turn.failed"])
@pytest.mark.parametrize("extra", ["turn.completed", "turn.failed", "turn.started", "notice"])
def test_any_event_after_either_terminal_is_rejected(terminal, extra):
    events = _ExecEvents(20)
    start(events)
    emit(events, terminal, usage={"output_tokens": 1}, error={"message": "synthetic failure"})
    with pytest.raises(RpcFailure, match="CODEX_EVENT_AFTER_COMPLETION"):
        if extra == "notice":
            notice(events, UNKNOWN)
        else:
            emit(events, extra, usage={"output_tokens": 1}, error={"message": "failure"})


@pytest.mark.parametrize("kind", ["turn.started", "turn.completed", "turn.failed"])
def test_turn_sequence_cannot_be_fabricated_without_thread(kind):
    with pytest.raises(RpcFailure, match="CODEX_UNEXPECTED_EVENT"):
        emit(_ExecEvents(20), kind, usage={"output_tokens": 1}, error={"message": "failure"})


def test_recovered_top_level_error_completed_and_exit_zero_is_not_auth_failure():
    events = _ExecEvents(20)
    start(events)
    emit(events, "error", message="401 synthetic transient error")
    notice(events, UNKNOWN)
    complete(events)
    events.finish(0)
    assert events.stream_error is not None
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
    assert events.notification_policy == "nonfatal"


@pytest.mark.parametrize(("scenario", "code", "state", "exit_code"), [
    ("notification_boundary", "CODEX_RUNTIME_BOUNDARY_VIOLATION", "completed", 0),
    ("notification_stream_loss", "CODEX_RUNTIME_BOUNDARY_VIOLATION", "completed", 0),
    ("notification_failed", "PROVIDER_FAILURE", "failed", 1),
    ("notification_nonzero", "CODEX_PROCESS_FAILED", "completed", 3),
    ("notification_incomplete", "CODEX_INCOMPLETE_TURN", "incomplete", 0),
    ("notification_schema_wrong", "OUTPUT_SCHEMA_INVALID", "completed", 0),
    ("notification_secret", "OUTPUT_SCHEMA_INVALID", "completed", 0),
])
def test_real_fixture_process_reaches_natural_end_but_invalid_results_never_enter_cache(
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


@pytest.mark.parametrize("scenario", ["notification_warning", "notification_reroute",
                                      "notification_multiple", "notification_informational"])
@pytest.mark.parametrize("purpose", ["preview", "generate", "review", "answer"])
def test_ordinary_tasks_share_success_with_warning_without_diagnostic_opt_in(
    tmp_path, scenario, purpose
):
    model = runner(tmp_path, scenario)
    request = task(purpose=purpose)
    result = asyncio.run(model.run(request))
    assert result.status == "succeeded" and result.output is not None
    assert result.model_id is None and result.warning_codes == ("codex_nonfatal_notification",)
    assert model._cache and model.process_diagnostics == []
    calls_before = (tmp_path / "calls.jsonl").read_bytes()
    replay = asyncio.run(model.run(request))
    assert replay == result and (tmp_path / "calls.jsonl").read_bytes() == calls_before
    assert "never-log-me" not in json.dumps(result_metadata(result))


def test_notification_followed_by_stall_is_bounded_and_locally_interrupted(tmp_path):
    model = runner(tmp_path, "notification_timeout", timeout_seconds=5 if os.name == "nt" else 1)
    model._collect_process_diagnostics = True
    result = asyncio.run(model.run(task()))
    assert result.status == "cancelled" and result.error_code == "MODEL_DEADLINE_EXCEEDED"
    assert result.output is None and not model._cache
    process = model.process_diagnostics[-1]
    assert process["provider_turn_state"] == "interrupted"
    assert process["terminal_event"] is None and process["exit_code"] is None
    assert process["notification_policy"] == "nonfatal"
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
    assert process["notification_policy"] == "nonfatal"
    assert process["provider_turn_state"] == state and process["exit_code"] == exit_code
    assert (tmp_path / "calls.jsonl.finished").exists()


def test_unknown_notice_does_not_delay_immediate_native_tool_rejection(tmp_path):
    model = runner(tmp_path, "notification_tool", timeout_seconds=5 if os.name == "nt" else 1)
    model._collect_process_diagnostics = True
    result = asyncio.run(model.run(task()))
    assert result.status == "blocked" and result.error_code == "UNEXPECTED_MODEL_TOOL"
    assert result.output is None and not model._cache
    assert not (tmp_path / "calls.jsonl.finished").exists()
    process = model.process_diagnostics[-1]
    assert process["notification_policy"] == "nonfatal"
    assert process["terminal_event"] is None and process["exit_code"] is None
    assert process["cleanup"]["status"] == "completed"


@pytest.mark.parametrize("value", [None, "synthetic-secret", {}, ["synthetic-secret"],
                                  ["codex_nonfatal_notification", "synthetic-secret"],
                                  ["codex_nonfatal_notification"] * 17])
def test_warning_metadata_is_fixed_vocabulary_and_bounded(tmp_path, value):
    result = asyncio.run(runner(tmp_path).run(task()))
    audit = result_metadata(replace(result, warning_codes=value))
    assert audit["warning_codes"] == sanitized_warning_codes(value)
    assert "synthetic-secret" not in json.dumps(audit)


@pytest.mark.parametrize("purpose", ["preview", "answer"])
def test_recovered_error_has_safe_warning_without_auth_or_quota_side_effect(tmp_path, purpose):
    model = runner(tmp_path, "stream_error_recovered")
    result = asyncio.run(model.run(task(purpose=purpose)))
    assert result.status == "succeeded" and result.output is not None
    assert result.failure is None and result.error_code is None and result.retry_after is None
    assert result.warning_codes == ("codex_recovered_stream_error",)
    assert model._quota_until is None and model._cache
    assert "never-log-me" not in json.dumps(result_metadata(result))


@pytest.mark.parametrize(("scenario", "code"), [
    ("stream_error_failed", "PROVIDER_FAILURE"),
    ("stream_error_tool", "UNEXPECTED_MODEL_TOOL"),
    ("stream_error_schema_wrong", "OUTPUT_SCHEMA_INVALID"),
    ("stream_error_secret", "OUTPUT_SCHEMA_INVALID"),
    ("stream_error_incomplete", "CODEX_INCOMPLETE_TURN"),
    ("stream_error_nonzero", "SUBSCRIPTION_AUTH"),
    ("stream_error_extra_terminal", "CODEX_EVENT_AFTER_COMPLETION"),
])
def test_intermediate_error_does_not_relax_any_final_acceptance_gate(tmp_path, scenario, code):
    model = runner(tmp_path, scenario, timeout_seconds=5 if os.name == "nt" else 1)
    result = asyncio.run(model.run(task()))
    assert result.status != "succeeded" and result.error_code == code
    assert result.output is None and not model._cache
    assert "never-log-me" not in json.dumps(result_metadata(result), default=str)


@pytest.mark.parametrize("violation", [_SANDBOX_DISABLED_NOTICE, STREAM_LOSS])
def test_recoverable_error_cannot_override_confirmed_runtime_boundary(violation):
    events = _ExecEvents(20)
    start(events)
    emit(events, "error", message="intermediate synthetic error")
    notice(events, violation)
    complete(events)
    with pytest.raises(RpcFailure, match="CODEX_RUNTIME_BOUNDARY_VIOLATION"):
        events.finish(0)


def test_safe_warning_metadata_survives_json_roundtrip_and_canonical_seal(tmp_path):
    from ahadiff.knowledge_platform.model.contracts import canonical_hash

    result = asyncio.run(runner(tmp_path, "notification_warning").run(task()))
    result = replace(result, warning_codes=("codex_nonfatal_notification",
                                           "codex_recovered_stream_error"))
    metadata = result_metadata(result)
    restored = json.loads(json.dumps(metadata))
    assert restored["warning_codes"] == [
        "codex_nonfatal_notification", "codex_recovered_stream_error",
    ]
    assert canonical_hash(restored) == canonical_hash(metadata)


@pytest.mark.parametrize(("kind", "fields"), [
    ("error", {}), ("error", {"message": None}), ("error", {"message": 3}),
    ("error", {"error": {"message": "wrong nested shape"}}),
    ("turn.failed", {}), ("turn.failed", {"error": None}),
    ("turn.failed", {"error": "not an object"}),
    ("turn.failed", {"error": {}}), ("turn.failed", {"error": {"message": 3}}),
])
def test_recovery_never_accepts_malformed_error_event(kind, fields):
    events = _ExecEvents(20)
    start(events)
    with pytest.raises(RpcFailure, match="CODEX_INVALID_EVENT"):
        emit(events, kind, **fields)
    assert not events.completed
