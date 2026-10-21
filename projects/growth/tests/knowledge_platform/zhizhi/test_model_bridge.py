"""Unified model tests are explicit fixtures and make no provider calls."""

from types import SimpleNamespace

import pytest

from ahadiff.core.errors import ProviderError, SafetyError
from ahadiff.knowledge_platform.learning.model_bridge import (
    ModelBridgeConfiguration,
    configure_model_bridge,
    unified_provider_config,
)
from ahadiff.llm.provider import make_provider
from ahadiff.llm.schemas import ProviderRequest


class RecordingRunner:
    def __init__(self, status="succeeded"):
        self.status = status
        self.tasks = []

    async def run(self, task):
        self.tasks.append(task)
        return SimpleNamespace(
            status=self.status,
            output={"text": "settled answer"} if self.status == "succeeded" else None,
            error_code=self.status,
            model_id="fixture-model",
            provider_id="fixture",
            task_id=task.task_id,
            usage={},
            fixture=True,
            result_hash="fixture-result",
        )


@pytest.fixture(autouse=True)
def reset_bridge():
    configure_model_bridge(None, ModelBridgeConfiguration())
    yield
    configure_model_bridge(None, ModelBridgeConfiguration())


def request(privacy_mode="redacted_remote"):
    return ProviderRequest(
        prompt_name="growth_card_chat",
        prompt_fingerprint="hash",
        prompt_version="1",
        eval_bundle_version="1",
        model="old-model",
        payload_text="Explain public recursion",
        diff_content="",
        source_ref="concept-one",
        privacy_mode=privacy_mode,
    )


def test_old_provider_factory_uses_runner_without_http(monkeypatch):
    runner = RecordingRunner()
    configure_model_bridge(
        runner,
        ModelBridgeConfiguration(
            enabled=True, allowed_provider_ids=("fixture",), input_classification="public"
        ),
    )
    monkeypatch.setattr(
        "httpx.Client", lambda *args, **kwargs: pytest.fail("legacy HTTP adapter was instantiated")
    )
    chunks = []
    with make_provider(unified_provider_config()) as provider:
        result = provider.generate(request(), on_text_delta=chunks.append)
    assert result.content == "settled answer"
    assert chunks == ["settled answer"]
    assert runner.tasks[0].allowed_provider_ids == ("fixture",)
    assert "settled_output_not_token_stream" in result.notes


@pytest.mark.parametrize(
    "status", ["waiting_auth", "waiting_quota", "blocked", "invalid_output", "failed"]
)
def test_waiting_states_never_use_metered_fallback(status, monkeypatch):
    configure_model_bridge(RecordingRunner(status), ModelBridgeConfiguration(enabled=True))
    monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: pytest.fail("metered fallback"))
    with (
        make_provider(unified_provider_config()) as provider,
        pytest.raises(ProviderError, match=status),
    ):
        provider.generate(request())


def test_strict_local_is_not_weakened_by_unified_mode():
    runner = RecordingRunner()
    configure_model_bridge(runner, ModelBridgeConfiguration(enabled=True))
    with make_provider(unified_provider_config()) as provider, pytest.raises(SafetyError):
        provider.generate(request("strict_local"))
    assert runner.tasks == []


def test_missing_runner_fails_closed():
    configure_model_bridge(None, ModelBridgeConfiguration(enabled=True))
    with (
        make_provider(unified_provider_config()) as provider,
        pytest.raises(ProviderError, match="blocked"),
    ):
        provider.generate(request())
