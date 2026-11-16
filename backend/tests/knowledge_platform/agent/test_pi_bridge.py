"""Synthetic pipe/provider contracts. No actual CLI, account, socket or model."""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from dataclasses import replace
from typing import Any

import pytest

from ahadiff.knowledge_platform.model.contracts import ModelResult, canonical_hash
from ahadiff.knowledge_platform.model.pi_bridge import (
    MODEL,
    PiBridge,
    PiBridgeConfig,
)
from ahadiff.knowledge_platform.model.pi_bridge_stdio import serve


def identity() -> str:
    return str(uuid.uuid4())


class Runner:
    def __init__(self, output: Any = None, **fields: Any):
        self.output = output or {"type": "text", "text": "Synthetic answer."}
        self.fields = fields
        self.calls: list[Any] = []

    async def run(self, task: Any) -> ModelResult:
        self.calls.append(task)
        return ModelResult(
            task_id=task.task_id,
            status="succeeded",
            output=self.output,
            provider_id="openai",
            requested_model_id=MODEL,
            input_fingerprint=task.fingerprint,
            result_hash=canonical_hash(self.output),
            **self.fields,
        )


def setup(output: Any = None, **overrides: Any) -> tuple[PiBridge, Runner]:
    runner = Runner(output)
    config = PiBridgeConfig(
        owner_id="synthetic-owner",
        device_id="synthetic-device",
        deployment_id="synthetic-release",
        allowed_tools=("zhizhi_search", "zhizhi_job_status", "zhizhi_intake"),
        enabled=True,
        **overrides,
    )
    return PiBridge(
        runner, config, authorize=lambda _request: True, reserve_attempt=lambda _task: True
    ), runner


def request(**fields: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "id": identity(),
        "type": "turn",
        "run_id": identity(),
        "turn": 1,
        "messages": [{"role": "user", "text": "Synthetic question."}],
        **fields,
    }


def perform(bridge: PiBridge, value: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(bridge.turn(value))


def follow(first: dict[str, Any], result: dict[str, Any], **fields: Any) -> dict[str, Any]:
    call = {key: result["output"][key] for key in ("id", "name", "arguments")}
    return {
        **first,
        "id": identity(),
        "turn": first["turn"] + 1,
        "messages": [
            *first["messages"],
            {"role": "assistant", "tool_call": call},
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": call["name"],
                "result": {
                    "data": {"items": []},
                    "task_id": None,
                    "trace_id": "synthetic",
                    "data_classification": "public",
                },
            },
        ],
        **fields,
    }


def test_real_model_boundary_receives_trusted_schema_and_no_authority_from_prompt():
    bridge, runner = setup()
    result = perform(bridge, request())
    assert result["status"] == "succeeded"
    assert result["actual_model_id"] is None
    task = runner.calls[0]
    assert task.purpose == "answer" and task.schema_name == "pi_domain_turn_v1"
    assert task.allowed_provider_ids == ("openai",)
    assert task.input_classification == "personal"
    assert "owner_id" not in task.payload and "device_id" not in task.payload
    assert all("apply" not in item["name"] for item in task.payload["tools"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", "other"),
        ("device_id", "other"),
        ("model", "other"),
        ("output_schema", {}),
        ("tools", []),
        ("provider", "other"),
        ("path", "/tmp"),
        ("version", True),
        ("turn", True),
        ("turn", 0),
        ("turn", 5),
        ("id", "not-uuid"),
        ("run_id", "not-uuid"),
    ],
)
def test_request_cannot_set_capabilities(field: str, value: Any):
    bridge, runner = setup()
    assert perform(bridge, request(**{field: value}))["status"] == "blocked"
    assert not runner.calls


def test_disabled_and_input_authorizer_and_attempt_reservation_fail_before_runner():
    for attribute in ("disabled", "authorize", "reserve"):
        bridge, runner = setup()
        if attribute == "disabled":
            bridge.config = replace(bridge.config, enabled=False)
        elif attribute == "authorize":
            bridge._authorize = lambda _: False
        else:
            bridge._reserve = lambda _: False
        assert perform(bridge, request())["status"] == "blocked"
        assert not runner.calls


