"""One exact engineering task, one durable attempt; not a general model grant.

Receipts are integrity records, not an OS boundary against the same Windows
account. Reserve before execution: cancellation, crash and invalid output never
refund the attempt. No raw prompt, response or credential is written here.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .acceptance_model import ScopeReceipts
from .model.contracts import ModelResult, ModelRunner, ModelTask, canonical_hash

POLICY = "owner-engineering-one-shot-v1"


class EngineeringScopeError(ValueError):
    """Fixed, non-sensitive scope failure code."""


class EngineeringScopeRunner:
    """Reject every other task, including automatic repair and human retry."""

    def __init__(self, runner: ModelRunner, receipts: ScopeReceipts,
                 scope: dict[str, Any], revalidate: Callable[[], dict[str, Any]]):
        self.runner = runner
        self.receipts = receipts
        self.scope = scope
        self.revalidate = revalidate

    async def run(self, task: ModelTask) -> ModelResult:
        """Consume the sole persistent attempt before delegating, never refund."""
        def blocked(code: str) -> ModelResult:
            return ModelResult(task_id=task.task_id, status="blocked", error_code=code,
                               input_fingerprint=task.fingerprint)
        try:
            if (self.scope.get("policy") != POLICY
                    or task.schema_name != "growth_opportunities"
                    or task.purpose != "generate"
                    or task.fingerprint != self.scope.get("task_fingerprint")
                    or canonical_hash(self.revalidate()) != canonical_hash(self.scope)):
                return blocked("ENGINEERING_SCOPE_MISMATCH")
            if self.receipts.read("activation") != self.scope:
                return blocked("ENGINEERING_ACTIVATION_REQUIRED")
            # Identical attempted requests also return False, not a cached success.
            if not self.receipts.put("attempt", {
                "scope_hash": canonical_hash(self.scope),
                "task_fingerprint": task.fingerprint, "status": "reserved_consumed",
                "automatic_retry": False,
            }):
                return blocked("ENGINEERING_ATTEMPT_CONSUMED")
        except Exception:
            return blocked("ENGINEERING_SCOPE_CHECK_FAILED")
        try:
            result = await self.runner.run(task)
            # Only a real runner result may become a live engineering opportunity.
            if result.fixture or (result.status == "succeeded" and (
                result.provider_id != "openai" or result.input_fingerprint != task.fingerprint
            )):
                result = blocked("ENGINEERING_RESULT_NOT_ATTESTED")
            self.receipts.put("outcome", {
                "status": result.status, "fixture": result.fixture,
                "task_fingerprint": task.fingerprint,
                "result_hash": result.result_hash,
            })
            return result
        except BaseException:
            # Includes cancellation and process interruption. The reservation is
            # already durable; an absent outcome is intentionally not retryable.
            raise
