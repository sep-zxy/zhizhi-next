"""Allowlisted model failure diagnostics, never excerpts of provider messages."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, cast

FAILURE_STAGES = frozenset(
    {"queue", "validation", "version", "authentication", "execution", "output", "cleanup"}
)
FAILURE_KINDS = frozenset(
    {"policy", "authentication", "quota", "transport", "provider", "protocol",
     "output", "process", "timeout", "host"}
)
FAILURE_SUMMARIES = frozenset(
    {
        "authentication_required",
        "subscription_quota_exhausted",
        "structured_output_schema_rejected",
        "requested_model_unavailable",
        "transport_connection_failed",
        "sandbox_initialization_failed",
        "unsupported_cli_configuration",
        "provider_reported_failure",
        "cli_notification_requires_review",
        "unexpected_tool_or_event",
        "invalid_protocol_response",
        "output_validation_failed",
        "process_exited_unsuccessfully",
        "runtime_version_mismatch",
        "host_process_unavailable",
        "deadline_exceeded",
        "policy_rejected",
    }
)
FAILURE_EVENTS = frozenset({"error", "turn.failed", "item.error"})


@dataclass(frozen=True, kw_only=True)
class ModelFailure:
    """Bounded diagnostic vocabulary safe for persistence and display.

    ``exit_code`` is the observed natural process result, not an exit produced
    by adapter cleanup. Unknown values remain None instead of being guessed.
    """

    stage: str
    kind: str
    summary: str
    event_type: str | None = None
    exit_code: int | None = None

    def __post_init__(self) -> None:
        if self.stage not in FAILURE_STAGES or self.kind not in FAILURE_KINDS:
            raise ValueError("Unknown model diagnostic stage or kind")
        if self.summary not in FAILURE_SUMMARIES:
            raise ValueError("Model diagnostics require an allowlisted summary")
        if self.event_type is not None and self.event_type not in FAILURE_EVENTS:
            raise ValueError("Unknown model diagnostic event")
        if self.exit_code is not None and (
            type(self.exit_code) is not int or not -(2**31) <= self.exit_code < 2**32
        ):
            raise ValueError("Invalid process exit code")


def sanitized_failure(value: Any) -> dict[str, Any] | None:
    """Drop unrecognized keys and reject untrusted diagnostic field values."""
    if isinstance(value, ModelFailure):
        return asdict(value)
    if not isinstance(value, dict):
        return None
    value = cast("dict[str, Any]", value)
    selected = {
        key: value[key]
        for key in ("stage", "kind", "summary", "event_type", "exit_code")
        if key in value
    }
    try:
        return asdict(ModelFailure(**selected))
    except (TypeError, ValueError):
        return None


def provider_failure_diagnostic(message: str, *, event_type: str) -> ModelFailure:
    """Classify in memory; return only fixed vocabulary, never provider text.

    This does not authorize a retry, new login, changed route or relaxed sandbox.
    Unknown errors deliberately stay unknown until separately verified.
    """
    lowered = message[:8192].lower()
    if any(word in lowered for word in ("429", "quota", "rate limit", "usage limit", "credits")):
        kind, summary = "quota", "subscription_quota_exhausted"
    elif any(
        word in lowered
        for word in ("401", "403", "unauthorized", "oauth", "credential", "api key")
    ):
        kind, summary = "authentication", "authentication_required"
    elif re.search(r"(invalid|unsupported|reject\w*)[^\n]{0,100}schema", lowered):
        kind, summary = "provider", "structured_output_schema_rejected"
    elif re.search(
        r"model[^\n]{0,100}(not found|not supported|unsupported|unavailable|does not exist)",
        lowered,
    ):
        kind, summary = "provider", "requested_model_unavailable"
    elif any(
        word in lowered
        for word in (
            "stream disconnected", "connection", "network", "dns",
            "tls", "certificate", "transport", "timed out",
        )
    ):
        kind, summary = "transport", "transport_connection_failed"
    elif "sandbox" in lowered and any(word in lowered for word in ("failed", "error", "unable")):
        kind, summary = "host", "sandbox_initialization_failed"
    elif any(
        word in lowered
        for word in ("unexpected argument", "unknown feature", "unknown variant", "invalid config")
    ):
        kind, summary = "host", "unsupported_cli_configuration"
    else:
        kind, summary = "provider", "provider_reported_failure"
    return ModelFailure(
        stage="execution", kind=kind, summary=summary, event_type=event_type
    )


def coded_failure_diagnostic(
    code: str, *, stage: str, exit_code: int | None = None
) -> ModelFailure:
    """Describe adapter-generated error codes using the same fixed vocabulary."""
    if code == "SUBSCRIPTION_AUTH":
        kind, summary = "authentication", "authentication_required"
    elif code == "SUBSCRIPTION_QUOTA":
        kind, summary = "quota", "subscription_quota_exhausted"
    elif code == "CODEX_PROCESS_FAILED":
        kind, summary = "process", "process_exited_unsuccessfully"
    elif code == "CODEX_VERSION_MISMATCH":
        kind, summary = "host", "runtime_version_mismatch"
    elif code in {"CODEX_EXECUTABLE_NOT_FOUND", "CODEX_HOST_INVALID"}:
        kind, summary = "host", "host_process_unavailable"
    elif code == "MODEL_DEADLINE_EXCEEDED":
        kind, summary = "timeout", "deadline_exceeded"
    elif code == "CODEX_NOTIFICATION_REQUIRES_REVIEW":
        kind, summary = "policy", "cli_notification_requires_review"
    elif code in {"UNEXPECTED_MODEL_TOOL", "CODEX_UNEXPECTED_EVENT"}:
        kind, summary = "policy", "unexpected_tool_or_event"
    elif code in {
        "OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_TOKEN_LIMIT",
        "CODEX_INCOMPLETE_TURN", "CODEX_AMBIGUOUS_OUTPUT",
    }:
        kind, summary = "output", "output_validation_failed"
    elif code in {
        "CODEX_INVALID_JSONL", "CODEX_INVALID_EVENT", "CODEX_EVENT_AFTER_COMPLETION",
        "CODEX_USAGE_MISSING", "CODEX_USAGE_INVALID",
    }:
        kind, summary = "protocol", "invalid_protocol_response"
    else:
        kind, summary = "policy", "policy_rejected"
    return ModelFailure(stage=stage, kind=kind, summary=summary, exit_code=exit_code)