def test_idempotency_replays_without_model_and_changed_content_rejected():
    bridge, runner = setup()
    original = request()
    result = perform(bridge, original)
    assert perform(bridge, original) == result
    changed = {**original, "messages": [{"role": "user", "text": "Changed"}]}
    assert perform(bridge, changed)["error_code"] == "BRIDGE_IDEMPOTENCY_CONFLICT"
    assert len(runner.calls) == 1


@pytest.mark.parametrize("two_turns", [False, True])
@pytest.mark.parametrize("revocation", ["authorizer", "disabled", "authorizer_exception"])
def test_cached_success_rechecks_current_authority_without_new_attempt(two_turns, revocation):
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "synthetic"}}
        if two_turns
        else None
    )
    reservations = []
    bridge._reserve = lambda task: reservations.append(task) is None
    original = request()
    result = perform(bridge, original)
    if two_turns:
        original = follow(original, result)
        runner.output = {"type": "text", "text": "Previously authorized synthetic result."}
        result = perform(bridge, original)
    assert result["status"] == "succeeded"
    count = 2 if two_turns else 1
    if revocation == "disabled":
        bridge.config = replace(bridge.config, enabled=False)
    elif revocation == "authorizer":
        bridge._authorize = lambda _: False
    else:

        def fail_authorization(_request):
            raise RuntimeError("PRIVATE-AUTHORIZATION-ERROR")

        bridge._authorize = fail_authorization
    denied = perform(bridge, original)
    assert denied["status"] == "blocked" and denied["output"] is None
    assert "Previously authorized" not in json.dumps(denied)
    assert "PRIVATE-AUTHORIZATION-ERROR" not in json.dumps(denied)
    assert len(runner.calls) == len(reservations) == count
    bridge.config = replace(bridge.config, enabled=True)
    bridge._authorize = lambda _: True
    assert perform(bridge, original) == result
    assert len(runner.calls) == len(reservations) == count


def test_single_tool_then_answer_and_no_model_supplied_call_id():
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "synthetic"}}
    )
    first = request()
    result = perform(bridge, first)
    assert result["status"] == "succeeded"
    assert str(uuid.UUID(result["output"]["id"])) == result["output"]["id"]
    runner.output = {"type": "text", "text": "No synthetic matches."}
    second = follow(first, result)
    assert perform(bridge, second)["status"] == "succeeded"
    assert len(runner.calls) == 2
    assert perform(bridge, {**second, "id": identity(), "turn": 3})["status"] == "blocked"


@pytest.mark.parametrize(
    "output",
    [
        {"type": "tool_call", "name": "bash", "arguments": {"command": "synthetic"}},
        {
            "type": "tool_call",
            "name": "zhizhi_search",
            "arguments": {"query": "x", "owner_id": "x"},
        },
        {"type": "tool_call", "id": "chosen", "name": "zhizhi_search", "arguments": {"query": "x"}},
        {"type": "text", "text": "answer", "tool_call": {}},
        {"type": "text", "text": ""},
        {"type": "tool_call", "name": "zhizhi_intake", "arguments": {}},
        {"type": "tool_call", "name": "zhizhi_job_status", "arguments": {"task_id": "task\n"}},
    ],
)
def test_invalid_or_unknown_tool_never_emitted(output: Any):
    bridge, runner = setup(output)
    result = perform(bridge, request())
    assert result["status"] != "succeeded" and result["output"] is None
    assert len(runner.calls) == 1 and not bridge._issued


