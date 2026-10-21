"""JSON boundary contracts and canonical fingerprints; no implicit authority."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from .errors import DomainError, invalid

JSONDict = dict[str, Any]
TASK_STATES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "partial",
        "waiting_user",
        "waiting_auth",
        "waiting_quota",
        "retry_wait",
        "blocked",
        "failed",
        "cancelled",
        "waiting_review",
        "waiting_input",
    }
)
TASK_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
MAJOR_RISKS = frozenset(
    {"core_conclusion", "important_merge_delete", "personal_position", "privacy_exposure"}
)
IDENTITY_FIELDS = frozenset(
    {
        "owner_id",
        "actor_id",
        "actor",
        "actor_kind",
        "actor_type",
        "user_approved",
        "approved_by",
        "user_confirmed",
        "allowed_classifications",
        "scopes",
    }
)


class EvidenceRef(TypedDict, total=False):
    """A reference to retained source or authenticated learning evidence."""

    source_id: str
    source_revision: int
    learning_outcome_id: str
    content_hash: str
    locator: dict[str, Any]
    claim_ids: list[str]


def now() -> str:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    """Create an opaque stable identifier independent of a title or path."""
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical(value: Any) -> str:
    """Serialize JSON deterministically, rejecting NaN and arbitrary objects."""
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise invalid("Payload must contain finite JSON values only") from exc


def fingerprint(value: Any) -> str:
    """Hash a canonical JSON value for immutable evidence and idempotency."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text_hash(value: str) -> str:
    """Hash exact UTF-8 content, without normalizing meaningful whitespace."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_payload(payload: JSONDict, *, forbid_identity: bool = True) -> JSONDict:
    """Copy and validate a JSON object without accepting self-asserted identity."""
    if not isinstance(payload, dict):
        raise invalid("Expected a JSON object")
    if forbid_identity and IDENTITY_FIELDS.intersection(payload):
        raise DomainError(
            "FORBIDDEN",
            "Identity and approval fields must come from authenticated context",
            status_code=403,
        )
    return json.loads(canonical(payload))


def identifier(value: Any, label: str = "identifier") -> str:
    """Validate opaque IDs so they are safe even when used as Git path fragments."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise invalid(f"Invalid {label}")
    return value


def required_text(payload: JSONDict, field: str, *, limit: int = 1_000_000) -> str:
    """Require a nonempty bounded string."""
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise invalid(f"{field} must be a nonempty string of at most {limit} characters")
    return value
