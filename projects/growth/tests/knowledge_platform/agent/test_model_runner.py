"""Unit/protocol fixture tests, deliberately not real subscription E2E evidence."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ahadiff.knowledge_platform.model import (
    DisabledModelRunner,
    FixtureModelRunner,
    ModelTask,
    PiModelRunner,
    PiRunnerConfig,
    SubscriptionRoute,
    build_runner_from_config,
)
from ahadiff.knowledge_platform.model.validation import (
    OUTPUT_ERRORS,
    parse_output,
    sanitized_usage,
    validate_schema,
)

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


def task(**changes) -> ModelTask:
    value = ModelTask(
        task_id="task-1",
        purpose="preview",
        input_refs=("source-1",),
        input_classification="public",
        allowed_provider_ids=("openai",),
        schema_name="preview",
        output_schema=SCHEMA,
        payload={"text": "untrusted article"},
        dedupe_key="key-1",
    )
    return replace(value, **changes)


def runner(tmp_path: Path, scenario: str = "success", **changes) -> PiModelRunner:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(exist_ok=True)
    guard = tmp_path / "guard.ts"
    guard.write_text(
        "// test-only placeholder; the fake RPC peer never loads this", encoding="utf-8"
    )
    config = PiRunnerConfig(
        enabled=True,
        agent_dir=agent_dir,
        guard_extension=guard,
        executable=(
            sys.executable,
            str(Path(__file__).with_name("fake_pi_rpc.py")),
            scenario,
            str(tmp_path / "calls.jsonl"),
        ),
        routes=(
            SubscriptionRoute(
                provider_id="openai", model_id="fixture-model", account_qualified=True
            ),
        ),
        development_transport=True,
    )
    return PiModelRunner(replace(config, **changes))


def test_default_disabled_has_no_process() -> None:
    assert asyncio.run(DisabledModelRunner().run(task())).status == "blocked"
    assert isinstance(build_runner_from_config(None), DisabledModelRunner)
    assert asyncio.run(PiModelRunner(PiRunnerConfig()).run(task())).status == "blocked"


def test_fixture_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError):
        FixtureModelRunner({"preview": {"text": "hello"}})
    result = asyncio.run(
        FixtureModelRunner({"preview": {"text": "hello"}}, development=True).run(task())
    )
    assert result.fixture and result.status == "succeeded"


@pytest.mark.parametrize("provider", ["openai-codex", "anthropic", "openrouter", "deepseek"])
def test_disallow_legacy_and_metered_provider(provider: str) -> None:
    with pytest.raises(ValueError):
        SubscriptionRoute(provider_id=provider, model_id="model", account_qualified=True)


@pytest.mark.parametrize(
    "raw",
    [
        '{"text":"a","text":"b"}',
        '```json\n{"text":"a"}\n```',
        '{"text":NaN}',
        '{"text":"ok","approved":true}',
        "[]",
    ],
)
def test_strict_output(raw: str) -> None:
    with pytest.raises(OUTPUT_ERRORS):
        parse_output(raw, SCHEMA)


def test_schema_cannot_resolve_remote_url() -> None:
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "$ref": "https://untrusted.invalid/schema.json"})


def test_rpc_success_is_explicit_fixture(tmp_path: Path) -> None:
    result = asyncio.run(runner(tmp_path).run(task()))
    assert result.status == "succeeded" and result.fixture
    assert result.output == {"text": "有效结果\u2028仍为同一JSONL"}
    assert result.provider_id == "openai" and result.model_id == "fixture-model"
    assert result.result_hash and result.input_fingerprint
    calls = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert '"--no-tools"' in calls and '"--no-mcp"' in calls and '"--no-session"' in calls
    assert '"--no-refresh"' in calls and '"--credentials"' not in calls
    assert '"--model", "fixture-model"' in calls


@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [
        ("auth_missing", "waiting_auth", "SUBSCRIPTION_AUTH"),
        ("api_key", "blocked", "METERED_AUTH_FORBIDDEN"),
        ("wrong_version", "blocked", "PI_VERSION_MISMATCH"),
        ("wrong_model", "blocked", "PI_MODEL_SELECTION_MISMATCH"),
        ("quota", "waiting_quota", "SUBSCRIPTION_QUOTA"),
        ("auth_error", "waiting_auth", "SUBSCRIPTION_AUTH"),
        ("end_is_not_settled", "waiting_quota", "SUBSCRIPTION_QUOTA"),
        ("handled", "blocked", "RPC_PROMPT_NOT_STARTED"),
        ("tool", "blocked", "UNEXPECTED_MODEL_TOOL"),
        ("early_exit", "failed", "RPC_UNEXPECTED_EXIT"),
        ("aborted", "cancelled", "MODEL_ABORTED"),
        ("invalid", "invalid_output", "OUTPUT_SCHEMA_INVALID"),
    ],
)
def test_rpc_failures_are_not_success(
    tmp_path: Path, scenario: str, status: str, code: str
) -> None:
    result = asyncio.run(runner(tmp_path, scenario).run(task()))
    assert result.status == status and result.error_code == code and result.output is None


def test_bounded_json_repair(tmp_path: Path) -> None:
    result = asyncio.run(runner(tmp_path, "repair").run(task()))
    assert result.status == "succeeded"
    assert result.usage["input"] == 24 and result.usage["output"] == 10
    calls = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert calls.count('"type": "prompt"') == 2


def test_usage_audit_cannot_retain_arbitrary_provider_text() -> None:
    assert sanitized_usage(
        {
            "input": 12,
            "output": "private text",
            "debug": "private text",
            "cacheRead": float("nan"),
            "cacheWrite": -1,
            "reasoning": True,
            "cost": {"total": 0, "debug": "private text"},
        }
    ) == {"input": 12, "cost": {"total": 0}}


def test_no_provider_fallback_and_quota_cooldown(tmp_path: Path) -> None:
    model = runner(
        tmp_path,
        "quota",
        routes=(
            SubscriptionRoute(
                provider_id="openai", model_id="fixture-model", account_qualified=True
            ),
            SubscriptionRoute(provider_id="zai", model_id="fixture-model", account_qualified=True),
        ),
    )

    async def run_twice():
        first = await model.run(task(allowed_provider_ids=("openai", "zai")))
        second = await model.run(task(task_id="task-2", dedupe_key="key-2"))
        return first, second

    first, second = asyncio.run(run_twice())
    assert first.status == second.status == "waiting_quota"
    assert first.retry_after and first.retry_after > datetime.now(UTC)
    calls = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert calls.count('"type": "prompt"') == 1 and '"zai"' not in calls


def test_queued_requests_recheck_quota_after_lock(tmp_path: Path) -> None:
    model = runner(tmp_path, "quota")

    async def concurrent_requests():
        return await asyncio.gather(
            model.run(task()), model.run(task(task_id="task-2", dedupe_key="key-2"))
        )

    results = asyncio.run(concurrent_requests())
    assert all(result.status == "waiting_quota" and result.fixture for result in results)
    calls = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert calls.count('"type": "prompt"') == 1


def test_paid_endpoint_is_blocked(tmp_path: Path) -> None:
    model = runner(
        tmp_path,
        "paid_endpoint",
        routes=(
            SubscriptionRoute(provider_id="zai", model_id="fixture-model", account_qualified=True),
        ),
    )
    result = asyncio.run(model.run(task(allowed_provider_ids=("zai",))))
    assert result.error_code == "METERED_ENDPOINT_FORBIDDEN"
    assert '"type": "prompt"' not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "changes",
    [
        {"input_classification": "secret"},
        {"input_classification": "employer"},
        {"allowed_provider_ids": ()},
        {"payload": {"api_key": "fixture_sensitive_value"}},
    ],
)
def test_policy_stops_before_spawn(tmp_path: Path, changes: dict) -> None:
    result = asyncio.run(runner(tmp_path).run(task(**changes)))
    assert result.status == "blocked"
    assert not (tmp_path / "calls.jsonl").exists()


def test_china_requires_qualification(tmp_path: Path) -> None:
    model = runner(
        tmp_path,
        routes=(
            SubscriptionRoute(
                provider_id="zai-coding-cn", model_id="fixture-model", account_qualified=True
            ),
        ),
    )
    assert asyncio.run(model.run(task(allowed_provider_ids=("zai-coding-cn",)))).status == "blocked"


def test_idempotent_result_and_collision(tmp_path: Path) -> None:
    model = runner(tmp_path)

    async def execute():
        first = await model.run(task())
        second = await model.run(task(task_id="task-2"))
        conflict = await model.run(task(payload={"text": "different"}))
        return first, second, conflict

    first, second, conflict = asyncio.run(execute())
    assert first.result_hash == second.result_hash and second.task_id == "task-2"
    assert conflict.error_code == "MODEL_IDEMPOTENCY_CONFLICT"
    assert (tmp_path / "calls.jsonl").read_text(encoding="utf-8").count('"type": "prompt"') == 1


def test_timeout_closes_child(tmp_path: Path) -> None:
    result = asyncio.run(runner(tmp_path, "timeout", timeout_seconds=0.8).run(task()))
    assert result.status == "cancelled" and result.error_code == "MODEL_DEADLINE_EXCEEDED"


def test_expired_task_never_starts(tmp_path: Path) -> None:
    value = task(deadline=datetime.now(UTC) - timedelta(seconds=1))
    assert asyncio.run(runner(tmp_path).run(value)).status == "cancelled"
    assert not (tmp_path / "calls.jsonl").exists()


def test_production_config_rejects_credential_value() -> None:
    with pytest.raises(ValueError):
        build_runner_from_config({"enabled": True, "api_key": "never-a-real-key"})
    with pytest.raises(ValueError):
        build_runner_from_config({"enabled": True, "provider_env_names": ["OPENAI_API_KEY"]})


def test_custom_model_overrides_never_called(tmp_path: Path) -> None:
    model = runner(tmp_path)
    (tmp_path / "agent" / "models.json").write_text(json.dumps({"providers": {}}), encoding="utf-8")
    assert asyncio.run(model.run(task())).error_code == "CUSTOM_MODEL_OVERRIDES_FORBIDDEN"