@pytest.mark.parametrize(
    "mutation", ["unissued", "name", "arguments", "history", "classification", "nested", "secret"]
)
def test_tool_receipts_require_provenance_and_unchanged_transcript(mutation: str):
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "x"}}
    )
    first = request()
    second = follow(first, perform(bridge, first))
    if mutation == "unissued":
        second["messages"][-1]["tool_call_id"] = identity()
    elif mutation == "name":
        second["messages"][-1]["name"] = "zhizhi_growth"
    elif mutation == "arguments":
        second["messages"][-2]["tool_call"]["arguments"]["query"] = "changed"
    elif mutation == "history":
        second["messages"][0]["text"] = "changed"
    elif mutation == "classification":
        second["messages"][-1]["result"]["data_classification"] = "employer"
    elif mutation == "nested":
        second["messages"][-1]["result"]["data"]["classification_labels"] = ["secret"]
    else:
        second["messages"][-1]["result"]["data"]["text"] = "sk-proj-" + "a" * 36
    assert perform(bridge, second)["status"] == "blocked"
    assert len(runner.calls) == 1


def test_fixed_tool_error_is_accepted_but_raw_errors_not():
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "x"}}
    )
    first = request()
    second = follow(first, perform(bridge, first))
    last = second["messages"][-1]
    del last["result"]
    last["error_code"] = "raw secret error"
    assert perform(bridge, second)["status"] == "blocked"
    last["error_code"] = "DOMAIN_REQUEST_FAILED"
    runner.output = {"type": "text", "text": "Unable to read."}
    assert perform(bridge, second)["status"] == "succeeded"


def test_fourth_model_turn_cannot_issue_a_fourth_tool_and_process_is_bounded():
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "x"}}
    )
    current = request()
    for number in range(3):
        runner.output["arguments"]["query"] = f"synthetic-{number}"
        result = perform(bridge, current)
        assert result["status"] == "succeeded"
        current = follow(current, result)
    assert perform(bridge, current)["status"] == "invalid_output"
    assert len(runner.calls) == 4
    assert len(runner.calls[-1].output_schema["oneOf"]) == 1
    runner.output = {"type": "text", "text": "done"}
    for _ in range(12):
        assert perform(bridge, request())["status"] == "succeeded"
    assert perform(bridge, request())["error_code"] == "BRIDGE_PROCESS_BUDGET_EXHAUSTED"
    assert len(runner.calls) == 16


@pytest.mark.parametrize(
    "values",
    [
        {"allowed_classifications": ("public", "personal"), "input_classification": "public"},
        {"allowed_classifications": ("secret",), "input_classification": "secret"},
        {
            "allowed_classifications": ("employer", "third_party_private"),
            "input_classification": "employer",
        },
        {"enabled": "yes"},
        {"allow_fixture": "yes"},
    ],
)
def test_trusted_config_still_rejects_invalid_scope(values: Any):
    defaults = {
        "owner_id": "owner",
        "device_id": "device",
        "deployment_id": "release",
        "allowed_tools": ("zhizhi_search",),
    }
    with pytest.raises(ValueError):
        PiBridgeConfig(**{**defaults, **values})


def test_provider_wait_and_failure_never_become_text_success_or_retry():
    bridge, runner = setup()

    async def wait(task: Any) -> ModelResult:
        runner.calls.append(task)
        return ModelResult(
            task_id=task.task_id, status="waiting_auth", error_code="SUBSCRIPTION_AUTH"
        )

    runner.run = wait
    original = request()
    assert perform(bridge, original)["status"] == "waiting_auth"
    assert perform(bridge, original)["status"] == "waiting_auth"
    assert len(runner.calls) == 1


def test_unapproved_fixture_result_is_not_success():
    bridge, runner = setup()
    runner.fields = {"fixture": True}
    assert perform(bridge, request())["error_code"] == "FIXTURE_RESULT_FORBIDDEN"


def test_cancel_reaches_runner_and_is_cached_without_retry():
    async def scenario():
        bridge, runner = setup()
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked(_task: Any) -> ModelResult:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        runner.run = blocked
        value = request()
        task = asyncio.create_task(bridge.turn(value))
        await entered.wait()
        task.cancel()
        assert (await task)["status"] == "cancelled"
        assert cancelled.is_set()
        assert (await bridge.turn(value))["status"] == "cancelled"

    asyncio.run(scenario())


