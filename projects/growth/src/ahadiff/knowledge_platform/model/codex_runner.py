"""Opt-in official Codex CLI transport using its existing ChatGPT login only.

The CLI, not this adapter, owns credential storage/refresh. No auth file is read
or copied. Exec JSONL does not attest the actual model: ``model_id`` stays None.
Tool-event rejection is detection, not rollback or an OS security boundary.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from ..portable import portable_child_environment
from ._codex_cleanup import cleanup_failure, cleanup_owned_workspace
from ._codex_job import CodexOwnedJob
from ._codex_notice_projection import (
    PROJECTION_CLI_VERSION,
    PROJECTION_TASK_FINGERPRINT,
    project_notice,
    projection_result,
)
from .contracts import ModelResult, ModelStatus, ModelTask, canonical_hash
from .diagnostics import (
    ModelFailure,
    coded_failure_diagnostic,
    notification_diagnostic,
    provider_failure_diagnostic,
    sanitized_failure,
)
from .pi_runner import SYSTEM_PROMPT, RpcFailure, SubscriptionRoute, classify_provider_error
from .validation import OUTPUT_ERRORS, contains_obvious_secret, parse_output, validate_schema

CODEX_VERSION = "0.163.0-alpha.11"
# Exact official-source review is recorded in deploy/windows-portable/
# codex-version-review.json. Never infer compatibility from a version prefix.
SUPPORTED_CODEX_VERSIONS = ("0.163.0-alpha.10.2", CODEX_VERSION)
# A string envelope supports our existing schemas (optional keys/local refs),
# without weakening them to fit the provider's strict structured-output subset.
ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {"result_json": {"type": "string"}},
    "required": ["result_json"],
    "additionalProperties": False,
}
DISABLED_FEATURES = (
    "shell_tool",
    "shell_snapshot",
    "unified_exec",
    "apps",
    "plugins",
    "hooks",
    "memories",
    "multi_agent",
    "skill_mcp_dependency_install",
    "view_image",
    "image_generation",
    "js_repl",
    "browser_use",
    "computer_use",
    "tool_suggest",
)

# Full upstream statements of a violated configured boundary, not unknown
# warning words. Provenance: CODEX_EVENT_ACCEPTANCE.md. Optional masking,
# metadata, deprecation, hook-trust hints and same-provider model reroutes do
# not establish a permissions violation by themselves.
_SANDBOX_DISABLED_NOTICE = (
    "Credential masking is inactive because sandboxing is disabled. Commands "
    "will receive unmasked credentials. Select a sandboxed permission profile "
    "to use credential masking."
)


def _known_notice_boundary_violation(message: str) -> bool:
    if message == _SANDBOX_DISABLED_NOTICE:
        return True
    match = re.fullmatch(
        r"in-process app-server event stream lagged; dropped ([1-9][0-9]{0,19}) events",
        message,
    )
    return match is not None and int(match[1]) <= 2**64 - 1


_MAX_NOTIFICATIONS = 16


class _CodexFailure(RpcFailure):
    """A provider/host failure with strictly allowlisted diagnostic metadata."""

    def __init__(
        self, code: str, status: ModelStatus = "failed", *, failure: ModelFailure
    ) -> None:
        super().__init__(code, status)
        self.failure = failure


@dataclass
class _ExecutionState:
    """Per-invocation stage; never shared between queued calls."""

    stage: str = "queue"


@dataclass(frozen=True, kw_only=True)
class CodexRunnerConfig:
    """Explicit deployment opt-in; never accepts keys, auth blobs or API URLs."""

    enabled: bool = False
    reuse_existing_login: bool = False
    routes: tuple[SubscriptionRoute, ...] = ()
    executable: tuple[str, ...] = ("codex",)
    expected_version: str = CODEX_VERSION
    codex_home: Path | None = None
    timeout_seconds: float = 120
    max_input_bytes: int = 512_000
    max_output_bytes: int = 1_048_576
    quota_backoff_seconds: int = 3600
    development_transport: bool = False

    def __post_init__(self) -> None:
        if not self.executable or any(
            not isinstance(arg, str) or not arg for arg in cast("tuple[Any, ...]", self.executable)
        ):
            raise ValueError("Executable must be a non-empty argv list")
        if self.expected_version not in SUPPORTED_CODEX_VERSIONS:
            raise ValueError("Only exact reviewed Codex CLI versions are supported")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 600:
            raise ValueError("Timeout must be finite and at most 600 seconds")
        if not 1024 <= self.max_input_bytes <= 2_000_000:
            raise ValueError("Invalid model input byte limit")
        if not 1024 <= self.max_output_bytes <= 8_000_000:
            raise ValueError("Invalid model output byte limit")
        if not 60 <= self.quota_backoff_seconds <= 86400:
            raise ValueError("Invalid quota backoff")
        if self.codex_home is not None and not self.codex_home.is_absolute():
            raise ValueError("Existing Codex home must be an explicit absolute path")
        if len(self.routes) > 1 or any(route.provider_id != "openai" for route in self.routes):
            raise ValueError("Codex accepts exactly one OpenAI subscription route, no fallback")
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", route.model_id)
            for route in self.routes
        ):
            raise ValueError("Codex requires an exact model identifier")


class _ExecEvents:
    """Conservative allowlist for the official exec JSONL protocol, not model claims."""

    def __init__(
        self, max_output_tokens: int, *, notification_details: bool = False,
        notice_projection: bool = False, cli_version: str | None = None,
    ) -> None:
        self.notification_details = notification_details
        self.notice_projection = notification_details and notice_projection
        self.notification: dict[str, Any] | None = None
        self.cli_version = cli_version
        self.notification_policy = "not_observed"
        self.provider_turn_state = "not_observed"
        self.stream_error: _CodexFailure | None = None
        self.turn_failure: _CodexFailure | None = None
        self.max_output_tokens = max_output_tokens
        self.started = False
        self.turn_started = False
        self.completed = False
        self.text: str | None = None
        self.usage: dict[str, Any] = {}
        self.event_counts: dict[str, int] = {}
        self.terminal_event: str | None = None

    def accept(self, raw: bytes) -> None:
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise RpcFailure("CODEX_INVALID_JSONL") from exc
        if not isinstance(event, dict):
            raise RpcFailure("CODEX_INVALID_EVENT")
        event = cast("dict[str, Any]", event)
        kind = event.get("type")
        if not isinstance(kind, str):
            raise RpcFailure("CODEX_INVALID_EVENT")
        known_events = {
            "thread.started", "turn.started", "turn.completed", "turn.failed", "error",
            "item.started", "item.updated", "item.completed",
        }
        label = kind if kind in known_events else "unknown"
        self.event_counts[label] = self.event_counts.get(label, 0) + 1
        if self.terminal_event is not None:
            raise RpcFailure("CODEX_EVENT_AFTER_COMPLETION")
        if kind in {"turn.completed", "turn.failed"}:
            self.terminal_event = kind
        if kind == "item.completed":
            notification = event.get("item")
            if (
                isinstance(notification, dict)
                and cast("dict[str, Any]", notification).get("type") == "error"
            ):
                # Official exec keeps running for these non-fatal items. Collect
                # the natural terminal result before deciding whether to release
                # output; startup warnings may precede thread.started.
                self.event_counts["item.error"] = self.event_counts.get("item.error", 0) + 1
                if self.event_counts["item.error"] > _MAX_NOTIFICATIONS:
                    raise RpcFailure("CODEX_NOTIFICATION_LIMIT", "blocked")
                message = cast("dict[str, Any]", notification).get("message")
                if not isinstance(message, str):
                    raise RpcFailure("CODEX_INVALID_EVENT")
                if _known_notice_boundary_violation(message):
                    self.notification_policy = "boundary_violation"
                elif self.notification_policy == "not_observed":
                    self.notification_policy = "nonfatal"
                if self.notification_details and self.notification is None:
                    self.notification = notification_diagnostic(
                        cast("dict[str, Any]", notification).get("message")
                    )
                    if self.notice_projection:
                        try:
                            projection = project_notice(
                                cast("dict[str, Any]", notification).get("message")
                            )
                        except Exception:
                            # No exception text/traceback, and never replace the
                            # protocol/acceptance facts with projector failure.
                            projection = projection_result(evidence="projection_failed")
                        self.notification["projection"] = projection
                return
        if kind == "thread.started" and not self.started:
            self.started = True
        elif kind == "turn.started" and self.started and not self.turn_started:
            self.turn_started = True
            self.provider_turn_state = "in_progress"
        elif kind in {"item.started", "item.updated", "item.completed"} and self.turn_started:
            item = event.get("item")
            if not isinstance(item, dict):
                raise RpcFailure("CODEX_INVALID_EVENT")
            item = cast("dict[str, Any]", item)
            item_type = item.get("type")
            if item_type not in {"agent_message", "reasoning"}:
                # Events can be emitted after a tool starts: this cannot undo side effects.
                raise RpcFailure("UNEXPECTED_MODEL_TOOL", "blocked")
            if item_type == "agent_message" and kind == "item.completed":
                if not isinstance(item.get("text"), str) or self.text is not None:
                    raise RpcFailure("CODEX_AMBIGUOUS_OUTPUT", "invalid_output")
                self.text = item["text"]
        elif kind == "turn.completed" and self.turn_started:
            usage = event.get("usage")
            if not isinstance(usage, dict):
                raise RpcFailure("CODEX_USAGE_MISSING")
            usage = cast("dict[str, Any]", usage)
            for source, target in (
                ("input_tokens", "input"),
                ("output_tokens", "output"),
                ("cached_input_tokens", "cacheRead"),
                ("reasoning_output_tokens", "reasoning"),
            ):
                value = usage.get(source)
                if value is not None:
                    if type(value) is not int or not 0 <= value <= 1e15:
                        raise RpcFailure("CODEX_USAGE_INVALID")
                    self.usage[target] = value
            if "output" not in self.usage:
                raise RpcFailure("CODEX_USAGE_MISSING")
            if self.usage["output"] > self.max_output_tokens:
                raise RpcFailure("MODEL_OUTPUT_TOKEN_LIMIT", "invalid_output")
            self.completed = True
            self.provider_turn_state = "completed"
        elif kind == "error" or (kind == "turn.failed" and self.turn_started):
            if self.event_counts.get("error", 0) > _MAX_NOTIFICATIONS:
                raise RpcFailure("CODEX_NOTIFICATION_LIMIT", "blocked")
            error = event if kind == "error" else event.get("error")
            if not isinstance(error, dict):
                raise RpcFailure("CODEX_INVALID_EVENT")
            message = cast("dict[str, Any]", error).get("message")
            if not isinstance(message, str):
                raise RpcFailure("CODEX_INVALID_EVENT")
            # Classify only in memory. No classification authorizes stopping the
            # stream, retrying, or interpreting a recovered turn as auth failure.
            status, code = classify_provider_error(message)
            failure = _CodexFailure(
                code, status, failure=provider_failure_diagnostic(message, event_type=kind),
            )
            if kind == "turn.failed":
                self.provider_turn_state = "failed"
                self.turn_failure = failure
            elif self.stream_error is None:
                self.stream_error = failure
        else:
            raise RpcFailure("CODEX_UNEXPECTED_EVENT", "blocked")

    def finish(self, exit_code: int) -> None:
        """Natural EOF/exit precedes acceptance; never release quarantined text."""
        if self.provider_turn_state not in {"completed", "failed"}:
            self.provider_turn_state = "incomplete"
        failure = self.turn_failure or (self.stream_error if exit_code else None)
        if failure is not None:
            failure.failure = replace(failure.failure, exit_code=exit_code)
            raise failure
        if exit_code:
            raise _CodexFailure(
                "CODEX_PROCESS_FAILED", failure=coded_failure_diagnostic(
                    "CODEX_PROCESS_FAILED", stage="execution", exit_code=exit_code,
                ),
            )
        if not self.completed:
            raise RpcFailure("CODEX_INCOMPLETE_TURN", "invalid_output")
        # The pinned CLI emits retryable errors as top-level error without
        # willRetry, then completed/exit 0 on recovery. Successful final facts
        # win over intermediate text; failed/invalid/nonzero paths above do not.
        if self.notification_policy == "boundary_violation":
            raise _CodexFailure(
                "CODEX_RUNTIME_BOUNDARY_VIOLATION", "blocked",
                failure=ModelFailure(
                    stage="execution", kind="policy",
                    summary="runtime_boundary_violation", event_type="item.error",
                    exit_code=exit_code,
                ),
            )


async def _stop(process: asyncio.subprocess.Process, job: CodexOwnedJob | None = None) -> None:
    """Bound cleanup even when the main CLI has exited but descendants remain."""
    if job is not None:
        job.close()
    elif os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.returncode is None:
        # The only unassigned Windows process is a helper still waiting on its gate.
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    if process.stdin is not None:
        process.stdin.close()

    async def discard(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            while await stream.read(4096):
                pass

    # wait() alone can return immediately after the parent exits while pipe
    # transports still await EOF from descendants. Drain after killing the group.
    await asyncio.wait_for(
        asyncio.gather(process.wait(), discard(process.stdout), discard(process.stderr)), 5
    )


class CodexModelRunner:
    """Serial, bounded, schema-checked CLI execution; Pi remains the default adapter."""

    def __init__(
        self, config: CodexRunnerConfig, *, collect_process_diagnostics: bool = False,
        collect_notification_diagnostics: bool = False,
        collect_notice_projection: bool = False,
    ) -> None:
        self.config = config
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[str, ModelResult]] = {}
        self._quota_until: datetime | None = None
        self._collect_process_diagnostics = collect_process_diagnostics
        self._collect_notification_diagnostics = (
            collect_process_diagnostics and collect_notification_diagnostics
        )
        self._collect_notice_projection = collect_notice_projection
        self.process_diagnostics: list[dict[str, Any]] = []
        self.cleanup_diagnostics: list[dict[str, Any]] = []
        self._cleanup_pending = False
        self.preflight_states = {"version": "not_checked", "login": "not_checked"}

    def _record_cleanup(self, report: dict[str, Any]) -> None:
        if len(self.cleanup_diagnostics) < 32:
            self.cleanup_diagnostics.append(report)
        if report["status"] != "completed":
            self._cleanup_pending = True

    @contextlib.asynccontextmanager
    async def _workspace(self, prefix: str) -> AsyncGenerator[Path, None]:
        """No finalizer, forced permission reset, or cleanup of earlier invocations."""
        import tempfile

        root = Path(tempfile.mkdtemp(prefix=prefix))
        primary: BaseException | None = None
        report: dict[str, Any]
        try:
            yield root
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                if self._cleanup_pending:
                    report = {
                        "resource": "temporary_directory", "status": "failed", "attempts": 0,
                        "errors": [], "recovery": "owned_process_cleanup_requires_review",
                    }
                else:
                    report = await cleanup_owned_workspace(root)
            except BaseException as exc:
                report = {
                    "resource": "temporary_directory", "status": "failed", "attempts": 1,
                    "errors": [cleanup_failure(exc)],
                    "recovery": "owned_workspace_retained_for_review",
                }
                self._record_cleanup(report)
                if primary is None:
                    raise
            else:
                self._record_cleanup(report)
                if report["status"] != "completed" and primary is None:
                    raise _CodexFailure(
                        "CODEX_CLEANUP_FAILED", "blocked",
                        failure=coded_failure_diagnostic("CODEX_CLEANUP_FAILED", stage="cleanup"),
                    )

    def _environment(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "COMSPEC",
            "PATHEXT",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "CODEX_HOME",
        }
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(portable_child_environment())
        # Deliberate opt-in exception: only the official CLI accesses the existing
        # auth home, which may be outside the portable root. Never inspect/copy it.
        if self.config.codex_home is not None:
            env["CODEX_HOME"] = str(self.config.codex_home)
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", DO_NOT_TRACK="1")
        return env

    async def _process(
        self,
        args: list[str],
        cwd: Path,
        *,
        payload: bytes = b"",
        events: _ExecEvents | None = None,
        phase: str = "execution",
    ) -> tuple[int, str]:
        if phase not in {"version", "authentication", "execution"}:
            raise ValueError("Unknown process diagnostic phase")
        started = time.monotonic()
        observation: dict[str, Any] = {
            "phase": phase,
            "steps": [],
            "elapsed_ms": 0,
            "exit_code": None,
            "failure_type": None,
            "errno": None,
            "winerror": None,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "event_counts": {},
            "terminal_event": None,
            "provider_turn_state": "not_observed",
            "notification_policy": "not_observed",
            "stream_error_observed": False,
        }
        if self._collect_process_diagnostics and len(self.process_diagnostics) < 32:
            self.process_diagnostics.append(observation)

        def step(name: str) -> None:
            observation["steps"].append(
                {"name": name, "elapsed_ms": round((time.monotonic() - started) * 1000)}
            )

        def failure(exc: BaseException) -> None:
            # Deliberately never serialize str(exc), repr(exc), filenames or argv.
            if isinstance(exc, OSError):
                observation["failure_type"] = "os_error"
                observation["errno"] = exc.errno if type(exc.errno) is int else None
                winerror = getattr(exc, "winerror", None)
                observation["winerror"] = winerror if type(winerror) is int else None
            elif isinstance(exc, asyncio.CancelledError):
                observation["failure_type"] = "cancelled"
            elif isinstance(exc, RpcFailure):
                observation["failure_type"] = "adapter_error"
            elif isinstance(exc, UnicodeError):
                observation["failure_type"] = "encoding_error"
            else:
                observation["failure_type"] = "host_error"
            observation["elapsed_ms"] = round((time.monotonic() - started) * 1000)

        async def finish_process(
            primary: BaseException | None, *, record_steps: bool = True,
            readers: tuple[asyncio.Task[None], ...] = (),
        ) -> None:
            report: dict[str, Any] = {
                "resource": "process", "phase": phase, "status": "pending", "attempts": 1,
                "errors": [], "recovery": "none",
            }
            observation["cleanup"] = report
            first_cleanup_error: BaseException | None = None

            def failed(exc: BaseException, operation: str) -> None:
                nonlocal first_cleanup_error
                if first_cleanup_error is None:
                    first_cleanup_error = exc
                report["errors"].append({"operation": operation, **cleanup_failure(exc)})
                report.update(status="failed", recovery="owned_process_cleanup_requires_review")
                # Latch before the next await: another cancellation must never
                # make workspace deletion or a queued model request eligible.
                self._cleanup_pending = True

            try:
                if record_steps:
                    step("cleanup")
                setup_cleanup = getattr(primary, "codex_cleanup_error", None)
                if isinstance(setup_cleanup, OSError):
                    failed(setup_cleanup, "job_setup_handle_close")
                if readers:
                    try:
                        for reader in readers:
                            reader.cancel()
                        async with asyncio.timeout(5):
                            await asyncio.gather(*readers, return_exceptions=True)
                    except BaseException as exc:
                        failed(exc, "reader_join")
                # Even a cancelled/failed reader join must attempt owned stop.
                # A second cancellation is separately retained, not permission
                # to delete a workspace with unconfirmed process ownership.
                try:
                    await _stop(process, job)
                except BaseException as exc:
                    failed(exc, "owned_stop")
                if first_cleanup_error is None:
                    report["status"] = "completed"
                    if record_steps:
                        step("cleanup_complete")
                elif primary is None:
                    if isinstance(first_cleanup_error, asyncio.CancelledError):
                        raise first_cleanup_error
                    raise _CodexFailure(
                        "CODEX_CLEANUP_FAILED", "blocked",
                        failure=coded_failure_diagnostic("CODEX_CLEANUP_FAILED", stage="cleanup"),
                    ) from None
            finally:
                self._record_cleanup(report)

        limit = self.config.max_output_bytes if events is not None else 16384
        command = [*self.config.executable, *args]
        job = None
        if os.name == "nt":
            # Raw one-byte read must not buffer the model prompt before CLI inherits stdin.
            gate = (
                "import os,subprocess,sys; "
                "sys.exit(subprocess.call(sys.argv[1:]) if os.read(0,1)==b'\\0' else 2)"
            )
            command = [sys.executable, "-B", "-s", "-c", gate, *command]
        try:
            step("process_spawn")
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                env=self._environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=limit + 1,
                start_new_session=os.name == "posix",
            )
            step("helper_started" if os.name == "nt" else "process_started")
        except BaseException as exc:
            failure(exc)
            raise
        try:
            if os.name == "nt":
                step("job_create")
                job = CodexOwnedJob()
                step("job_created")
                step("job_assign")
                job.assign(process.pid)
                step("job_assigned")
        except BaseException as exc:
            failure(exc)
            await finish_process(exc, record_steps=False)
            raise
        total = 0
        captured = bytearray()

        async def drain(stream: asyncio.StreamReader, *, stdout: bool) -> None:
            nonlocal total
            while True:
                try:
                    chunk = (
                        await stream.readline() if stdout and events else await stream.read(4096)
                    )
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise RpcFailure("CODEX_OUTPUT_TOO_LARGE", "blocked") from exc
                if not chunk:
                    break
                total += len(chunk)
                observation["stdout_bytes" if stdout else "stderr_bytes"] += len(chunk)
                if total > limit:
                    raise RpcFailure("CODEX_OUTPUT_TOO_LARGE", "blocked")
                if stdout and events:
                    events.accept(chunk)
                elif events is None:
                    captured.extend(chunk)
                # Exec stderr is discarded, not persisted or sent back to a model.

        async def feed() -> None:
            assert process.stdin is not None
            process.stdin.write((b"\0" if os.name == "nt" else b"") + payload)
            await process.stdin.drain()
            step("stdin_released")
            process.stdin.close()

        assert process.stdout is not None and process.stderr is not None
        jobs = [
            asyncio.create_task(drain(process.stdout, stdout=True)),
            asyncio.create_task(drain(process.stderr, stdout=False)),
            asyncio.create_task(feed()),
        ]
        primary: BaseException | None = None
        try:
            await asyncio.gather(*jobs)
            code = await process.wait()
            observation["exit_code"] = code
            step("process_exited")
            if events is not None:
                events.finish(code)
            return code, captured.decode("utf-8", errors="strict").strip()
        except BaseException as exc:
            primary = exc
            if events is not None and events.provider_turn_state not in {"completed", "failed"}:
                events.provider_turn_state = (
                    "interrupted" if isinstance(exc, asyncio.CancelledError) else "incomplete"
                )
            failure(exc)
            # Cleanup may kill a still-running CLI. That synthetic exit must not
            # be reported as the provider's actual exit code.
            if process.returncode is not None:
                observation["exit_code"] = process.returncode
                if isinstance(exc, _CodexFailure):
                    exc.failure = replace(exc.failure, exit_code=process.returncode)
            raise
        finally:
            try:
                await finish_process(primary, readers=tuple(jobs))
            finally:
                observation["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                if events is not None:
                    observation["event_counts"] = dict(events.event_counts)
                    observation["terminal_event"] = events.terminal_event
                    observation["provider_turn_state"] = events.provider_turn_state
                    observation["notification_policy"] = events.notification_policy
                    observation["stream_error_observed"] = events.stream_error is not None
                    if events.notification is not None:
                        observation["notification"] = dict(events.notification)

    async def run(self, task: ModelTask) -> ModelResult:
        route = self.config.routes[0] if self.config.routes else None

        def outcome(status: ModelStatus, code: str | None = None, **fields: Any) -> ModelResult:
            return ModelResult(
                task_id=task.task_id,
                status=status,
                error_code=code,
                provider_id="openai" if route else None,
                requested_model_id=route.model_id if route else None,
                model_id=None,
                runtime_version="codex-cli " + self.config.expected_version,
                input_fingerprint=task.fingerprint,
                fixture=self.config.development_transport,
                **fields,
            )

        if not self.config.enabled:
            return outcome("blocked", "MODEL_PROCESSING_DISABLED")
        if not self.config.reuse_existing_login:
            return outcome("blocked", "CODEX_EXISTING_LOGIN_NOT_APPROVED")
        if self._cleanup_pending:
            return outcome(
                "blocked", "CODEX_CLEANUP_PENDING",
                failure=coded_failure_diagnostic("CODEX_CLEANUP_PENDING", stage="cleanup"),
            )
        if self._collect_notice_projection and (
            not self._collect_notification_diagnostics
            or self.config.expected_version != PROJECTION_CLI_VERSION
            or route is None or route.model_id != "gpt-6.1-sol"
            or task.fingerprint != PROJECTION_TASK_FINGERPRINT
            or task.task_id != "codex-cli-public-arithmetic-smoke"
            or task.dedupe_key != "codex-cli-public-arithmetic-smoke-v1"
            or task.deadline is not None or task.trace_id != ""
        ):
            return outcome(
                "blocked", "CODEX_NOTICE_PROJECTION_SCOPE_INVALID",
                failure=coded_failure_diagnostic(
                    "CODEX_NOTICE_PROJECTION_SCOPE_INVALID", stage="validation"
                ),
            )
        if (
            route is None
            or not route.account_qualified
            or "openai" not in task.allowed_provider_ids
            or task.input_classification not in route.allowed_classifications
        ):
            return outcome("blocked", "NO_APPROVED_SUBSCRIPTION_ROUTE")
        contents = {
            "instructions": task.instructions,
            "schema": task.output_schema,
            "payload": task.payload,
            "input_refs": task.input_refs,
        }
        if task.input_classification == "secret" or contains_obvious_secret(contents):
            return outcome("blocked", "SECRET_INPUT_FORBIDDEN")
        try:
            validate_schema(task.output_schema)
        except OUTPUT_ERRORS:
            return outcome("blocked", "REQUEST_SCHEMA_INVALID")
        prompt = json.dumps(
            {
                "system_instructions": SYSTEM_PROMPT,
                "response_format": "Return result_json containing exactly one JSON object matching "
                "output_schema. The outer object has only result_json. Do not use tools.",
                "task_id": task.task_id,
                "purpose": task.purpose,
                "application_instructions": task.instructions,
                "output_schema": task.output_schema,
                "untrusted_payload": task.payload,
                "input_refs": task.input_refs,
                "max_output_tokens": task.max_output_tokens,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(prompt) > self.config.max_input_bytes:
            return outcome("blocked", "MODEL_INPUT_TOO_LARGE")
        timeout = self.config.timeout_seconds
        if task.deadline is not None:
            timeout = min(timeout, (task.deadline - datetime.now(UTC)).total_seconds())
        if timeout <= 0:
            return outcome("cancelled", "MODEL_DEADLINE_EXCEEDED")
        execution = _ExecutionState()
        try:
            async with asyncio.timeout(timeout):
                async with self._lock:
                    if self._cleanup_pending:
                        return outcome(
                            "blocked", "CODEX_CLEANUP_PENDING",
                            failure=coded_failure_diagnostic(
                                "CODEX_CLEANUP_PENDING", stage="cleanup"
                            ),
                        )
                    if self._quota_until and self._quota_until > datetime.now(UTC):
                        return outcome(
                            "waiting_quota", "SUBSCRIPTION_QUOTA", retry_after=self._quota_until
                        )
                    cached = self._cache.get(task.dedupe_key)
                    if cached:
                        if cached[0] != task.fingerprint:
                            return outcome("blocked", "MODEL_IDEMPOTENCY_CONFLICT")
                        return replace(cached[1], task_id=task.task_id)
                    try:
                        output, usage, warning_codes = await self._execute(task, prompt, execution)
                    except RpcFailure as exc:
                        if exc.status == "waiting_quota":
                            self._quota_until = datetime.now(UTC) + timedelta(
                                seconds=self.config.quota_backoff_seconds
                            )
                        return outcome(
                            cast("ModelStatus", exc.status),
                            exc.code,
                            failure=(
                                exc.failure if isinstance(exc, _CodexFailure)
                                else coded_failure_diagnostic(exc.code, stage=execution.stage)
                            ),
                            retry_after=(
                                self._quota_until if exc.status == "waiting_quota" else None
                            ),
                        )
                    result = outcome(
                        "succeeded", output=output, usage=usage, result_hash=canonical_hash(output),
                        warning_codes=warning_codes,
                    )
                    if len(self._cache) >= 256:
                        self._cache.pop(next(iter(self._cache)))
                    self._cache[task.dedupe_key] = (task.fingerprint, result)
                    return result
        except TimeoutError:
            return outcome(
                "cancelled", "MODEL_DEADLINE_EXCEEDED",
                failure=coded_failure_diagnostic(
                    "MODEL_DEADLINE_EXCEEDED", stage=execution.stage
                ),
            )
        except FileNotFoundError:
            return outcome(
                "blocked", "CODEX_EXECUTABLE_NOT_FOUND",
                failure=coded_failure_diagnostic(
                    "CODEX_EXECUTABLE_NOT_FOUND", stage=execution.stage
                ),
            )
        except (OSError, UnicodeError, ValueError):
            return outcome(
                "blocked", "CODEX_HOST_INVALID",
                failure=coded_failure_diagnostic("CODEX_HOST_INVALID", stage=execution.stage),
            )

    async def _preflight(self, cwd: Path, execution: _ExecutionState) -> None:
        """Use the identical version/login/owned-process path for run and diagnostics."""
        self.preflight_states = {"version": "not_checked", "login": "not_checked"}
        execution.stage = "version"
        code, version = await self._process(["--version"], cwd, phase="version")
        if code or version != "codex-cli " + self.config.expected_version:
            self.preflight_states["version"] = "mismatch"
            raise _CodexFailure(
                "CODEX_VERSION_MISMATCH", "blocked",
                failure=coded_failure_diagnostic(
                    "CODEX_VERSION_MISMATCH", stage=execution.stage, exit_code=code
                ),
            )
        self.preflight_states["version"] = "verified"
        execution.stage = "authentication"
        code, auth = await self._process(["login", "status"], cwd, phase="authentication")
        if "api key" in auth.lower() or "api_key" in auth.lower():
            self.preflight_states["login"] = "api_key_rejected"
            raise RpcFailure("METERED_AUTH_FORBIDDEN", "blocked")
        if code or auth != "Logged in using ChatGPT":
            self.preflight_states["login"] = "not_authenticated" if code else "unrecognized"
            raise _CodexFailure(
                "SUBSCRIPTION_AUTH", "waiting_auth",
                failure=coded_failure_diagnostic(
                    "SUBSCRIPTION_AUTH", stage=execution.stage, exit_code=code
                ),
            )
        self.preflight_states["login"] = "chatgpt"

    async def inspect_existing_runtime(self) -> dict[str, Any]:
        """Check only existing CLI version/login through the owned process boundary.

        No exec/model call is made. Success is host_preflight_passed, never a
        ModelResult succeeded state or a model/platform E2E assertion.
        """
        execution = _ExecutionState(stage="validation")
        code = "CODEX_HOST_INVALID"
        status: str = "blocked"
        try:
            if self._cleanup_pending:
                code = "CODEX_CLEANUP_PENDING"
                execution.stage = "cleanup"
            elif not self.config.enabled or not self.config.reuse_existing_login:
                code = "CODEX_EXISTING_LOGIN_NOT_APPROVED"
            else:
                async with asyncio.timeout(self.config.timeout_seconds):
                    async with self._lock:
                        if self._cleanup_pending:
                            execution.stage = "cleanup"
                            raise RpcFailure("CODEX_CLEANUP_PENDING", "blocked")
                        async with self._workspace("zhizhi-codex-probe-") as temporary:
                            await self._preflight(temporary, execution)
                return {"status": "host_preflight_passed", "failure": None}
        except RpcFailure as exc:
            return {
                "status": exc.status, "error_code": exc.code,
                "failure": sanitized_failure(
                    exc.failure if isinstance(exc, _CodexFailure)
                    else coded_failure_diagnostic(exc.code, stage=execution.stage)
                ),
            }
        except TimeoutError:
            code, status = "MODEL_DEADLINE_EXCEEDED", "cancelled"
        except (OSError, UnicodeError, ValueError):
            pass
        return {
            "status": status, "error_code": code,
            "failure": sanitized_failure(coded_failure_diagnostic(code, stage=execution.stage)),
        }

    async def _execute(
        self, task: ModelTask, prompt: bytes, execution: _ExecutionState
    ) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
        async with self._workspace("zhizhi-codex-") as cwd:
            await self._preflight(cwd, execution)
            schema = cwd / "output-schema.json"
            schema.write_text(json.dumps(ENVELOPE_SCHEMA), encoding="utf-8")
            args = [
                "exec",
                "--ignore-user-config",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--json",
                "-C",
                str(cwd),
                "--model",
                self.config.routes[0].model_id,
                "--output-schema",
                str(schema),
            ]
            for feature in DISABLED_FEATURES:
                args.extend(["--disable", feature])
            overrides: dict[str, Any] = {
                "forced_login_method": "chatgpt",
                "model_provider": "openai",
                "approval_policy": "never",
                "project_doc_max_bytes": 0,
                "skills.max_context_tokens": 1,
                "web_search": "disabled",
                "history.persistence": "none",
                "log_dir": str(cwd / "logs"),
                "sqlite_home": str(cwd / "state"),
                "tools.update_plan.enabled": False,
                "tools.experimental_request_user_input.enabled": False,
                "notify": [],
                "developer_instructions": SYSTEM_PROMPT,
            }
            for key, value in overrides.items():
                args.extend(["-c", key + "=" + json.dumps(value)])
            args.append("-")
            events = _ExecEvents(
                task.max_output_tokens,
                notification_details=self._collect_notification_diagnostics,
                notice_projection=self._collect_notice_projection,
                cli_version=self.config.expected_version,
            )
            execution.stage = "execution"
            code, _ = await self._process(args, cwd, payload=prompt, events=events)
            if code:
                raise _CodexFailure(
                    "CODEX_PROCESS_FAILED",
                    failure=coded_failure_diagnostic(
                        "CODEX_PROCESS_FAILED", stage=execution.stage, exit_code=code
                    ),
                )
            if not events.completed or events.text is None:
                raise RpcFailure("CODEX_INCOMPLETE_TURN", "invalid_output")
            execution.stage = "output"
            try:
                envelope = parse_output(events.text, ENVELOPE_SCHEMA)
                output = parse_output(envelope["result_json"], task.output_schema)
                if contains_obvious_secret(output):
                    raise ValueError("Secret-like model output")
            except OUTPUT_ERRORS:
                raise RpcFailure("OUTPUT_SCHEMA_INVALID", "invalid_output") from None
            warnings: list[str] = []
            if events.event_counts.get("item.error"):
                warnings.append("codex_nonfatal_notification")
            if events.stream_error is not None:
                warnings.append("codex_recovered_stream_error")
            return output, events.usage, tuple(warnings)
