"""Cross-module task orchestration, without direct database or Vault writes.

Only this trusted worker composes collection, generation, independent review and
commit. Every durable state change remains a DomainService operation. Model
credentials and user decision authority are deliberately not available here.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from .auth import Actor
from .errors import DomainError
from .model.diagnostics import sanitized_failure, sanitized_warning_codes

if TYPE_CHECKING:
    from .model.contracts import Classification


@dataclass(frozen=True)
class WorkflowSettings:
    """Deployment-owned configuration, never populated from submitted content."""

    owner_id: str
    worker_id: str = "knowledge-worker"
    allowed_provider_ids: tuple[str, ...] = ()
    input_classification: Classification = "personal"
    allowed_classifications: frozenset[str] = frozenset({"public", "personal"})
    allow_fixture: bool = False
    auto_commit_enabled: bool = False
    lease_seconds: int = 120
    poll_seconds: float = 1.0


class KnowledgeWorkflow:
    """Run persisted tasks and resume reviews without re-generating their patch."""

    kinds = ["intake.preview", "knowledge.generate", "knowledge.review", "knowledge.apply"]

    def __init__(
        self, domain: Any, runner: Any, settings: WorkflowSettings, *, intake_service: Any = None
    ) -> None:
        if not settings.owner_id or settings.lease_seconds < 15:
            raise ValueError("An owner and a lease of at least 15 seconds are required")
        self.domain, self.runner, self.settings = domain, runner, settings
        self.intake_service = intake_service
        self.last_maintenance_error: str | None = None
        self._next_cleanup = 0.0
        self.worker = Actor(
            settings.owner_id,
            settings.worker_id,
            "service",
            frozenset({"tasks:work", "knowledge:read", "knowledge:write"}),
            settings.allowed_classifications,
        )
        self.generator = Actor(
            settings.owner_id,
            settings.worker_id + ":generator",
            "agent",
            frozenset({"knowledge:read", "knowledge:write"}),
            settings.allowed_classifications,
        )
        self.reviewer = Actor(
            settings.owner_id,
            settings.worker_id + ":reviewer",
            "agent",
            frozenset({"knowledge:read", "knowledge:review"}),
            settings.allowed_classifications,
        )
        self.writer = Actor(
            settings.owner_id,
            settings.worker_id + ":writer",
            "service",
            frozenset({"knowledge:read", "knowledge:apply"}),
            settings.allowed_classifications,
        )

    def _update(self, task: dict[str, Any], patch: dict[str, Any], stage: str) -> None:
        self.domain.update_task(
            task["task_id"],
            {**patch, "lease_token": task["lease_token"]},
            actor=self.worker,
            idempotency_key=f"{task['task_id']}:{task['lease_token']}:{stage}",
            trace_id=task.get("trace_id"),
        )

    async def _heartbeat(self, task: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(self.settings.lease_seconds / 3)
            self.domain.renew_task_lease(
                task["task_id"],
                task["lease_token"],
                actor=self.worker,
                lease_seconds=self.settings.lease_seconds,
            )

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Poll eligible tasks; waits/auth/quota are not silently made runnable."""
        while not stop.is_set():
            if time.monotonic() >= self._next_cleanup:
                try:
                    await asyncio.to_thread(self.domain.resume_due_quota_tasks, actor=self.worker)
                    if self.intake_service is not None:
                        await asyncio.to_thread(
                            self.intake_service.cleanup_expired, actor=self.worker
                        )
                    self.last_maintenance_error = None
                except Exception:
                    self.last_maintenance_error = "INTAKE_CLEANUP_FAILED"
                self._next_cleanup = time.monotonic() + 60
            if not await self.run_once():
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.poll_seconds)

    async def run_once(self) -> bool:
        task = self.domain.claim_task(
            self.settings.worker_id,
            actor=self.worker,
            kinds=self.kinds,
            lease_seconds=self.settings.lease_seconds,
        )
        if task is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(task))
        try:
            operation = asyncio.create_task(self._execute(task))
            finished, _ = await asyncio.wait(
                {operation, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in finished:
                operation.cancel()
                with suppress(asyncio.CancelledError):
                    await operation
                # A lost lease must not be followed by an unowned terminal write.
                await heartbeat
            else:
                await operation
        except DomainError as exc:
            state = (
                "waiting_user"
                if exc.code == "REVIEW_REQUIRED"
                else "partial"
                if exc.code in {"SOURCE_INCOMPLETE", "BASE_STALE"}
                else "blocked"
            )
            with suppress(DomainError):
                self._update(task, {"state": state, "error": {"code": exc.code}}, "domain-error")
        except asyncio.CancelledError:
            # A lease/reconciliation, not a guessed rollback, handles interruption.
            raise
        except Exception:
            # Never persist arbitrary exception strings containing source text/tokens.
            with suppress(DomainError):
                self._update(
                    task,
                    {"state": "blocked", "error": {"code": "WORKFLOW_ERROR"}},
                    "unexpected-error",
                )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, DomainError):
                await heartbeat
        return True

    def _settle_model(self, task: dict[str, Any], response: dict[str, Any], stage: str) -> bool:
        status = response.get("status")
        states = {
            "waiting_auth": "waiting_auth",
            "waiting_quota": "waiting_quota",
            "blocked": "blocked",
            "invalid_output": "partial",
            "failed": "failed",
            "cancelled": "cancelled",
            "needs_evidence": "partial",
            "needs_user": "waiting_user",
            "rejected": "succeeded",
        }
        if status not in states:
            return False
        safe_result = {"stage": stage, "status": status, "change_id": response.get("change_id")}
        publication_block_reasons = {
            "DERIVATIVE_TEXT_BOUNDS", "DERIVATIVE_SENSITIVE_PATTERN",
            "DERIVATIVE_ASSET_OR_QUOTATION", "DERIVATIVE_UNVERIFIED_LINK",
            "DERIVATIVE_UNSAFE_CITATION", "DERIVATIVE_COPY_ALARM",
            "DERIVATIVE_REVIEW_INCOMPLETE", "PUBLICATION_BLOCKED",
        }
        reason = response.get("reason")
        if isinstance(reason, str) and reason in publication_block_reasons:
            safe_result["publication_block_reason"] = reason
        # These records are produced by our model adapter, not by model output.
        # Keep an explicit field allowlist so future adapter additions cannot
        # accidentally persist prompts, source text or credentials here.
        metadata_fields = {
            "task_id",
            "status",
            "provider_id",
            "model_id",
            "requested_model_id",
            "runtime_version",
            "input_fingerprint",
            "result_hash",
            "error_code",
            "retry_after",
            "fixture",
        }

        def audit(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict):
                return {}
            value = cast("dict[str, Any]", value)
            safe: dict[str, Any] = {
                key: item
                for key, item in cast("dict[str, Any]", value).items()
                if key in metadata_fields
                and isinstance(item, str | bool | int | float | type(None))
            }
            safe["warning_codes"] = sanitized_warning_codes(value.get("warning_codes"))
            failure = sanitized_failure(value.get("failure"))
            if failure is not None:
                safe["failure"] = failure
            return safe

        safe_result["model"] = audit(response.get("model"))
        if isinstance(response.get("model_attempts"), list):
            safe_result["model_attempts"] = [audit(item) for item in response["model_attempts"]]
        self._update(task, {"state": states[status], "result": safe_result}, f"{stage}:{status}")
        return True

    async def _execute(self, task: dict[str, Any]) -> None:
        if task["kind"] == "intake.preview":
            if self.intake_service is None:
                raise DomainError("ROUTE_UNAVAILABLE", "Intake worker is not configured")
            await self.intake_service.process_claimed(task, actor=self.worker)
            return
        payload = task.get("payload", {})
        checkpoint: dict[str, Any] = task.get("checkpoint") or {}
        change_id = payload.get("change_id") or checkpoint.get("change_id")
        if task["kind"] == "knowledge.generate" and not change_id:
            from .generation import GenerationService

            generator = GenerationService(
                self.domain,
                self.runner,
                allowed_provider_ids=self.settings.allowed_provider_ids,
                input_classification=self.settings.input_classification,
                allow_fixture=self.settings.allow_fixture,
            )
            response = await generator.generate(
                payload.get("evidence_refs", []),
                task_id=task["task_id"],
                workflow_task_id=task["task_id"],
                actor=self.generator,
                idempotency_key=task["task_id"] + ":propose",
                entry_id=payload.get("entry_id"),
                trace_id=task.get("trace_id"),
            )
            change_id = response.get("change_id")
            if change_id:
                self._update(task, {"checkpoint": {"change_id": change_id}}, "checkpoint-proposal")
            if self._settle_model(task, response, "generate"):
                return
            if not change_id:
                if response.get("status") == "no_change":
                    self._update(
                        task, {"state": "succeeded", "result": {"status": "no_change"}}, "no-change"
                    )
                    return
                raise DomainError("INVALID_OUTPUT", "Generation did not return a change")
        if not isinstance(change_id, str):
            raise DomainError("INVALID_INPUT", "An apply task must identify its change")
        change = self.domain.get_change(change_id, actor=self.worker)
        state = change.get("state")
        if state not in {"approved", "committed", "indexed"}:
            from .ai_review import AIReviewService

            reviewer = AIReviewService(
                self.domain,
                self.runner,
                allowed_provider_ids=self.settings.allowed_provider_ids,
                input_classification=self.settings.input_classification,
                allow_fixture=self.settings.allow_fixture,
            )
            response = await reviewer.review(
                change_id,
                task_id=task["task_id"],
                actor=self.reviewer,
                idempotency_key=task["task_id"] + ":review",
                trace_id=task.get("trace_id"),
            )
            if self._settle_model(task, response, "review"):
                return
        if not self.settings.auto_commit_enabled:
            self._update(
                task,
                {
                    "state": "blocked",
                    "result": {
                        "status": "ready_to_commit",
                        "change_id": change_id,
                        "reason": "AUTO_COMMIT_DISABLED",
                    },
                },
                "auto-commit-disabled",
            )
            return
        result = self.domain.apply_change(
            change_id,
            actor=self.writer,
            idempotency_key=change_id + ":apply",
            trace_id=task.get("trace_id"),
        )
        self._update(
            task,
            {
                "state": "succeeded",
                "result": {
                    "status": "committed",
                    "change_id": change_id,
                    "entry_id": result.get("entry_id"),
                    "commit_sha": result.get("commit_sha"),
                },
            },
            "committed",
        )