def test_strict_stdio_rejects_duplicate_keys_and_unknown_commands_without_models():
    bridge, runner = setup()
    req_id = identity()
    lines = [
        json.dumps({"version": 1, "id": req_id, "type": "hello"}),
        '{"version":1,"version":1,"id":"' + req_id + '","type":"hello"}',
        json.dumps({"version": 1, "id": identity(), "type": "shell"}),
    ]
    destination = io.StringIO()
    asyncio.run(serve(bridge, io.BytesIO(("\n".join(lines) + "\n").encode()), destination))
    results = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert [item["status"] for item in results] == ["ready", "blocked", "blocked"]
    assert not runner.calls


def test_envelope_keeps_optional_full_labels_and_owned_list_projection():
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "x"}}
    )
    first = request()
    second = follow(first, perform(bridge, first))
    second["messages"][-1]["result"]["classification_labels"] = ["personal", "public"]
    second["messages"][-1]["result"]["data"] = []
    runner.output = {"type": "text", "text": "No matches."}
    assert perform(bridge, second)["status"] == "succeeded"
    assert runner.calls[-1].payload["transcript"][-1]["result"]["classification_labels"] == [
        "personal",
        "public",
    ]


def test_pipe_cancel_while_model_pending_and_eof_clean_up():
    import os

    async def scenario():
        bridge, runner = setup()
        entered, cleaned, answered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def waiting(_task: Any) -> ModelResult:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        runner.run = waiting

        class Output(io.StringIO):
            def write(self, text: str) -> int:
                count = super().write(text)
                if '"status":"cancelled"' in text:
                    answered.set()
                return count

        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd, "rb")
        output = Output()
        server = asyncio.create_task(serve(bridge, source, output))
        value = request()
        try:
            os.write(write_fd, (json.dumps(value) + "\n").encode())
            await asyncio.wait_for(entered.wait(), 2)
            cancel = {"version": 1, "id": identity(), "type": "cancel", "request_id": value["id"]}
            os.write(write_fd, (json.dumps(cancel) + "\n").encode())
            await asyncio.wait_for(answered.wait(), 2)
            assert cleaned.is_set()
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            assert [record["status"] for record in records] == ["cancel_requested", "cancelled"]
        finally:
            os.close(write_fd)
            await asyncio.wait_for(server, 2)
            source.close()

    asyncio.run(scenario())


def test_timeout_calls_cleanup_and_consumes_attempt_without_retry():
    from ahadiff.knowledge_platform.model.pi_bridge import BridgeLimits

    async def scenario():
        bridge, runner = setup(limits=BridgeLimits(timeout_seconds=1))
        cleanup = asyncio.Event()
        attempts = []
        bridge._reserve = lambda task: attempts.append(task.task_id) or True

        async def waiting(_task: Any) -> ModelResult:
            try:
                await asyncio.Future()
            finally:
                cleanup.set()

        runner.run = waiting
        value = request()
        response = await bridge.turn(value)
        assert response["status"] == "cancelled" and cleanup.is_set()
        assert await bridge.turn(value) == response
        assert len(attempts) == 1

    asyncio.run(scenario())


def test_protocol_limits_and_unicode_newlines_are_not_frame_delimiters():
    bridge, runner = setup()
    response = perform(bridge, request(messages=[{"role": "user", "text": "a\u2028b\u2029c"}]))
    assert response["status"] == "succeeded"
    assert runner.calls[0].payload["transcript"][0]["text"] == "a\u2028b\u2029c"
    assert (
        perform(bridge, request(messages=[{"role": "user", "text": "x" * 256001}]))["status"]
        == "blocked"
    )
    assert (
        perform(bridge, request(messages=[{"role": "user", "text": "\ud800"}]))["status"]
        == "blocked"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "another-task"),
        ("input_fingerprint", "another-input"),
        ("result_hash", "another-output"),
        ("requested_model_id", None),
        ("model_id", "another-model"),
        ("provider_id", "another-provider"),
    ],
)
def test_success_requires_correlated_runner_evidence(field: str, value: Any):
    bridge, runner = setup()
    original = runner.run

    async def mismatched(task: Any) -> ModelResult:
        return replace(await original(task), **{field: value})

    runner.run = mismatched
    result = perform(bridge, request())
    assert result["status"] == "invalid_output" and result["output"] is None


