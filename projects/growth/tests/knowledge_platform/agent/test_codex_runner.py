"""Fixture transport coverage only; not a real login, subscription or Windows E2E."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ahadiff.knowledge_platform.model import (
    CodexModelRunner,
    CodexRunnerConfig,
    DisabledModelRunner,
    ModelTask,
    PiModelRunner,
    SubscriptionRoute,
    build_runner_from_config,
)

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def task(**changes):
    return replace(
        ModelTask(
            task_id="codex-task",
            purpose="preview",
            input_refs=("source-1",),
            input_classification="public",
            allowed_provider_ids=("openai",),
            schema_name="preview",
            output_schema=SCHEMA,
            payload={"text": "untrusted text"},
            dedupe_key="codex-1",
        ),
        **changes,
    )


def runner(tmp_path, scenario="success", **changes):
    config = CodexRunnerConfig(
        enabled=True,
        reuse_existing_login=True,
        routes=(
            SubscriptionRoute(
                provider_id="openai", model_id="fixture-model", account_qualified=True
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
    return CodexModelRunner(replace(config, **changes))


def test_codex_is_opt_in_and_pi_stays_default():
    assert isinstance(build_runner_from_config(None), DisabledModelRunner)
    assert isinstance(build_runner_from_config({"enabled": True}), PiModelRunner)
    assert isinstance(
        build_runner_from_config({"enabled": True, "adapter": "codex_cli"}), CodexModelRunner
    )
    with pytest.raises(ValueError):
        build_runner_from_config({"enabled": True, "adapter": "guess"})
    for key in ("api_key", "provider_env_names", "agent_dir", "development_transport"):
        with pytest.raises(ValueError):
            build_runner_from_config({"enabled": True, "adapter": "codex_cli", key: "forbidden"})


def test_success_is_fixture_with_requested_not_claimed_actual_model(tmp_path):
    result = asyncio.run(runner(tmp_path).run(task()))
    assert result.status == "succeeded" and result.fixture
    assert result.output == {"text": "有效结果\u2028仍为同一JSONL"}
    assert result.model_id is None and result.requested_model_id == "fixture-model"
    assert (
        result.provider_id == "openai" and result.runtime_version == "codex-cli 0.163.0-alpha.10.2"
    )
    assert result.result_hash and result.input_fingerprint
    assert result.usage == {"input": 50, "cacheRead": 10, "output": 15}
    records = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    args = records[-1]["args"]
    for flag in ("--json", "--ephemeral", "--ignore-user-config", "--output-schema"):
        assert flag in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--model") + 1] == "fixture-model"
    assert 'forced_login_method="chatgpt"' in args and 'model_provider="openai"' in args
    assert "shell_tool" in args and "unified_exec" in args and "apps" in args and "plugins" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args


@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [
        ("wrong_version", "blocked", "CODEX_VERSION_MISMATCH"),
        ("api_key", "blocked", "METERED_AUTH_FORBIDDEN"),
        ("auth_missing", "waiting_auth", "SUBSCRIPTION_AUTH"),
        ("auth_error", "waiting_auth", "SUBSCRIPTION_AUTH"),
        ("quota", "waiting_quota", "SUBSCRIPTION_QUOTA"),
        ("invalid_inner", "invalid_output", "OUTPUT_SCHEMA_INVALID"),
        ("invalid_envelope", "invalid_output", "OUTPUT_SCHEMA_INVALID"),
        ("schema_wrong", "invalid_output", "OUTPUT_SCHEMA_INVALID"),
        ("secret", "invalid_output", "OUTPUT_SCHEMA_INVALID"),
        ("early_exit", "invalid_output", "CODEX_INCOMPLETE_TURN"),
        ("nonzero_exit", "failed", "CODEX_PROCESS_FAILED"),
        ("bad_jsonl", "failed", "CODEX_INVALID_JSONL"),
        ("too_many_tokens", "invalid_output", "MODEL_OUTPUT_TOKEN_LIMIT"),
        ("duplicate_message", "invalid_output", "CODEX_AMBIGUOUS_OUTPUT"),
        ("after_completion", "failed", "CODEX_EVENT_AFTER_COMPLETION"),
        ("stdout_flood", "blocked", "CODEX_OUTPUT_TOO_LARGE"),
        ("stderr_flood", "blocked", "CODEX_OUTPUT_TOO_LARGE"),
    ],
)
def test_failures_are_not_success_or_sensitive_audit(tmp_path, scenario, status, code):
    result = asyncio.run(runner(tmp_path, scenario).run(task()))
    assert result.status == status and result.error_code == code
    assert result.output is None and result.fixture and result.model_id is None
    assert "never-log-me" not in json.dumps(asdict(result), default=str)


@pytest.mark.parametrize(
    "kind",
    [
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
        "todo_list",
        "future_unknown_tool",
    ],
)
def test_all_tool_events_fail_closed(tmp_path, kind):
    result = asyncio.run(runner(tmp_path, "tool_" + kind).run(task()))
    assert result.status == "blocked" and result.error_code == "UNEXPECTED_MODEL_TOOL"


@pytest.mark.parametrize(
    "change",
    [
        {"input_classification": "secret"},
        {"input_classification": "employer"},
        {"allowed_provider_ids": ()},
        {"payload": {"api_key": "fixture_secret_value"}},
        {"output_schema": {"type": "object", "$ref": "https://invalid.example/schema"}},
        {"payload": {"text": "x" * 600000}},
        {"deadline": datetime.now(UTC) - timedelta(days=1)},
    ],
)
def test_policy_stops_before_process(tmp_path, change):
    result = asyncio.run(runner(tmp_path).run(task(**change)))
    assert result.status in {"blocked", "cancelled"}
    assert not (tmp_path / "calls.jsonl").exists()


def test_auth_opt_in_stops_before_process(tmp_path):
    assert asyncio.run(runner(tmp_path, reuse_existing_login=False).run(task())).status == "blocked"
    assert not (tmp_path / "calls.jsonl").exists()


def test_environment_no_api_fallback_or_auth_file_read(tmp_path, monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_BASE_URL",
        "HTTPS_PROXY",
        "NODE_OPTIONS",
    ):
        monkeypatch.setenv(key, "must-not-be-inherited")
    existing = tmp_path / "unread-auth-home"
    # There is intentionally no auth directory/file. Only the fixture CLI handles it.
    result = asyncio.run(runner(tmp_path, codex_home=existing).run(task()))
    assert result.status == "succeeded" and not existing.exists()
    record = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert record["codex_home"] == str(existing)
    assert not set(record["env_keys"]) & {
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENAI_BASE_URL",
        "HTTPS_PROXY",
        "NODE_OPTIONS",
    }


def test_quota_serialization_and_no_retry(tmp_path):
    model = runner(tmp_path, "quota")

    async def twice():
        return await asyncio.gather(
            model.run(task()), model.run(task(task_id="two", dedupe_key="two"))
        )

    results = asyncio.run(twice())
    assert all(r.status == "waiting_quota" and r.retry_after for r in results)
    calls = (tmp_path / "calls.jsonl").read_text()
    assert calls.count('"exec"') == 1


def test_idempotency(tmp_path):
    model = runner(tmp_path)

    async def twice():
        return (
            await model.run(task()),
            await model.run(task(task_id="two")),
            await model.run(task(payload={"text": "different"})),
        )

    first, second, conflict = asyncio.run(twice())
    assert first.result_hash == second.result_hash and second.task_id == "two"
    assert conflict.error_code == "MODEL_IDEMPOTENCY_CONFLICT"
    assert (tmp_path / "calls.jsonl").read_text().count('"exec"') == 1


def test_timeout_kills_process(tmp_path):
    result = asyncio.run(runner(tmp_path, "timeout", timeout_seconds=1).run(task()))
    assert result.error_code == "MODEL_DEADLINE_EXCEEDED"
    if os.name == "posix":
        pid = int((tmp_path / "calls.jsonl.pid").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_model_self_report_is_not_audit_metadata(tmp_path):
    result = asyncio.run(runner(tmp_path, "claimed_model").run(task()))
    assert result.status == "succeeded" and result.model_id is None
    assert result.requested_model_id == "fixture-model"


def test_no_provider_or_model_fallback():
    with pytest.raises(ValueError):
        CodexRunnerConfig(routes=(SubscriptionRoute(provider_id="zai", model_id="model"),))
    with pytest.raises(ValueError):
        CodexRunnerConfig(routes=(SubscriptionRoute(provider_id="openai", model_id="a"),) * 2)
    with pytest.raises(ValueError):
        CodexRunnerConfig(expected_version="unknown")


def test_workflow_audit_preserves_requested_model_and_unknown_actual(tmp_path):
    from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings

    class RecordingDomain:
        def __init__(self):
            self.patches = []

        def update_task(self, task_id, patch, **kwargs):
            self.patches.append(patch)

    domain = RecordingDomain()
    workflow = KnowledgeWorkflow(domain, None, WorkflowSettings(owner_id="fixture-owner"))
    result = asyncio.run(runner(tmp_path, "quota").run(task()))
    metadata = {**asdict(result), "debug": "private diagnostic", "output": {"private": True}}
    settled = workflow._settle_model(
        {"task_id": "fixture-task", "lease_token": "fixture-lease"},
        {"status": "waiting_quota", "model": metadata, "model_attempts": [metadata]},
        "generate",
    )
    assert settled
    audit = domain.patches[0]["result"]
    for saved in [audit["model"], *audit["model_attempts"]]:
        assert saved["model_id"] is None and saved["requested_model_id"] == "fixture-model"
        assert "debug" not in saved and "output" not in saved
        assert saved["failure"]["summary"] == "subscription_quota_exhausted"


def test_nested_secret_text_stops_before_process(tmp_path):
    result = asyncio.run(
        runner(tmp_path).run(task(payload={"text": '"password": "fixture_secret"'}))
    )
    assert result.error_code == "SECRET_INPUT_FORBIDDEN"
    assert not (tmp_path / "calls.jsonl").exists()


def test_caller_cancellation_closes_child(tmp_path):
    async def cancel():
        operation = asyncio.create_task(runner(tmp_path, "timeout").run(task()))
        for _ in range(100):
            if (tmp_path / "calls.jsonl.pid").exists():
                break
            await asyncio.sleep(0.02)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation

    asyncio.run(cancel())
    if os.name == "posix":
        pid = int((tmp_path / "calls.jsonl.pid").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_deep_output_and_missing_local_ref_are_normalized(tmp_path):
    result = asyncio.run(runner(tmp_path, "deep_output").run(task()))
    assert result.status == "invalid_output" and result.error_code == "OUTPUT_SCHEMA_INVALID"
    result = asyncio.run(
        runner(tmp_path).run(task(output_schema={"type": "object", "$ref": "#/$defs/missing"}))
    )
    assert result.status == "invalid_output" and result.error_code == "OUTPUT_SCHEMA_INVALID"


def test_iterative_secret_scan_accepts_deep_bounded_data():
    from ahadiff.knowledge_platform.model.validation import contains_obvious_secret

    value = "ordinary text"
    for _ in range(600):
        value = [value]
    assert not contains_obvious_secret(value)


def test_secret_in_object_key_stops_before_process(tmp_path):
    result = asyncio.run(runner(tmp_path).run(task(payload={"ghp_" + "A" * 20: {}})))
    assert result.error_code == "SECRET_INPUT_FORBIDDEN"
    assert not (tmp_path / "calls.jsonl").exists()


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX process-group regression; Windows job requires host test"
)
def test_exited_cli_does_not_leak_descendant(tmp_path):
    result = asyncio.run(runner(tmp_path, "orphan_child", timeout_seconds=1).run(task()))
    assert result.status == "cancelled"
    pid = int((tmp_path / "calls.jsonl.child-pid").read_text())
    status = Path(f"/proc/{pid}/stat")
    # A killed orphan may await init's reap, but it must not be running.
    if status.exists():
        assert status.read_text().split()[2] == "Z"


@pytest.mark.parametrize(
    ("scenario", "kind", "summary", "event_type"),
    [
        ("transport", "transport", "transport_connection_failed", "turn.failed"),
        ("schema", "provider", "structured_output_schema_rejected", "turn.failed"),
        ("model", "provider", "requested_model_unavailable", "turn.failed"),
        ("sandbox", "host", "sandbox_initialization_failed", "turn.failed"),
        ("configuration", "host", "unsupported_cli_configuration", "turn.failed"),
        ("unknown", "provider", "provider_reported_failure", "turn.failed"),
        ("error", "transport", "transport_connection_failed", "error"),
    ],
)
def test_provider_failure_has_controlled_diagnostic_not_raw_message(
    tmp_path, scenario, kind, summary, event_type
):
    from ahadiff.knowledge_platform.generation.service import result_metadata

    result = asyncio.run(runner(tmp_path, "provider_failure_" + scenario).run(task()))
    assert result.status == "failed" and result.error_code == "PROVIDER_FAILURE"
    assert result.failure is not None
    assert result.failure.stage == "execution"
    assert result.failure.kind == kind and result.failure.summary == summary
    assert result.failure.event_type == event_type
    # A live process is killed when the failure arrives. Do not fabricate a
    # natural exit code from that cleanup termination.
    assert result.failure.exit_code in {None, 1}
    audit = result_metadata(result)
    assert audit["failure"] == asdict(result.failure)
    encoded = json.dumps(audit, default=str)
    assert "never-log-me" not in encoded and "untrusted text" not in encoded
    assert "password=" not in encoded and "token=" not in encoded


@pytest.mark.parametrize(
    "scenario",
    ["provider_failure_item", "notification_warning", "notification_reroute"],
)
def test_nonfatal_cli_notifications_still_fail_closed_without_mislabeling_provider_failure(
    tmp_path, scenario
):
    from ahadiff.knowledge_platform.generation.service import result_metadata

    result = asyncio.run(runner(tmp_path, scenario).run(task()))
    assert result.status == "blocked"
    assert result.error_code == "CODEX_NOTIFICATION_REQUIRES_REVIEW"
    assert result.failure is not None and result.failure.kind == "policy"
    assert result.failure.summary == "cli_notification_requires_review"
    assert result.failure.event_type == "item.error" and result.output is None
    encoded = json.dumps(result_metadata(result), default=str)
    assert "never-log-me" not in encoded and "fallback-model" not in encoded


@pytest.mark.parametrize(
    ("scenario", "stage", "exit_code"),
    [
        ("wrong_version", "version", 0),
        ("auth_missing", "authentication", 1),
        ("nonzero_exit", "execution", 3),
    ],
)
def test_observed_exit_code_is_kept_without_raw_output(tmp_path, scenario, stage, exit_code):
    result = asyncio.run(runner(tmp_path, scenario).run(task()))
    assert result.failure is not None
    assert result.failure.stage == stage and result.failure.exit_code == exit_code


def test_diagnostic_serialization_rejects_untrusted_fields_and_values():
    from ahadiff.knowledge_platform.model.diagnostics import ModelFailure, sanitized_failure

    valid = {
        "stage": "execution", "kind": "provider", "summary": "provider_reported_failure",
        "event_type": "turn.failed", "exit_code": None,
    }
    assert sanitized_failure({**valid, "message": "never-log-me", "prompt": "private"}) == valid
    assert sanitized_failure(ModelFailure(**valid)) == valid
    for field, value in (
        ("summary", "private upstream text"),
        ("stage", "private/path"),
        ("kind", []),
        ("event_type", {"secret": True}),
        ("exit_code", True),
        ("exit_code", 2**32),
    ):
        assert sanitized_failure({**valid, field: value}) is None
    assert sanitized_failure(None) is None


def test_real_task_ledger_retains_only_allowlisted_failure_diagnostic(tmp_path):
    from ahadiff.knowledge_platform.auth import Actor
    from ahadiff.knowledge_platform.domain import DomainService
    from ahadiff.knowledge_platform.generation.service import result_metadata
    from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings

    domain = DomainService(tmp_path / "platform.db")
    owner = Actor("fixture-owner", "human", "user", frozenset({"*"}))
    workflow = KnowledgeWorkflow(domain, None, WorkflowSettings(owner_id=owner.owner_id))
    try:
        queued = domain.create_task(
            "knowledge.generate", {}, actor=owner, idempotency_key="fixture-failure-task"
        )
        claimed = domain.claim_task("fixture-worker", actor=workflow.worker)
        assert claimed is not None
        result = asyncio.run(runner(tmp_path, "provider_failure_transport").run(task()))
        metadata = result_metadata(result)
        metadata["failure"]["message"] = "never-log-me"
        assert workflow._settle_model(
            claimed, {"status": result.status, "model": metadata}, "generate"
        )
        saved = domain.read_task(queued["task_id"], actor=owner)
        assert saved["state"] == "failed"
        assert saved["result"]["model"]["failure"]["summary"] == "transport_connection_failed"
        assert "message" not in saved["result"]["model"]["failure"]
        assert "never-log-me" not in json.dumps(saved)
        assert domain.list_entries(actor=owner) == []
    finally:
        domain.close()
