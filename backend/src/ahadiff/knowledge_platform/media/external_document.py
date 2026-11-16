"""A narrow anonymous-body manifest, never a privacy or publication approval.

Only the intake worker constructs this value from fetched bytes. The private
share envelope is deliberately not an input. DomainService independently checks
the exact body/hash against retained RAW before creating its document broker.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from .fetch import FetchResponse, SafeFetcher

if TYPE_CHECKING:
    from ..intake.contracts import ExtractedDocument

_RESTRICTED_PATH = re.compile(
    r"(?:^|[/_.;-])(?:auth|login|signin|sign-in|password|passwd|passcode|token|"
    r"secret|private|session|invite|reset|preview|account)(?:$|[/_.;=-])",
    re.IGNORECASE,
)
_OPAQUE_PATH = re.compile(r"^[A-Za-z0-9_-]{32,}$")
_RESTRICTED_ROBOTS = {"none", "noai", "noimageai", "noindex", "noarchive", "nosnippet"}


def _plain_provenance_url(url: str) -> bool:
    """Fail closed on query capabilities and recognizable private/opaque paths.

    This is a syntactic filter, not proof a URL/body contains no sensitive content.
    The independent derivative reviewer must still assess the entire candidate.
    """
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        return False
    path = unquote(unquote(parts.path))
    if _RESTRICTED_PATH.search(path):
        return False
    return not any(
        _OPAQUE_PATH.fullmatch(segment)
        and any(c.isalpha() for c in segment)
        and any(c.isdigit() for c in segment)
        for segment in path.split("/")
    )


class _RestrictionSignals(HTMLParser):
    """Observe explicit page restrictions without interpreting source commands."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.restricted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "").lower() for key, value in attrs}
        if tag == "input" and values.get("type") == "password":
            self.restricted = True
        if tag == "meta":
            key = values.get("name") or values.get("property") or values.get("itemprop")
            content = values.get("content", "")
            if key in {"robots", "googlebot", "bingbot"}:
                self.restricted |= bool(set(re.split(r"[\s,:;]+", content)) & _RESTRICTED_ROBOTS)
            if key == "isaccessibleforfree" and content == "false":
                self.restricted = True


def external_document_manifest(
    response: FetchResponse,
    document: ExtractedDocument,
    *,
    fetcher: SafeFetcher,
    envelope_labels: frozenset[str],
) -> dict[str, Any] | None:
    """Separate a complete anonymously fetched body, retaining unknown semantics.

    No title, author, description, media, account, user note or original share URL is
    copied. Empty restriction observations are not an assertion of copyright rights,
    non-personal content, technical subject matter or eligibility to publish.
    """
    basis = document.metadata.get("text_basis")
    if (
        not isinstance(fetcher, SafeFetcher)
        or not envelope_labels
        or envelope_labels - {"public", "personal"}
        or not 200 <= response.status_code < 300
        or document.state != "extracted"
        or not document.body_complete
        or not document.body_text.strip()
        or len(document.body_text) > 100_000
        or "\ufffd" in document.body_text
        or basis not in {"article_body", "post_body"}
        or document.warnings
    ):
        return None
    urls = (*response.redirects, response.url, document.source_url)
    if not all(_plain_provenance_url(url) for url in urls):
        return None
    headers = {key.lower(): value.lower() for key, value in response.headers.items()}
    if (
        "www-authenticate" in headers
        or "private" in headers.get("cache-control", "")
        or set(re.split(r"[\s,:;]+", headers.get("x-robots-tag", ""))) & _RESTRICTED_ROBOTS
    ):
        return None
    page = response.content.decode("utf-8-sig", errors="replace")
    # Explicit JSON-LD paywall markers are facts even if articleBody is present.
    if re.search(r'"isAccessibleForFree"\s*:\s*(?:false|"false")', page, re.IGNORECASE):
        return None
    if response.content_type == "text/html":
        signals = _RestrictionSignals()
        signals.feed(page)
        if signals.restricted:
            return None
    return {
        "body_text": document.body_text,
        "content_hash": hashlib.sha256(document.body_text.encode("utf-8")).hexdigest(),
        "canonical_url": document.source_url,
        "final_url": response.url,
        "captured_at": datetime.now(UTC).isoformat(),
        "fetcher_version": "anonymous-safe-fetch-v1",
        "extractor_version": f"{document.parser}:{document.parser_version}",
        "text_basis": basis,
        "authenticated": False,
        "publicly_accessible": True,
        "context_separated": True,
        "content_kind": "external_article" if basis == "article_body" else "external_post",
        "privacy_assessment": "not_assessed",
        "observed_restrictions": [],
    }