def test_model_cannot_reissue_the_identical_tool_with_a_new_call_id():
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_intake", "arguments": {"text": "synthetic"}}
    )
    first = request()
    second = follow(first, perform(bridge, first))
    result = perform(bridge, second)
    assert result["error_code"] == "BRIDGE_TOOL_REPLAY_FORBIDDEN"
    assert len(bridge._issued) == 1 and len(runner.calls) == 2


def test_unknown_write_result_only_allows_read_only_reconciliation():
    bridge, runner = setup(
        {"type": "tool_call", "name": "zhizhi_intake", "arguments": {"text": "synthetic"}}
    )
    first = request()
    second = follow(first, perform(bridge, first))
    second["messages"][-1].pop("result")
    second["messages"][-1]["error_code"] = "DOMAIN_REQUEST_FAILED"
    runner.output = {
        "type": "tool_call",
        "name": "zhizhi_intake",
        "arguments": {"text": "a replacement"},
    }
    assert perform(bridge, second)["status"] == "invalid_output"
    assert {item["name"] for item in runner.calls[-1].payload["tools"]} == {
        "zhizhi_search",
        "zhizhi_job_status",
    }
    assert len(bridge._issued) == 1


def test_turn_deadline_includes_async_preflight_not_just_model():
    from ahadiff.knowledge_platform.model.pi_bridge import BridgeLimits
    """Synthetic delay fixture: one shared deadline, no actual provider."""
    async def scenario():
        bridge, runner = setup(limits=BridgeLimits(timeout_seconds=1))
        cancelled = asyncio.Event()
        async def authorize(_request):
            await asyncio.sleep(.6)
            return True
        async def slow_model(_task):
            try:
                await asyncio.sleep(.6)
                raise AssertionError('shared deadline did not cover authorization')
            finally:
                cancelled.set()
        bridge._authorize = authorize
        runner.run = slow_model
        result = await bridge.turn(request())
        assert result['status'] == 'cancelled'
        assert cancelled.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['authorize', 'reserve'])
def test_async_preflight_keeps_exclusive_inflight_ownership(stage):
    """Mocked callback fixture, not a real model request."""
    async def scenario():
        bridge, runner = setup()
        entered, release = asyncio.Event(), asyncio.Event()
        reserves = []
        async def permission(_value):
            entered.set()
            await release.wait()
            return True
        def reserve(task):
            reserves.append(task.task_id)
            return True
        async def slow_reserve(task):
            await permission(task)
            return reserve(task)
        bridge._reserve = slow_reserve if stage == 'reserve' else reserve
        if stage == 'authorize':
            bridge._authorize = permission
        first_request = request()
        first = asyncio.create_task(bridge.turn(first_request))
        await entered.wait()
        assert (await bridge.turn(request()))['error_code'] == 'BRIDGE_BUSY'
        assert (await bridge.turn(first_request))['error_code'] == 'BRIDGE_REQUEST_IN_PROGRESS'
        release.set()
        await first
        assert len(reserves) == 1
        assert not bridge._inflight
    asyncio.run(scenario())


