"""Versioned model boundary shared by intake, legacy Zhizhi and review workers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from .diagnostics import ModelFailure

ModelPurpose = Literal["preview", "generate", "review", "answer"]
Classification = Literal["public", "personal", "third_party_private", "employer", "secret"]
ModelStatus = Literal[
    "succeeded", "waiting_auth", "waiting_quota", "blocked", "invalid_output", "failed", "cancelled"
]


def canonical_hash(value: Any) -> str:
    """Hash JSON deterministically without writing payloads to logs."""
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class ModelTask:
    """Application-owned request; payload text never grants tool or routing permissions."""

    task_id: str
    purpose: ModelPurpose
    input_refs: tuple[str, ...]
    input_classification: Classification
    allowed_provider_ids: tuple[str, ...]
    schema_name: str
    output_schema: dict[str, Any]
    payload: dict[str, Any]
    dedupe_key: str
    max_output_tokens: int = 4096
    deadline: datetime | None = None
    trace_id: str = ""
    instructions: str = ""

    def __post_init__(self) -> None:
        if not self.task_id or not self.dedupe_key or not self.schema_name:
            raise ValueError("Task ID, dedupe key and schema name must be non-empty")
        if self.purpose not in {"preview", "generate", "review", "answer"}:
            raise ValueError("Unsupported model purpose")
        if self.input_classification not in {
            "public",
            "personal",
            "third_party_private",
            "employer",
            "secret",
        }:
            raise ValueError("Unsupported input classification")
        if not 1 <= self.max_output_tokens <= 32768:
            raise ValueError("Output token bound must be between 1 and 32768")
        if self.deadline is not None and self.deadline.tzinfo is None:
            raise ValueError("Deadline must be timezone-aware")
        canonical_hash(self.payload)
        canonical_hash(self.output_schema)

    @property
    def fingerprint(self) -> str:
        """Hash all semantic inputs, including policy and schema, but not execution IDs."""
        return canonical_hash(
            {
                "purpose": self.purpose,
                "refs": self.input_refs,
                "classification": self.input_classification,
                "providers": self.allowed_provider_ids,
                "schema_name": self.schema_name,
                "schema": self.output_schema,
                "payload": self.payload,
                "instructions": self.instructions,
                "max_output_tokens": self.max_output_tokens,
            }
        )


@dataclass(frozen=True, kw_only=True)
class ModelResult:
    """Explicit outcome; waiting, fixture and invalid responses are not real E2E passes."""

    task_id: str
    status: ModelStatus
    output: dict[str, Any] | None = None
    provider_id: str | None = None
    model_id: str | None = None
    requested_model_id: str | None = None
    runtime_version: str = "1.3.0"
    input_fingerprint: str | None = None
    result_hash: str | None = None
    usage: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    error_code: str | None = None
    failure: ModelFailure | None = None
    retry_after: datetime | None = None
    fixture: bool = False
    warning_codes: tuple[str, ...] = ()


class ModelRunner(Protocol):
    """Only supported model execution entry point in unified mode."""

    async def run(self, task: ModelTask) -> ModelResult:
        """Return a validated result or an explicit non-success state."""
        ...
