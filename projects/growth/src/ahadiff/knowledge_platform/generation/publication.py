"""Conservative original-summary checks, never copyright permission or a legal safe harbor."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

_MARKDOWN = MarkdownIt("commonmark")
_SENSITIVE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-proj-|sk-live-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}|"
    r"\b(?:password|access_token|refresh_token|client_secret|api_key)\s*[:=]\s*\S+|"
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b1[3-9][0-9]{9}\b",
    re.I,
)


def compact_expression(text: str) -> str:
    """A copy-alarm normalization, not a test of lawful use or semantic originality."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", text).casefold()
        if character.isalnum()
    )


def summary_output_issues(
    title: str,
    body: str,
    source_texts: list[str],
    citation_urls: set[str],
    public_metadata: dict[str, Any] | None = None,
) -> list[str]:
    """Block obvious assets, private data and copying; a separate reviewer is still required.

    The 40-character alarm is deliberately conservative. Passing this scan neither
    establishes independent expression nor grants any right to publish.
    """
    issues: set[str] = set()
    metadata_text = "\n".join(
        item
        for value in (public_metadata or {}).values()
        for item in (value if isinstance(value, list) else [value])
        if isinstance(item, str)
    )
    all_text = "\n".join([title, body, metadata_text])
    if not body.strip() or len(body) > 50_000 or len(title) > 300:
        issues.add("DERIVATIVE_TEXT_BOUNDS")
    if _SENSITIVE.search(all_text):
        issues.add("DERIVATIVE_SENSITIVE_PATTERN")
    if re.search(r"(?m)^\s*\|?.+\|.+\n\s*\|?\s*:?-{3,}", body):
        issues.add("DERIVATIVE_ASSET_OR_QUOTATION")
    for token in _MARKDOWN.parse(body):
        if token.type in {"fence", "code_block", "html_block", "blockquote_open"}:
            issues.add("DERIVATIVE_ASSET_OR_QUOTATION")
        for child in token.children or []:
            if child.type in {"image", "code_inline", "html_inline"}:
                issues.add("DERIVATIVE_ASSET_OR_QUOTATION")
            if child.type == "link_open":
                target = child.attrGet("href") or ""
                if target not in citation_urls:
                    issues.add("DERIVATIVE_UNVERIFIED_LINK")
    for url in re.findall(r"https?://[^\s<>\])]+", body):
        if url not in citation_urls:
            issues.add("DERIVATIVE_UNVERIFIED_LINK")
    for url in citation_urls:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            issues.add("DERIVATIVE_UNSAFE_CITATION")
    # Exclude approved attribution destinations from comparison, not arbitrary prose.
    compared = all_text
    for url in citation_urls:
        compared = compared.replace(url, "")
    expression = compact_expression(compared)
    width = 40
    windows = {expression[index : index + width] for index in range(len(expression) - width + 1)}
    for source in source_texts:
        source_expression = compact_expression(source)
        if source_expression and compact_expression(body) == source_expression:
            issues.add("DERIVATIVE_COPY_ALARM")
        if any(
            source_expression[index : index + width] in windows
            for index in range(len(source_expression) - width + 1)
        ):
            issues.add("DERIVATIVE_COPY_ALARM")
    return sorted(issues)


def public_entry_input(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Use only the published semantic body, never internal quotations or private notes."""
    if entry is None:
        return None
    return {
        key: entry.get(key)
        for key in ("entry_id", "revision", "title", "body", "entry_type", "visibility")
    }
