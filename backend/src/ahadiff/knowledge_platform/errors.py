"""Stable, deliberately non-secret domain errors for all adapters."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """An expected failure that may safely be returned at the API boundary."""

    def __init__(
        self, code: str, detail: str, retryable: bool = False, status_code: int = 400
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.status_code = status_code

    def as_dict(self, trace_id: str | None = None) -> dict[str, Any]:
        """Return the shared error envelope without internal exception details."""
        return {
            "error": {"code": self.code, "retryable": self.retryable, "detail": self.detail},
            "trace_id": trace_id,
        }


def invalid(detail: str) -> DomainError:
    """Build a consistent input-validation failure."""
    return DomainError("INVALID_INPUT", detail, status_code=422)
