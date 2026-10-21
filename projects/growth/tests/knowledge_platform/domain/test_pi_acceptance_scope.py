"""Inert identities and synthetic transport only, never an operational activation."""

from __future__ import annotations

import asyncio
import copy
import hashlib
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
    result = PiBridge(
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
        authorize=scope.authorize,
        reserve_attempt=scope.reserve_model,
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
