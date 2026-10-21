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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ..portable import portable_child_environment
from ._codex_job import CodexOwnedJob
from .contracts import ModelResult, ModelStatus, ModelTask, canonical_hash
from .pi_runner import SYSTEM_PROMPT, RpcFailure, SubscriptionRoute, classify_provider_error
from .validation import OUTPUT_ERRORS, contains_obvious_secret, parse_output, validate_schema

CODEX_VERSION = "0.163.0-alpha.10.2"
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
            not isinstance(arg, str) or not arg for arg in self.executable
        ):
            raise ValueError("Executable must be a non-empty argv list")
        if self.expected_version != CODEX_VERSION:
            raise ValueError("Only the reviewed Codex CLI version is supported")
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

    def __init__(self, max_output_tokens: int) -> None:
        self.max_output_tokens = max_output_tokens
        self.started = False
        self.turn_started = False
        self.completed = False
        self.text: str | None = None
        self.usage: dict[str, Any] = {}

    def accept(self, raw: bytes) -> None:
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise RpcFailure("CODEX_INVALID_JSONL") from exc
        if not isinstance(event, dict):
            raise RpcFailure("CODEX_INVALID_EVENT")
        kind = event.get("type")
        if self.completed:
            raise RpcFailure("CODEX_EVENT_AFTER_COMPLETION")
        if kind == "thread.started" and not self.started:
            self.started = True
        elif kind == "turn.started" and self.started and not self.turn_started:
            self.turn_started = True
        elif kind in {"item.started", "item.updated", "item.completed"} and self.turn_started:
            item = event.get("item")
            if not isinstance(item, dict):
                raise RpcFailure("CODEX_INVALID_EVENT")
            item_type = item.get("type")
            if item_type == "error":
                status, code = classify_provider_error(str(item.get("message", "")))
                raise RpcFailure(code, status)
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
        elif kind in {"turn.failed", "error"}:
            error = event.get("error", event)
            message = error.get("message", "") if isinstance(error, dict) else ""
            status, code = classify_provider_error(str(message))
            raise RpcFailure(code, status)
        else:
            raise RpcFailure("CODEX_UNEXPECTED_EVENT", "blocked")


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

    def __init__(self, config: CodexRunnerConfig) -> None:
        self.config = config
        self._lock = asyncio.Lock()
        self._cache: dict[str, tuple[str, ModelResult]] = {}
        self._quota_until: datetime | None = None

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
    ) -> tuple[int, str]:
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
        try:
            if os.name == "nt":
                job = CodexOwnedJob()
                job.assign(process.pid)
        except BaseException:
            await _stop(process, job)
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
            process.stdin.close()

        assert process.stdout is not None and process.stderr is not None
        jobs = [
            asyncio.create_task(drain(process.stdout, stdout=True)),
            asyncio.create_task(drain(process.stderr, stdout=False)),
            asyncio.create_task(feed()),
        ]
        try:
            await asyncio.gather(*jobs)
            code = await process.wait()
            return code, captured.decode("utf-8", errors="strict").strip()
        finally:
            for reader in jobs:
                reader.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await _stop(process, job)

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
        try:
            async with asyncio.timeout(timeout):
                async with self._lock:
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
                        output, usage = await self._execute(task, prompt)
                    except RpcFailure as exc:
                        if exc.status == "waiting_quota":
                            self._quota_until = datetime.now(UTC) + timedelta(
                                seconds=self.config.quota_backoff_seconds
                            )
                        return outcome(
                            cast("ModelStatus", exc.status),
                            exc.code,
                            retry_after=(
                                self._quota_until if exc.status == "waiting_quota" else None
                            ),
                        )
                    result = outcome(
                        "succeeded", output=output, usage=usage, result_hash=canonical_hash(output)
                    )
                    if len(self._cache) >= 256:
                        self._cache.pop(next(iter(self._cache)))
                    self._cache[task.dedupe_key] = (task.fingerprint, result)
                    return result
        except TimeoutError:
            return outcome("cancelled", "MODEL_DEADLINE_EXCEEDED")
        except FileNotFoundError:
            return outcome("blocked", "CODEX_EXECUTABLE_NOT_FOUND")
        except (OSError, UnicodeError, ValueError):
            return outcome("blocked", "CODEX_HOST_INVALID")

    async def _execute(
        self, task: ModelTask, prompt: bytes
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="zhizhi-codex-") as temporary:
            cwd = Path(temporary)
            code, version = await self._process(["--version"], cwd)
            if code or version != "codex-cli " + self.config.expected_version:
                raise RpcFailure("CODEX_VERSION_MISMATCH", "blocked")
            code, auth = await self._process(["login", "status"], cwd)
            if "api key" in auth.lower() or "api_key" in auth.lower():
                raise RpcFailure("METERED_AUTH_FORBIDDEN", "blocked")
            if code or auth != "Logged in using ChatGPT":
                raise RpcFailure("SUBSCRIPTION_AUTH", "waiting_auth")
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
            for key, value in {
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
            }.items():
                args.extend(["-c", key + "=" + json.dumps(value)])
            args.append("-")
            events = _ExecEvents(task.max_output_tokens)
            code, _ = await self._process(args, cwd, payload=prompt, events=events)
            if code:
                raise RpcFailure("CODEX_PROCESS_FAILED")
            if not events.completed or events.text is None:
                raise RpcFailure("CODEX_INCOMPLETE_TURN", "invalid_output")
            try:
                envelope = parse_output(events.text, ENVELOPE_SCHEMA)
                output = parse_output(envelope["result_json"], task.output_schema)
                if contains_obvious_secret(output):
                    raise ValueError("Secret-like model output")
            except OUTPUT_ERRORS:
                raise RpcFailure("OUTPUT_SCHEMA_INVALID", "invalid_output") from None
            return output, events.usage