def test_real_jsonl_pipe_cancels_readonly_preflight_before_reservation():
    """Real local pipe and serve loop, deterministic read fixture, zero model calls."""
    import os
    import threading
    async def scenario():
        bridge, runner = setup()
        entered, release = threading.Event(), threading.Event()
        answered = asyncio.Event()
        reserves = []
        def readonly():
            entered.set()
            assert release.wait(3)
            return True
        async def authorize(_request):
            return await asyncio.to_thread(readonly)
        bridge._authorize = authorize
        bridge._reserve = lambda task: reserves.append(task.task_id) or True
        class Output(io.StringIO):
            def write(self, text):
                count = super().write(text)
                if '"status":"cancelled"' in text:
                    answered.set()
                return count
        read_fd, write_fd = os.pipe()
        source = os.fdopen(read_fd, 'rb')
        output = Output()
        server = asyncio.create_task(serve(bridge, source, output))
        value = request()
        try:
            os.write(write_fd, (json.dumps(value)+'\n').encode())
            assert await asyncio.to_thread(entered.wait, 2)
            cancel = {'version':1,'id':identity(),'type':'cancel','request_id':value['id']}
            os.write(write_fd, (json.dumps(cancel)+'\n').encode())
            await asyncio.wait_for(answered.wait(), 1)
            assert [json.loads(line)['status'] for line in output.getvalue().splitlines()] == ['cancel_requested','cancelled']
            release.set()
            await asyncio.sleep(.01)
            assert not reserves and not runner.calls and not bridge._inflight
        finally:
            release.set()
            os.close(write_fd)
            await asyncio.wait_for(server, 2)
            source.close()
    asyncio.run(scenario())


def test_failure_diagnostic_projects_only_closed_vocabulary():
    from ahadiff.knowledge_platform.model.diagnostics import ModelFailure
    bridge, runner = setup()
    async def failed(task):
        return ModelResult(task_id=task.task_id, status='invalid_output', output=None,
                           input_fingerprint=task.fingerprint, error_code='OUTPUT_SCHEMA_INVALID',
                           failure=ModelFailure(stage='output', kind='output', summary='output_validation_failed'))
    runner.run = failed
    response = perform(bridge, request())
    assert response['status'] == 'invalid_output'
    assert response['output'] is None
    assert response['failure'] == {'stage': 'output', 'kind': 'output', 'summary': 'output_validation_failed'}
    assert response['error_code'] == 'OUTPUT_SCHEMA_INVALID'


def _production_codex_bridge(monkeypatch):
    """Production adapter boundary with an unreachable executable, not live E2E."""
    from unittest.mock import AsyncMock

    from ahadiff.knowledge_platform.model.codex_runner import CodexModelRunner, CodexRunnerConfig
    from ahadiff.knowledge_platform.model.pi_bridge import BridgeLimits
    from ahadiff.knowledge_platform.model.pi_runner import SubscriptionRoute

    runner = CodexModelRunner(CodexRunnerConfig(
        enabled=True, reuse_existing_login=True, executable=("MUST_NOT_EXECUTE",),
        routes=(SubscriptionRoute(provider_id="openai", model_id=MODEL,
                                  account_qualified=True, allowed_classifications=("personal",)),),
    ))
    bridge, _ = setup()
    bridge.runner = runner
    bridge.config = replace(
        bridge.config, allowed_tools=("zhizhi_search",),
        limits=BridgeLimits(model_calls_per_run=2, tool_calls_per_run=1,
                            model_calls_per_process=2, timeout_seconds=120),
    )
    reserved = []
    bridge._reserve = lambda task: reserved.append(task) or True
    process = AsyncMock(side_effect=AssertionError("Actual process execution forbidden"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", process)
    return bridge, runner, reserved, process


def test_production_pi_codex_schema_boundary_no_model_two_turns(monkeypatch):
    """Exercise both real adapters; only the model execution is synthetic."""
    from unittest.mock import AsyncMock

    from ahadiff.knowledge_platform.model.validation import parse_output, validate_schema

    bridge, runner, reserved, process = _production_codex_bridge(monkeypatch)
    outputs = [
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "synthetic"}},
        {"type": "text", "text": "Synthetic answer, not real model evidence."},
    ]

    async def synthetic_execute(task, prompt, execution):
        validate_schema(task.output_schema)
        assert json.loads(prompt)["output_schema"] == task.output_schema
        output = parse_output(json.dumps(outputs.pop(0)), task.output_schema)
        return output, {}, ()

    execute = AsyncMock(side_effect=synthetic_execute)
    monkeypatch.setattr(runner, "_execute", execute)
    first_request = request()
    first = perform(bridge, first_request)
    assert first["status"] == "succeeded"
    assert first["output"]["type"] == "tool_call"
    second = perform(bridge, follow(first_request, first))
    assert second["status"] == "succeeded"
    assert second["output"]["type"] == "text"
    assert execute.await_count == len(reserved) == 2
    assert len(reserved[0].output_schema["oneOf"]) == 2
    assert len(reserved[1].output_schema["oneOf"]) == 1
    assert all(task.output_schema["type"] == "object" for task in reserved)
    assert all("failure" not in result for result in (first, second))
    process.assert_not_awaited()


