"""Allowlisted model failure diagnostics, never excerpts of provider messages."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, cast

from ._codex_notice_templates import EXACT_NOTIFICATION_TEMPLATES

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
        "cli_stream_error_requires_review",
        "runtime_boundary_violation",
        "unexpected_tool_or_event",
        "invalid_protocol_response",
        "output_validation_failed",
        "process_exited_unsuccessfully",
        "runtime_version_mismatch",
        "host_process_unavailable",
        "host_cleanup_incomplete",
        "deadline_exceeded",
        "policy_rejected",
        "pi_scope_attempt_binding",
        "pi_scope_source_before",
        "pi_scope_source_after",
        "pi_scope_result_validation",
        "pi_scope_first_output",
        "pi_scope_second_output",

    }
)
FAILURE_EVENTS = frozenset({"error", "turn.failed", "item.error"})


WARNING_CODES = frozenset({"codex_nonfatal_notification", "codex_recovered_stream_error"})


def sanitized_warning_codes(value: Any) -> list[str]:
    """Only fixed adapter-owned summaries; no upstream text or dynamic codes."""
    if not isinstance(value, tuple | list):
        return []
    items = cast("tuple[Any, ...] | list[Any]", value)
    if len(items) > 16:
        return []
    return sorted({item for item in items if isinstance(item, str) and item in WARNING_CODES})


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
    elif code in {"CODEX_CLEANUP_FAILED", "CODEX_CLEANUP_PENDING"}:
        kind, summary = "host", "host_cleanup_incomplete"
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


def notification_diagnostic(message: Any) -> dict[str, Any]:
    """Fixed-vocabulary hint for an exec error item, never text or an allow rule.

    alpha.11 erases ConfigWarning/Warning/DeprecationNotice/ModelRerouted kinds
    when mapping them into ErrorItem.message. Pattern hints cannot recover the
    actual upstream type and MUST NOT determine result acceptance or rejection.
    """
    present = isinstance(message, str)
    raw = message.encode("utf-8", errors="replace") if present else b""
    lowered = raw[:8192].decode("utf-8", errors="ignore").lower()
    code = "unknown_notification"
    if lowered.startswith("model rerouted:"):
        code = "model_reroute_notice"
    elif lowered.startswith("credential masking is inactive because sandboxing is disabled."):
        code = "credential_masking_sandbox_disabled"
    elif lowered.startswith(
        "credential masking is inactive because the network proxy is disabled."
    ):
        code = "credential_masking_proxy_disabled"
    elif lowered.startswith("credential masking is inactive because shell environment overrides"):
        code = "credential_masking_environment_conflict"
    elif (lowered.startswith("configured value for ")
          and "is disallowed by requirements; falling back" in lowered):
        code = "required_configuration_override"
    elif lowered.startswith("under-development features enabled:"):
        code = "unstable_features_notice"
    elif lowered.startswith("in-process app-server event stream lagged; dropped "):
        code = "event_stream_loss_notice"
    elif lowered.startswith("`--dangerously-bypass-hook-trust` is enabled."):
        code = "hook_trust_bypass_notice"
    elif lowered.startswith("invalid global instructions"):
        code = "invalid_global_instructions_notice"
    elif " is deprecated" in lowered:
        code = "deprecation_text_notice"
    evidence = "bounded_text_pattern_hint"
    # Exact original complete text only. Never trim, fold case, interpolate
    # dynamic values, or match a truncated prefix to a reviewed public template.
    if present and len(raw) <= 8192 and message in EXACT_NOTIFICATION_TEMPLATES:
        code = EXACT_NOTIFICATION_TEMPLATES[message]
        evidence = "exact_public_template_text"
    return {
        "classifier_version": 2,
        "rule_id": "codex_exec_notice_v2:" + code,
        "code": code,
        "evidence": evidence,
        "upstream_kind": "not_preserved_by_exec_jsonl",
        "message_present": present,
        "input_utf8_bytes": len(raw),
        "inspected_utf8_bytes": min(len(raw), 8192),
        "truncated": len(raw) > 8192,
    }
