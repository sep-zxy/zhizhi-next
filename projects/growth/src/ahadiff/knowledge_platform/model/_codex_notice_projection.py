"""Opt-in, constant-only projection; never a notification permission decision.

Reviewed producers are pinned in deploy/windows-portable/codex-notice-projection-review.json.
No redacted provider text, dynamic slots, hashes, word sets or slot lengths leave
this module. A template shape is evidence of a match, NOT authenticated origin.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ._codex_notice_templates import EXACT_NOTIFICATION_TEMPLATES, OFFICIAL_NOTICE_COMMIT

PROJECTION_CLI_VERSION = "0.163.0-alpha.11"
# Bound to diagnostic_probe.fixed_public_smoke_task(); regression checks drift.
PROJECTION_TASK_FINGERPRINT = "84876861b54346f1487a90314acfedc2ceb0180745186eca4f3ab370ac16749a"
MAX_NOTICE_BYTES = 8192

_EXACT_FAMILIES = {
    "model_metadata_missing_fixed_model": "model_metadata",
    "service_tier_priority_unsupported_fixed_model": "configuration",
    "code_mode_unsupported_fixed_model": "code_mode",
    "stop_hook_empty_continuation": "hooks",
    "compaction_accuracy_notice": "context",
    "guardian_thread_context_deprecation": "feature_deprecation",
    "transcript_v2_deprecation": "feature_deprecation",
}
_CONFIG_PREFIX = "Codex is ignoring "
_LEGACY_PREFIX = "Ignoring deprecated managed config file at "
_LEGACY_SUFFIX = (
    "; CODEX_HOME/managed_config.toml is no longer supported on Windows. "
    "Use %ProgramData%\\OpenAI\\Codex\\requirements.toml for enforced settings "
    "or config.toml for defaults."
)
_ANCHORS = (
    (_CONFIG_PREFIX, "configuration"),
    (_LEGACY_PREFIX, "configuration"),
    ("Falling back from WebSockets to HTTPS transport. ", "transport"),
    (
        "Failed to save the conversation transcript; Codex will continue retrying. Error: ",
        "persistence",
    ),
    ("Code Mode is unavailable because ", "code_mode"),
    ("in-process app-server event stream lagged; dropped ", "event_stream"),
)
_HINTS = {
    "windows.sandbox_private_desktop": (
        " Remove windows.sandbox_private_desktop; legacy Windows sandboxes always "
        "use a private desktop."
    ),
    "network_proxy": (
        " Use [permissions.<name>.network] for network settings, or "
        "[experimental_network] in requirements.toml for enforced network policy."
    ),
    "allowed_permissions": (
        " Use [allowed_permission_profiles] and default_permissions; the old "
        "allowlist is ignored even when both forms are present."
    ),
}
_IMAGE_HINT = " Use [features].view_image to configure the image tool."
_SOURCE_PREFIXES = (
    "packaged defaults (",
    "MDM (",
    "system (",
    "enterprise-managed (",
    "user (",
    "project (",
    "legacy managed_config.toml (",
    "requirements (",
)
FAMILIES = frozenset(
    {
        "unknown",
        "ambiguous",
        "model_metadata",
        "configuration",
        "code_mode",
        "hooks",
        "context",
        "feature_deprecation",
        "transport",
        "persistence",
        "event_stream",
    }
)
TEMPLATE_IDS = frozenset(_EXACT_FAMILIES) | {
    "ignored_configuration_fields",
    "windows_legacy_managed_config",
    "in_process_event_stream_loss",
}
REASON_IDS = frozenset(
    {
        "unrecognized_configuration",
        "unsupported_legacy_config_location",
        "notification_events_dropped",
    }
)
EVIDENCE_IDS = frozenset(
    {
        "none",
        "exact_public_template_text",
        "official_template_shape_match",
        "family_hint",
        "projection_failed",
    }
)


def projection_result(
    *,
    family: str = "unknown",
    template_id: str | None = None,
    reason_id: str | None = None,
    evidence: str = "none",
) -> dict[str, Any]:
    """Closed output vocabulary, even if a future caller passes an unsafe value."""
    return {
        "projection_version": 1,
        "source_commit": OFFICIAL_NOTICE_COMMIT,
        "template_id": template_id if template_id in TEMPLATE_IDS else None,
        "reason_id": reason_id if reason_id in REASON_IDS else None,
        "family": family if family in FAMILIES else "unknown",
        "evidence": evidence if evidence in EVIDENCE_IDS else "none",
        "upstream_kind": "not_preserved_by_exec_jsonl",
    }


def _opaque(value: str) -> bool:
    # Bounds are private validation, never emitted. No escaping/normalization.
    return (
        bool(value)
        and len(value.encode("utf-8")) <= 1024
        and not any(
            character in "`\r\n" or unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def _config_source(value: str) -> bool:
    if value == "session-flags":
        return True
    for prefix in _SOURCE_PREFIXES:
        if not value.startswith(prefix) or not value.endswith(")"):
            continue
        slot = value[len(prefix) : -1]
        if not _opaque(slot):
            return False
        # Preserve fixed separators from format_config_layer_source, rather
        # than silently treating an entire source description as arbitrary.
        if prefix == "MDM (":
            return ":" in slot
        if prefix == "enterprise-managed (":
            return ", " in slot
        if prefix == "project (":
            return slot.endswith("/config.toml")
        return True
    return False


def _ignored_configuration(message: str) -> bool:
    lines = message.split("\n")
    head = re.fullmatch(
        r"Codex is ignoring ([1-9][0-9]{0,3}) unrecognized configuration "
        r"(setting|settings)\. Check for typos or deprecated settings\.",
        lines[0],
    )
    if head is None:
        return False
    count = int(head[1])
    if head[2] != ("setting" if count == 1 else "settings"):
        return False
    if len(lines) != 1 + min(count, 3) + int(count > 3):
        return False
    if count > 3 and lines[-1] != f"  ... and {count - 3} more ignored settings.":
        return False
    seen: set[str] = set()
    for line in lines[1 : 1 + min(count, 3)]:
        if not line.startswith("  ") or line.count(": `") != 1:
            return False
        source, tail = line[2:].split(": `", 1)
        if not _config_source(source) or tail.count("` is ignored.") != 1:
            return False
        key, hint = tail.split("` is ignored.", 1)
        if not _opaque(key) or line in seen:
            return False
        seen.add(line)
        parts = key.split(".")
        image_key = parts in (["include_view_image_tool"], ["features", "include_view_image_tool"])
        image_key = image_key or (
            len(parts) in {3, 4}
            and parts[0] == "profiles"
            and parts[2:] in (["include_view_image_tool"], ["features", "include_view_image_tool"])
        )
        if hint != (_IMAGE_HINT if image_key else _HINTS.get(key, "")):
            return False
    return True


def _legacy_windows(message: str) -> bool:
    if not message.startswith(_LEGACY_PREFIX) or not message.endswith(_LEGACY_SUFFIX):
        return False
    path = message[len(_LEGACY_PREFIX) : -len(_LEGACY_SUFFIX)]
    # Full bounded Windows path-shaped slot, not a filesystem read.
    return (
        _opaque(path)
        and path.endswith("\\managed_config.toml")
        and re.match(r"[A-Za-z]:\\", path) is not None
        and not any(character in '<>"|?*' for character in path)
    )


def project_notice(message: object) -> dict[str, Any]:
    """Pure, bounded projection. All output strings come from reviewed constants."""
    if not isinstance(message, str) or len(message) > MAX_NOTICE_BYTES:
        return projection_result()
    try:
        encoded = message.encode("utf-8", errors="strict")
    except UnicodeError:
        return projection_result()
    if (
        not encoded
        or len(encoded) > MAX_NOTICE_BYTES
        or any(
            character != "\n" and unicodedata.category(character).startswith("C")
            for character in message
        )
    ):
        return projection_result()
    exact_id = EXACT_NOTIFICATION_TEMPLATES.get(message)
    if exact_id is not None:
        return projection_result(
            family=_EXACT_FAMILIES[exact_id],
            template_id=exact_id,
            evidence="exact_public_template_text",
        )
    # Nested/concatenated recognized notices cannot claim one producer, including
    # a public anchor hidden in an opaque dynamic slot. Never return an ID set.
    anchors = tuple(anchor for anchor, _ in _ANCHORS) + tuple(EXACT_NOTIFICATION_TEMPLATES)
    if any(message.find(anchor, 1) >= 0 for anchor in anchors):
        return projection_result(family="ambiguous")
    if _ignored_configuration(message):
        return projection_result(
            family="configuration",
            template_id="ignored_configuration_fields",
            reason_id="unrecognized_configuration",
            evidence="official_template_shape_match",
        )
    if _legacy_windows(message):
        return projection_result(
            family="configuration",
            template_id="windows_legacy_managed_config",
            reason_id="unsupported_legacy_config_location",
            evidence="official_template_shape_match",
        )
    if re.fullmatch(
        r"in-process app-server event stream lagged; dropped [1-9][0-9]{0,8} events", message
    ):
        return projection_result(
            family="event_stream",
            template_id="in_process_event_stream_loss",
            reason_id="notification_events_dropped",
            evidence="official_template_shape_match",
        )
    # Arbitrary error tails do NOT qualify as full grammars. A single leading
    # long public phrase is only a family hint, never a root-cause assertion.
    for anchor, family in _ANCHORS:
        if message.startswith(anchor):
            return projection_result(family=family, evidence="family_hint")
    return projection_result()