@pytest.mark.parametrize("code", [
    "NO_APPROVED_SUBSCRIPTION_ROUTE", "SECRET_INPUT_FORBIDDEN", "REQUEST_SCHEMA_INVALID",
])
def test_production_pi_codex_pre_execution_failures_are_bounded(monkeypatch, code):
    """The old missing root type deterministically fails before any CLI call."""
    from unittest.mock import AsyncMock

    from ahadiff.knowledge_platform.model import pi_bridge

    bridge, runner, reserved, process = _production_codex_bridge(monkeypatch)
    execute = AsyncMock(side_effect=AssertionError("Model execution forbidden"))
    monkeypatch.setattr(runner, "_execute", execute)
    if code == "NO_APPROVED_SUBSCRIPTION_ROUTE":
        runner.config = replace(runner.config, routes=())
    elif code == "SECRET_INPUT_FORBIDDEN":
        # Synthetic canary, never a real credential. The normal secret gate stays active.
        monkeypatch.setattr(pi_bridge, "_INSTRUCTIONS", "sk-proj-" + "a" * 36)
    else:
        schema = bridge._schema
        monkeypatch.setattr(bridge, "_schema", lambda names: {"oneOf": schema(names)["oneOf"]})
    response = perform(bridge, request())
    assert response["status"] == "blocked" and response["output"] is None
    assert response["error_code"] == code
    assert response["failure"] == {
        "stage": "validation", "kind": "policy", "summary": "policy_rejected",
    }
    assert len(reserved) == 1
    assert not bridge._issued
    assert "sk-proj-" not in json.dumps(response)
    execute.assert_not_awaited()
    process.assert_not_awaited()


@pytest.mark.parametrize("tools", [("zhizhi_search",), ()])
def test_explicit_object_root_preserves_closed_union_acceptance(tools):
    from jsonschema import Draft202012Validator

    from ahadiff.knowledge_platform.model.validation import validate_schema

    bridge, _ = setup()
    schema = bridge._schema(tools)
    validate_schema(schema)
    old = {"oneOf": schema["oneOf"]}
    valid = [{"type": "text", "text": "Synthetic"}]
    if tools:
        valid.append({"type": "tool_call", "name": "zhizhi_search",
                      "arguments": {"query": "synthetic"}})
    invalid = [None, [], 1, "text", {},
               {"type": "text", "text": "Synthetic", "extra": True},
               {"type": "tool_call", "name": "untrusted", "arguments": {}},
               {"type": "tool_call", "name": "zhizhi_search",
                "arguments": {"query": "synthetic", "owner": "extra"}},
               {"type": "text", "text": "ok", "name": "zhizhi_search", "arguments": {}}]
    if not tools:
        invalid.append({"type": "tool_call", "name": "zhizhi_search",
                        "arguments": {"query": "synthetic"}})
    for value in valid + invalid:
        accepted = Draft202012Validator(schema).is_valid(value)
        assert accepted == (value in valid)
        assert accepted == Draft202012Validator(old).is_valid(value)
