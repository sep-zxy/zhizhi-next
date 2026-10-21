"""Typed, JSON-compatible contracts for untrusted inbound material.

An accepted receipt is not consent to retain or publish its contents. Extracted
text is evidence, never an instruction to an agent or permission to call tools.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

Capability = Literal["supported", "partial", "unsupported", "unverified"]
ExtractionState = Literal[
    "extracted", "partial", "needs_input", "needs_auth", "rate_limited", "unsupported"
]


class IntakeError(Exception):
    """A safe, structured failure that never contains credentials or response bodies."""

    def __init__(
        self, code: str, detail: str, *, status_code: int = 400, retryable: bool = False
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class NormalizedShare:
    """One user-submitted item; source provenance survives URL normalization."""

    original_text: str
    original_url: str | None
    url: str | None
    platform: str
    content_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a serializable value for the domain ledger."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MediaReference:
    """Untrusted remote media reference, not proof the media has been downloaded."""

    url: str
    kind: Literal["image", "video", "audio", "captions"]
    alt: str = ""


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Parser output; description and full body/transcript are deliberately separate."""

    source_url: str
    platform: str
    parser: str
    parser_version: str
    state: ExtractionState
    title: str = ""
    author: str = ""
    body_text: str = ""
    description: str = ""
    body_complete: bool = False
    media: tuple[MediaReference, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible extraction evidence without inventing missing text."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Report individual features and whether live verification has occurred."""

    adapter: str
    platform: str
    resolve_url: Capability = "partial"
    metadata: Capability = "partial"
    body_text: Capability = "partial"
    images: Capability = "partial"
    video: Capability = "partial"
    captions: Capability = "unsupported"
    requires_auth: Capability = "unverified"
    last_verified_at: str | None = None
    verification: str = "not_live_verified"

    def to_dict(self) -> dict[str, Any]:
        """Expose honest capability information for deployment health checks."""
        return asdict(self)


class Extractor(Protocol):
    """Pure response parser; outbound I/O is owned by the guarded fetch layer."""

    capabilities: AdapterCapabilities

    def extract(
        self, share: NormalizedShare, *, url: str, content: bytes, content_type: str
    ) -> ExtractedDocument:
        """Convert a bounded response into explicit, attributable evidence."""
        ...
