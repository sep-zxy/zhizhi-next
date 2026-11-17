"""Inert identities and synthetic transport only, never an operational activation."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import shutil
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
import test_acceptance_model as acceptance_tests
from test_acceptance_model import USER, approved, generated, retained

from ahadiff.knowledge_platform import pi_acceptance as pi
from ahadiff.knowledge_platform.acceptance_model import AcceptanceScopeError, ScopeReceipts
from ahadiff.knowledge_platform.model.codex_runner import CodexModelRunner, CodexRunnerConfig
from ahadiff.knowledge_platform.model.contracts import ModelResult, canonical_hash
from ahadiff.knowledge_platform.model.pi_bridge import BridgeLimits, PiBridge, PiBridgeConfig
from ahadiff.knowledge_platform.owner_identity import OWNER


@pytest.fixture(name="real_scope_fixture")
def real_scope(tmp_path):
    yield from acceptance_tests.scoped.__wrapped__(tmp_path)


@pytest.fixture
def slot(tmp_path):
    (tmp_path / "data/owner-acceptance").mkdir(parents=True)
    (tmp_path / "data/owner-acceptance/identity.json").write_text(
        json.dumps({"format": "zhizhi-owner-acceptance-dpapi-v1", "protected": "FIXTURE_ONLY"})
    )
    entry = {
        "entry_id": str(uuid4()),
        "revision": 1,
        "content_hash": hashlib.sha256(pi.QUERY.encode()).hexdigest(),
        "title": "Synthetic signed source",
        "body": pi.QUERY,
        "owner_id": OWNER,
        "visibility": "private",
        "evidence_refs": [],
        "metadata": {},
        "data_classification": "personal",
        "classification_labels": ["personal"],
        "risk_flags": [],
        "relations": [],
        "commit_sha": "a" * 40,
    }
    identity = Mock(
        token="INERT-SYNTHETIC-OWNER", device_id="a" * 32, origin="http://127.0.0.1:8766"
    )
    context = pi._Context(
        tmp_path,
        identity,
        {"synthetic": True},
        ScopeReceipts(tmp_path, tmp_path / "base", {"synthetic": True}, identity.token),
        {},
    )
    with patch.object(pi, "_source", return_value=entry):
        scope = pi.PiScope(context)
        yield scope, entry


def enabled(slot):
    scope, _ = slot
    scope.receipts.put("activation", scope.binding)
    return scope


def bridge(scope, output=None):
    transport = CodexModelRunner(CodexRunnerConfig())

    async def run(task):
        value = output or (
            {"type": "tool_call", "name": "zhizhi_search", "arguments": pi.ARGUMENTS}
            if len(task.payload["transcript"]) == 1
            else {"type": "text", "text": "Synthetic match"}
        )
        return ModelResult(
            task_id=task.task_id,
            status="succeeded",
            output=value,
            input_fingerprint=task.fingerprint,
            result_hash=canonical_hash(value),
            provider_id="openai",
            requested_model_id=pi.MODEL,
            runtime_version="codex-cli " + pi.VERSION,
        )

    transport.run = AsyncMock(side_effect=run)
    result = pi._AcceptancePiBridge(
        pi._PiTransport(scope, transport),
        PiBridgeConfig(
            owner_id=OWNER,
            device_id="a" * 32,
            deployment_id="synthetic",
            allowed_tools=("zhizhi_search",),
            enabled=True,
            limits=BridgeLimits(
                model_calls_per_run=2,
                tool_calls_per_run=1,
                model_calls_per_process=2,
                timeout_seconds=120,
            ),
        ),
        authorize=scope.authorize_async,
        reserve_attempt=scope.reserve_model_async,
    )
    return result, transport


def request(run=None, turn=1, messages=None):
    return {
        "version": 1,
        "type": "turn",
        "id": str(uuid4()),
        "run_id": run or str(uuid4()),
        "turn": turn,
        "messages": messages or [{"role": "user", "text": pi.PI_PROMPT}],
    }


def after_tool(scope, initial, first):
    call = {key: value for key, value in first["output"].items() if key != "type"}
    assert scope.reserve_search("synthetic-trace") is True
    source = scope.binding["source"]
    envelope = {
        "data": {"results": [{k: source[k] for k in ("entry_id", "revision", "content_hash")}]},
        "task_id": None,
        "trace_id": "synthetic-trace",
        "data_classification": "personal",
    }
    scope.record_result(envelope)
    messages = [
        *initial["messages"],
        {"role": "assistant", "tool_call": call},
        {"role": "tool", "tool_call_id": call["id"], "name": "zhizhi_search", "result": envelope},
    ]
    return request(initial["run_id"], 2, messages)


def test_old_activation_and_owner_identity_do_not_enable_pi(slot):
    scope, _ = slot
    scope.context.base_receipts.put("activation", {"synthetic": True})
    with pytest.raises(AcceptanceScopeError, match="EXPLICIT_ACTIVATION"):
        scope.agent_token()
    assert not scope.receipts.directory.exists()
    with patch.object(pi, "_load_context") as load, pytest.raises(AcceptanceScopeError):
        pi.activate(approved=False, expected_source="a" * 40, expected_release="unused")
    load.assert_not_called()


def test_agent_bearer_is_separate_bound_and_never_written(slot):
    scope = enabled(slot)
    token = scope.agent_token()
    assert token != scope.context.identity.token
    actor = scope.agent()
    assert actor.kind == "agent" and actor.scopes == frozenset({"knowledge:read"})
    assert actor.owner_id == OWNER and actor.actor_id == pi.AGENT
    for path in scope.receipts.directory.iterdir():
        assert (
            token not in path.read_text() and scope.context.identity.token not in path.read_text()
        )
    original = scope.binding["source"]["revision"]
    scope.binding["source"]["revision"] += 1
    with pytest.raises(AcceptanceScopeError):
        scope.agent_token()
    scope.binding["source"]["revision"] = original


def test_source_revision_change_invalidates_even_exact_fixed_prompt(slot):
    scope = enabled(slot)
    slot[1]["revision"] += 1
    instance, transport = bridge(scope)
    result = asyncio.run(instance.turn(request()))
    assert result["status"] == "blocked" and transport.run.await_count == 0


def test_two_real_bridge_turns_need_actual_model_tool_and_signed_tool_receipt(slot):
    scope = enabled(slot)
    instance, transport = bridge(scope)
    initial = request()
    first = asyncio.run(instance.turn(initial))
    assert first["status"] == "succeeded" and first["output"]["type"] == "tool_call"
    second_request = after_tool(scope, initial, first)
    second = asyncio.run(instance.turn(second_request))
    assert second["status"] == "succeeded" and second["output"]["type"] == "text"
    assert transport.run.await_count == 2
    assert asyncio.run(instance.turn(second_request)) == second
    assert transport.run.await_count == 2  # Same-process exact replay is local only.
    with pytest.raises(AcceptanceScopeError):
        scope.reserve_search("another-trace")


def test_completion_summary_checks_signed_offline_fixture_results_without_printing_text(slot):
    # Synthetic transport metadata exercises checks, never counts as live acceptance.
    scope = enabled(slot)
    instance, transport = bridge(scope)
    with patch.object(pi, "active_scope", return_value=scope):
        with pytest.raises(AcceptanceScopeError):
            pi.completion_summary(scope.context.root)
        initial = request()
        first = asyncio.run(instance.turn(initial))
        with pytest.raises(AcceptanceScopeError):
            pi.completion_summary(scope.context.root)
        second = after_tool(scope, initial, first)
        assert asyncio.run(instance.turn(second))["status"] == "succeeded"
        summary = pi.completion_summary(scope.context.root)
        assert summary["status"] == "pi_source_search_verified"
        assert summary["final_delivery"] is False
        assert "Synthetic match" not in json.dumps(summary)
        assert (
            pi.completion_summary(
                scope.context.root,
                answer_sha256=hashlib.sha256(b"Synthetic match").hexdigest(),
                search_result_hash=canonical_hash(scope.receipts.read("search-result")["envelope"]),
            )
            == summary
        )
        for wrong in ({"answer_sha256": "0" * 64}, {"search_result_hash": "0" * 64}):
            with pytest.raises(AcceptanceScopeError):
                pi.completion_summary(scope.context.root, **wrong)
        assert transport.run.await_count == 2
        original_read = scope.receipts.read
        for key in ("model-result:1", "model-result:2", "search-attempt", "search-result"):
            with (
                patch.object(
                    scope.receipts,
                    "read",
                    side_effect=lambda name, key=key: (
                        None if name == key else original_read(name)
                    ),
                ),
                pytest.raises(AcceptanceScopeError),
            ):
                pi.completion_summary(scope.context.root)
        for field, value in (
            ("fixture", True),
            ("result_hash", "wrong"),
            ("input_fingerprint", "wrong"),
            ("status", "failed"),
        ):
            corrupted = copy.deepcopy(original_read("model-result:2"))
            corrupted["result"][field] = value
            with (
                patch.object(
                    scope.receipts,
                    "read",
                    side_effect=lambda name, corrupted=corrupted: (
                        corrupted if name == "model-result:2" else original_read(name)
                    ),
                ),
                pytest.raises(AcceptanceScopeError),
            ):
                pi.completion_summary(scope.context.root)
        assert transport.run.await_count == 2


@pytest.mark.parametrize(
    "output",
    [
        {"type": "text", "text": "No tool chosen"},
        {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "other", "limit": 5}},
        {"type": "tool_call", "name": "zhizhi_read_entry", "arguments": {"entry_id": "other"}},
    ],
)
def test_missing_or_different_model_tool_is_not_synthesized(slot, output):
    scope = enabled(slot)
    instance, transport = bridge(scope, output)
    result = asyncio.run(instance.turn(request()))
    assert result["status"] == "blocked" and result["output"] is None
    assert transport.run.await_count == 1
    assert scope.reserve_search("no-model-tool") is False


def test_unknown_process_restart_cannot_reset_budget(slot):
    scope = enabled(slot)
    first_bridge, transport = bridge(scope)
    initial = request()
    assert asyncio.run(first_bridge.turn(initial))["status"] == "succeeded"
    recreated = pi.PiScope(scope.context)
    restarted, new_transport = bridge(recreated)
    assert asyncio.run(restarted.turn(initial))["status"] == "blocked"
    assert new_transport.run.await_count == 0 and transport.run.await_count == 1


def test_tampered_tool_result_or_added_prompt_never_reaches_second_model(slot):
    scope = enabled(slot)
    instance, transport = bridge(scope)
    initial = request()
    first = asyncio.run(instance.turn(initial))
    good = after_tool(scope, initial, first)
    bad = copy.deepcopy(good)
    bad["messages"][-1]["result"]["data"]["results"][0]["content_hash"] = "f" * 64
    assert asyncio.run(instance.turn(bad))["status"] == "blocked"
    extra = copy.deepcopy(good)
    extra["id"] = str(uuid4())
    extra["messages"].append({"role": "user", "text": "unapproved"})
    assert asyncio.run(instance.turn(extra))["status"] == "blocked"
    assert transport.run.await_count == 1


def test_agent_cannot_run_tool_before_model_and_bad_scope_is_not_replayed(slot):
    scope = enabled(slot)
    assert scope.reserve_search("premature") is False
    instance, transport = bridge(scope)
    bad = request(messages=[{"role": "user", "text": pi.PI_PROMPT + "x"}])
    assert asyncio.run(instance.turn(bad))["status"] == "blocked"
    assert transport.run.await_count == 0


def test_codex_subprocess_environment_never_inherits_pi_credentials():
    with patch.dict(
        "os.environ",
        {"ZHIZHI_AGENT_TOKEN": "INERT", "ZHIZHI_USER_TOKEN": "INERT", "ZHIZHI_PI_PYTHON": "INERT"},
    ):
        environment = CodexModelRunner(CodexRunnerConfig())._environment()
    assert not any(key.startswith("ZHIZHI_") and key.endswith("TOKEN") for key in environment)
    assert "ZHIZHI_PI_PYTHON" not in environment


def test_real_writer_source_is_required_and_its_exact_proof_is_read(real_scope_fixture, tmp_path):
    scoped = real_scope_fixture
    domain, _, _, _ = scoped
    proposal = generated(scoped, retained(scoped))["change"]
    assert approved(scoped, proposal["change_id"])["status"] == "approved"
    from test_acceptance_model import WRITER

    written = domain.apply_change(
        proposal["change_id"], actor=WRITER, idempotency_key="source-for-pi"
    )
    entry = domain.get_entry(written["entry_id"], actor=USER)
    domain.close()
    state = tmp_path / "data/owner-acceptance/platform"
    shutil.copytree(tmp_path / "state", state)
    (state.parent / "identity.json").write_text(
        json.dumps({"format": "zhizhi-owner-acceptance-dpapi-v1", "protected": "FIXTURE_ONLY"})
    )
    identity = Mock(token="INERT", device_id="a" * 32, origin="http://127.0.0.1:8766")
    identity.actor.return_value = USER
    context = pi._Context(tmp_path, identity, {"synthetic": True}, domain.scope_receipts, {})
    assert pi._source(context)["content_hash"] == entry["content_hash"]
    assert pi.PiScope(context).binding["source"]["entry_id"] == entry["entry_id"]


@pytest.mark.parametrize("label", ["employer", "secret", "unknown", "third_party_private"])
@pytest.mark.parametrize("stage", ["activation", "first", "receipt", "second"])
def test_classification_drift_blocks_without_new_model_or_receipt(slot, label, stage):
    scope, entry = slot
    scope = enabled(slot)
    instance, transport = bridge(scope)
    initial = request()
    old_binding = pi._source_binding(entry)
    second = None
    if stage in {"receipt", "second"}:
        first = asyncio.run(instance.turn(initial))
        assert first["status"] == "succeeded"
        if stage == "second":
            second = after_tool(scope, initial, first)
        else:
            assert scope.reserve_search("classification-change")
    calls_before = transport.run.await_count
    # Only propagated classification changes: the exact body/revision/hash remain.
    entry["classification_labels"] = ["personal", label]
    assert pi._source_binding(entry) != old_binding
    if stage == "activation":
        with pytest.raises(Exception, match="classification"):
            pi.PiScope(scope.context)
    elif stage == "receipt":
        with pytest.raises(Exception, match="classification"):
            scope.record_result({"trace_id": "classification-change", "data": {"results": []}})
        assert scope.receipts.read("search-result") is None
    else:
        assert asyncio.run(instance.turn(second or initial))["status"] == "blocked"
    assert transport.run.await_count == calls_before


def test_source_commit_and_all_labels_are_bound(slot):
    scope = enabled(slot)
    slot[1]["commit_sha"] = "c" * 40
    with pytest.raises(AcceptanceScopeError, match="SOURCE_CHANGED"):
        scope.read_source()


def test_actual_search_boundary_and_bridge_share_persistent_capability(slot):
    from ahadiff.knowledge_platform.model.pi_search_boundary import PiSearchBoundary

    scope = enabled(slot)
    instance, transport = bridge(scope)
    initial = request()
    first = asyncio.run(instance.turn(initial))
    assert first["status"] == "succeeded"
    fallback = AsyncMock()
    boundary = PiSearchBoundary(
        fallback,
        agent_token=scope.agent_token(),
        origin=scope.context.identity.origin,
        actor=scope.agent(),
        read_source=scope.read_source,
        reserve_search=scope.reserve_search,
        record_result=scope.record_result,
    )

    async def search():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=boundary), base_url=scope.context.identity.origin
        ) as client:
            headers = {"Authorization": "Bearer " + scope.agent_token()}
            path = "/api/knowledge/v1/search?" + urlencode({"q": pi.QUERY, "limit": 5})
            response = await client.get(path, headers=headers)
            replay = await client.get(path, headers=headers)
            other = await client.get("/api/knowledge/v1/entries", headers=headers)
            return response, replay, other

    response, replay, other = asyncio.run(search())
    assert response.status_code == 200 and replay.status_code == other.status_code == 403
    fallback.assert_not_called()
    envelope = response.json()
    assert scope.receipts.read("search-result") == {"envelope": envelope, "matched": True}
    row = envelope["data"]["results"][0]
    assert row["content_hash"] == slot[1]["content_hash"] and row["snippet"] == pi.QUERY
    call = {key: value for key, value in first["output"].items() if key != "type"}
    messages = [
        *initial["messages"],
        {"role": "assistant", "tool_call": call},
        {"role": "tool", "tool_call_id": call["id"], "name": "zhizhi_search", "result": envelope},
    ]
    second = asyncio.run(instance.turn(request(initial["run_id"], 2, messages)))
    assert second["status"] == "succeeded" and transport.run.await_count == 2


def test_classification_changes_during_model_prevent_result_receipt(slot):
    scope = enabled(slot)
    instance, transport = bridge(scope)
    original = transport.run.side_effect

    async def changed(task):
        result = await original(task)
        slot[1]["classification_labels"] = ["personal", "secret"]
        return result

    transport.run.side_effect = changed
    result = asyncio.run(instance.turn(request()))
    assert result["status"] == "blocked" and transport.run.await_count == 1
    assert scope.receipts.read("model-result:1") is None
    assert scope.receipts.read("model-attempt:1") is not None


def test_explicit_pi_activation_idempotent_and_source_release_bound(slot):
    scope, _ = slot
    scope.context.base_binding.update(source_commit="a" * 40, release_id="synthetic-release")
    with (
        patch.object(pi, "_acceptance_root", return_value=scope.context.root),
        patch.object(pi, "_load_context", return_value=scope.context),
    ):
        with pytest.raises(AcceptanceScopeError, match="RELEASE_MISMATCH"):
            pi.activate(
                approved=True, expected_source="b" * 40, expected_release="synthetic-release"
            )
        assert not scope.receipts.directory.exists()
        assert (
            pi.activate(
                approved=True, expected_source="a" * 40, expected_release="synthetic-release"
            )
            == "pi_activated_not_started"
        )
        assert (
            pi.activate(
                approved=True, expected_source="a" * 40, expected_release="synthetic-release"
            )
            == "pi_already_activated"
        )


@pytest.mark.parametrize("turn", [1, 2])
def test_cached_success_rechecks_current_source_classification(slot, turn):
    scope = enabled(slot)
    instance, transport = bridge(scope)
    initial = request()
    first = asyncio.run(instance.turn(initial))
    replay = initial
    if turn == 2:
        replay = after_tool(scope, initial, first)
        assert asyncio.run(instance.turn(replay))["status"] == "succeeded"
    original_calls = transport.run.await_count
    original_attempt = scope.receipts.read(f"model-attempt:{turn}")
    slot[1]["classification_labels"] = ["personal", "employer"]
    blocked = asyncio.run(instance.turn(replay))
    assert blocked["status"] == "blocked" and blocked["output"] is None
    assert transport.run.await_count == original_calls
    assert scope.receipts.read(f"model-attempt:{turn}") == original_attempt
    slot[1]["classification_labels"] = ["personal"]
    assert asyncio.run(instance.turn(replay))["status"] == "succeeded"
    assert transport.run.await_count == original_calls


@pytest.mark.parametrize('blocked_read', [1, 2])
@pytest.mark.parametrize('interrupt', ['cancel', 'deadline', 'revocation'])
def test_production_async_preflight_cannot_late_reserve_or_block_loop(slot, blocked_read, interrupt):
    """Deterministic read-only delay fixture, not a model or Windows execution."""
    import threading
    scope = enabled(slot)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = scope.read_source
    count = 0
    worker_ids = []

    def delayed_read():
        nonlocal count
        worker_ids.append(threading.get_ident())
        entry = original()
        count += 1
        if count == blocked_read:
            entered.set()
            assert release.wait(4), 'fixture release missing'
            finished.set()
        return entry

    async def journey():
        runner = Mock(run=AsyncMock())
        instance = PiBridge(runner, PiBridgeConfig(
            owner_id=pi.OWNER, device_id=scope.context.identity.device_id,
            deployment_id='synthetic-async', allowed_tools=('zhizhi_search',), enabled=True,
            limits=BridgeLimits(model_calls_per_run=2, model_calls_per_process=2,
                                tool_calls_per_run=1, timeout_seconds=1)),
            authorize=scope.authorize_async, reserve_attempt=scope.reserve_model_async)
        with patch.object(scope, 'read_source', side_effect=delayed_read):
            task = asyncio.create_task(instance.turn(request()))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                heartbeat = asyncio.Event()
                asyncio.get_running_loop().call_soon(heartbeat.set)
                await asyncio.wait_for(heartbeat.wait(), .2)
                assert not runner.run.called
                if interrupt == 'cancel':
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif interrupt == 'deadline':
                    result = await task
                    assert result['error_code'] == 'BRIDGE_TIMEOUT'
                else:
                    with patch.object(scope, 'require_active', side_effect=AcceptanceScopeError('PRIVATE-FIXTURE')):
                        release.set()
                        result = await task
                    assert result['error_code'] == 'BRIDGE_INVALID_REQUEST'
                    assert 'PRIVATE-FIXTURE' not in json.dumps(result)
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                await asyncio.sleep(.01)
            runner.run.assert_not_called()
            for key in ('run', 'model-attempt:1', 'model-attempt:2'):
                assert scope.receipts.read(key) is None
            assert worker_ids and threading.get_ident() not in worker_ids
    asyncio.run(journey())


def test_packaged_factory_uses_cancellable_read_callbacks_without_calls(slot):
    scope = enabled(slot)
    scope.context.base_activation.update(
        codex_exe=str(scope.context.root / 'synthetic' / 'codex.exe'),
        codex_home=str(scope.context.root / 'synthetic'),
    )
    with patch.object(pi, 'active_scope', return_value=scope), \
         patch.object(pi, '_existing_cli_path', side_effect=lambda path, **kwargs: str(path)):
        instance = pi.create_bridge(scope.context.root)
    assert instance._authorize == scope.authorize_async
    assert instance._reserve == scope.reserve_model_async
    assert instance.config.limits.timeout_seconds == 120
    assert instance.config.limits.model_calls_per_process == 2
    assert scope.receipts.read('run') is None


@pytest.mark.parametrize('blocked_read', [3, 4])
def test_transport_source_rechecks_cancel_without_late_model_or_result(slot, blocked_read):
    """Production wrapper with synthetic runner; no external model or credentials."""
    import threading
    from dataclasses import replace
    scope = enabled(slot)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = scope.read_source
    calls = 0
    def delayed_read():
        nonlocal calls
        value = original()
        calls += 1
        if calls == blocked_read:
            entered.set()
            assert release.wait(4)
            finished.set()
        return value
    async def journey():
        instance, runner = bridge(scope)
        instance.config = replace(instance.config, limits=replace(instance.config.limits, timeout_seconds=1))
        with patch.object(scope, 'read_source', side_effect=delayed_read):
            task = asyncio.create_task(instance.turn(request()))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert (await task)['status'] == 'cancelled'
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                await asyncio.sleep(.01)
        assert runner.run.await_count == (0 if blocked_read == 3 else 1)
        assert scope.receipts.read('model-attempt:1') is not None
        assert scope.receipts.read('model-result:1') is None
    asyncio.run(journey())


def production_acceptance(slot, monkeypatch, outputs):
    """Production bridge/runner; synthetic execution only, zero CLI or model calls."""
    from ahadiff.knowledge_platform.model.pi_runner import RpcFailure, SubscriptionRoute
    from ahadiff.knowledge_platform.model.validation import OUTPUT_ERRORS, parse_output, validate_schema

    scope = enabled(slot)
    instance, _ = bridge(scope)
    runner = CodexModelRunner(CodexRunnerConfig(
        enabled=True, reuse_existing_login=True, executable=('MUST_NOT_EXECUTE',),
        expected_version=pi.VERSION,
        routes=(SubscriptionRoute(provider_id='openai', model_id=pi.MODEL,
                                 account_qualified=True, allowed_classifications=('personal',)),),
    ))
    seen = []

    async def execute(task, prompt, execution):
        validate_schema(task.output_schema)
        visible = json.loads(prompt)
        assert visible['output_schema'] == task.output_schema
        assert scope.receipts.read(f'model-attempt:{len(seen) + 1}')['fingerprint'] == task.fingerprint
        seen.append(task)
        try:
            parsed = parse_output(json.dumps(outputs.pop(0)), task.output_schema)
        except OUTPUT_ERRORS:
            # Match the production _execute boundary, without starting a CLI.
            raise RpcFailure('OUTPUT_SCHEMA_INVALID', 'invalid_output') from None
        return parsed, {}, ()

    runner._execute = AsyncMock(side_effect=execute)
    instance.runner = pi._PiTransport(scope, runner)
    process = AsyncMock(side_effect=AssertionError('CLI execution forbidden'))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', process)
    return scope, instance, runner, seen, process


def test_acceptance_production_contract_exact_two_turns(slot, monkeypatch):
    from jsonschema import Draft202012Validator

    scope, instance, runner, seen, process = production_acceptance(slot, monkeypatch, [
        {'type': 'tool_call', 'name': 'zhizhi_search', 'arguments': pi.ARGUMENTS},
        {'type': 'text', 'text': 'Synthetic result, not live acceptance.'},
    ])
    initial = request()
    first = asyncio.run(instance.turn(initial))
    assert first['status'] == 'succeeded'
    second = asyncio.run(instance.turn(after_tool(scope, initial, first)))
    assert second['status'] == 'succeeded'
    assert runner._execute.await_count == len(seen) == 2
    assert len(seen[0].output_schema['oneOf']) == len(seen[1].output_schema['oneOf']) == 1
    assert seen[0].payload['tools'][0]['parameters'] == seen[0].output_schema['oneOf'][0]['properties']['arguments']
    assert seen[1].payload['tools'] == []
    assert not Draft202012Validator(seen[1].output_schema).is_valid(first['output'])
    assert 'limit 5' in seen[0].instructions
    assert scope.receipts.read('model-result:2')
    process.assert_not_awaited()


@pytest.mark.parametrize('output', [
    {'type': 'text', 'text': 'Synthetic clarification'},
    {'type': 'tool_call', 'name': 'zhizhi_search', 'arguments': {'query': pi.QUERY}},
    {'type': 'tool_call', 'name': 'zhizhi_search', 'arguments': {'query': pi.QUERY, 'limit': 10}},
    {'type': 'tool_call', 'name': 'zhizhi_search', 'arguments': {'query': 'other', 'limit': 5}},
])
def test_acceptance_production_parser_rejects_generic_valid_outputs(slot, monkeypatch, output):
    from jsonschema import Draft202012Validator

    scope, instance, runner, seen, process = production_acceptance(slot, monkeypatch, [output])
    generic = PiBridge._schema(PiBridge.__new__(PiBridge), ('zhizhi_search',))
    assert Draft202012Validator(generic).is_valid(output)
    result = asyncio.run(instance.turn(request()))
    assert result['status'] != 'succeeded'
    assert result['error_code'] == 'OUTPUT_SCHEMA_INVALID'
    assert runner._execute.await_count == len(seen) == 1
    assert scope.receipts.read('model-result:1') is None
    assert scope.receipts.read('search-attempt') is None
    process.assert_not_awaited()


@pytest.mark.parametrize('reason', [
    'pi_scope_attempt_binding', 'pi_scope_source_before', 'pi_scope_source_after',
    'pi_scope_result_validation', 'pi_scope_first_output', 'pi_scope_second_output',
])
def test_scope_guard_closed_diagnostic_branches(slot, reason):
    from dataclasses import replace
    from ahadiff.knowledge_platform.model.diagnostics import sanitized_failure

    scope = enabled(slot)
    instance, transport = bridge(scope)
    # Generic schema deliberately bypasses the acceptance-specific model contract.
    # The independent first/second-turn guards must still refuse bad transport output.
    instance._schema = lambda names: PiBridge._schema(PiBridge.__new__(PiBridge), ('zhizhi_search',))
    captured = []
    original_reserve = instance._reserve

    async def reserve(task):
        captured.append(task)
        return await original_reserve(task)

    instance._reserve = reserve
    first = asyncio.run(instance.turn(request()))
    assert first['status'] == 'succeeded'
    task = captured[0]
    good = {'type': 'tool_call', 'name': 'zhizhi_search', 'arguments': pi.ARGUMENTS}
    if reason == 'pi_scope_second_output':
        task = replace(task, payload={'transcript': [{}, {}, {}]})
        scope.receipts.read = Mock(return_value={'fingerprint': task.fingerprint})
    elif reason == 'pi_scope_attempt_binding':
        scope.receipts.read = Mock(return_value=None)
    output = {'type': 'text', 'text': 'Synthetic only'} if reason == 'pi_scope_first_output' else good
    result = ModelResult(task_id=task.task_id, status='succeeded', output=output,
                         provider_id='openai', requested_model_id=pi.MODEL,
                         runtime_version='codex-cli ' + pi.VERSION,
                         input_fingerprint=task.fingerprint, result_hash=canonical_hash(output))
    if reason == 'pi_scope_result_validation':
        result = replace(result, result_hash='wrong')
    transport.run = AsyncMock(return_value=result)
    scope.read_source_async = AsyncMock(side_effect=(
        AcceptanceScopeError('PRIVATE_SENTINEL') if reason == 'pi_scope_source_before'
        else [None, AcceptanceScopeError('PRIVATE_SENTINEL')] if reason == 'pi_scope_source_after'
        else None
    ))
    actual = asyncio.run(pi._PiTransport(scope, transport).run(task))
    assert actual.error_code == 'PI_ACCEPTANCE_SCOPE_BLOCKED'
    failure = sanitized_failure(actual.failure)
    assert failure['summary'] == reason
    projected = instance._result(
        str(uuid4()), task, actual, next(iter(instance._runs.values())), task.payload['transcript']
    )
    assert projected['failure'] == {'stage': 'validation', 'kind': 'policy', 'summary': reason}
    assert 'PRIVATE_SENTINEL' not in json.dumps(failure)
    assert actual.output is None
    assert transport.run.await_count == (0 if reason in {'pi_scope_attempt_binding', 'pi_scope_source_before'} else 1)
    assert sanitized_failure({'stage': 'validation', 'kind': 'policy', 'summary': 'PRIVATE_SENTINEL'}) is None
