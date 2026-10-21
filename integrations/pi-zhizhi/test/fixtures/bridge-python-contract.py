"""Explicit cross-language fixture. No production activation, Codex, identity or network."""
import asyncio
import sys

from ahadiff.knowledge_platform.model.contracts import ModelResult, canonical_hash
from ahadiff.knowledge_platform.model.pi_bridge import (
    MODEL, BridgeLimits, PiBridge, PiBridgeConfig,
)
from ahadiff.knowledge_platform.model.pi_bridge_stdio import serve


class SyntheticRunner:
    calls = 0

    async def run(self, task):
        self.calls += 1
        assert self.calls <= 2
        if self.calls == 1:
            output = {"type": "tool_call", "name": "zhizhi_search", "arguments": {"query": "synthetic", "limit": 5}}
        else:
            receipt = task.payload["transcript"][-1]["result"]
            assert receipt["data_classification"] == "public"
            assert receipt["classification_labels"] == ["public"]
            assert receipt["data"] == [{"entry_id": "entry_fixture", "revision": 1}]
            output = {"type": "text", "text": "PYTHON_JSONL_SYNTHETIC_COMPLETE"}
        return ModelResult(
            task_id=task.task_id, status="succeeded", output=output,
            provider_id="openai", requested_model_id=MODEL, fixture=True,
            input_fingerprint=task.fingerprint, result_hash=canonical_hash(output),
        )


config = PiBridgeConfig(
    owner_id="synthetic-owner", device_id="synthetic-device", deployment_id="synthetic-deployment",
    allowed_tools=("zhizhi_search",), input_classification="public", allowed_classifications=("public",),
    enabled=True, allow_fixture=True, limits=BridgeLimits(model_calls_per_run=2, tool_calls_per_run=1),
)
bridge = PiBridge(SyntheticRunner(), config, authorize=lambda request: True, reserve_attempt=lambda task: True)
asyncio.run(serve(bridge, sys.stdin.buffer, sys.stdout))
