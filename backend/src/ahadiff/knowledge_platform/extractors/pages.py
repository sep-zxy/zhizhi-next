"""Small public-page parsers, with no signing, account pool or browser execution.

These parsers read bounded JSON/HTML already fetched by SafeFetcher. They do not
claim that an inaccessible platform API is available, and never treat a video
description as a transcript. Platform markup is untrusted data throughout.
"""

from __future__ import annotations

import contextlib
import json
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urljoin

from ..intake.contracts import (
    AdapterCapabilities,
    ExtractedDocument,
    MediaReference,
    NormalizedShare,
)
from ..intake.normalize import clean_url

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: list[str] = []
        self.article: list[str] = []
        self.documents: list[Any] = []
        self.media: list[tuple[str, str]] = []
        self._title = False
        self._article = 0
        self._hidden = 0
        self._script: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "track" and attributes.get("kind", "subtitles") in {"subtitles", "captions"}:
            if attributes.get("src"):
                self.media.append((attributes["src"] or "", "captions"))
        elif tag in {"video", "audio"} and attributes.get("src"):
            self.media.append((attributes["src"] or "", tag))
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name")
            value = attributes.get("content")
            if key and value:
                self.meta[key.lower()] = value
        if tag == "title":
            self._title = True
        if tag == "article":
            self._article += 1
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden += 1
        if tag == "script" and (
            attributes.get("type") in {"application/json", "application/ld+json"}
            or attributes.get("id")
            in {"RENDER_DATA", "__NEXT_DATA__", "__UNIVERSAL_DATA_FOR_REHYDRATION__"}
        ):
            self._script = []
        if self._article and tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.article.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._title = False
        if tag == "article":
            self._article = max(0, self._article - 1)
        if tag == "script" and self._script is not None:
            candidate = "".join(self._script).strip()
            for encoded in (candidate, unquote(candidate)):
                try:
                    self.documents.append(json.loads(encoded))
                    break
                except (ValueError, RecursionError):
                    pass
            self._script = None
        if tag in {"script", "style", "noscript", "template"}:
            self._hidden = max(0, self._hidden - 1)

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        if self._title:
            self.title.append(data)
        if self._article and not self._hidden:
            self.article.append(data)


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def text_value(value: Any) -> str:
    """Accept actual strings only; do not stringify arbitrary nested provider data."""
    return value.strip() if isinstance(value, str) else ""


def plain_text(value: Any) -> str:
    """Strip markup without executing it or including script contents."""
    parser = _PlainText()
    parser.feed(text_value(value))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def objects(value: Any) -> Iterator[dict[str, Any]]:
    """Bound traversal of untrusted JSON to avoid recursion/resource exhaustion."""
    pending = [(value, 0)]
    seen = 0
    while pending and seen < 10_000:
        item, depth = pending.pop()
        seen += 1
        if depth > 24:
            continue
        if isinstance(item, dict):
            yield item
            pending.extend((child, depth + 1) for child in reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in reversed(item))


def read_page(content: bytes, content_type: str) -> _Page:
    """Read UTF-8 platform pages or JSON; undecodable bytes remain visibly replaced."""
    page = _Page()
    text = content.decode("utf-8-sig", errors="replace")
    if content_type in {"application/json", "application/ld+json"}:
        with contextlib.suppress(ValueError, RecursionError):
            page.documents.append(json.loads(text))
    elif content_type == "text/plain":
        page.article.append(text)
    else:
        page.feed(text)
    return page


def media_reference(url: Any, kind: str, base_url: str) -> MediaReference | None:
    """Validate reference syntax; actual media fetch still requires per-hop SSRF checks."""
    value = text_value(url)
    if not value or kind not in {"image", "video", "audio", "captions"}:
        return None
    try:
        normalized = clean_url(urljoin(base_url, value))
    except Exception:
        return None
    return MediaReference(normalized, kind)  # type: ignore[arg-type]


def unique_media(items: list[MediaReference | None]) -> tuple[MediaReference, ...]:
    """Keep source order while limiting the size of an untrusted media manifest."""
    found: dict[tuple[str, str], MediaReference] = {}
    for item in items:
        if item is not None:
            found.setdefault((item.kind, item.url), item)
        if len(found) >= 100:
            break
    return tuple(found.values())


class PublicPageExtractor:
    """Extract explicit article text/JSON-LD, not navigation text or invented summaries."""

    capabilities = AdapterCapabilities("public-page", "web", captions="partial")

    def extract(
        self, share: NormalizedShare, *, url: str, content: bytes, content_type: str
    ) -> ExtractedDocument:
        """Return metadata-only partial evidence when no explicit article body exists."""
        page = read_page(content, content_type)
        title = page.meta.get("og:title") or "".join(page.title).strip()
        description = page.meta.get("og:description") or page.meta.get("description", "")
        author = page.meta.get("author", "")
        body = ""
        for document in page.documents:
            for item in objects(document):
                if text_value(item.get("articleBody")):
                    body = plain_text(item["articleBody"])
                    title = text_value(item.get("headline")) or title
                    author_value = item.get("author")
                    author = (
                        text_value(author_value.get("name"))
                        if isinstance(author_value, dict)
                        else author
                    )
                    break
            if body:
                break
        body = body or "\n".join(
            line.strip() for line in "".join(page.article).splitlines() if line.strip()
        )
        return ExtractedDocument(
            url,
            share.platform,
            "public-page",
            "1",
            "extracted" if body else "needs_input",
            title=title,
            author=author,
            body_text=body,
            description=description,
            body_complete=bool(body),
            media=unique_media(
                [media_reference(page.meta.get("og:image"), "image", url)]
                + [media_reference(value, kind, url) for value, kind in page.media]
            ),
            warnings=() if body else ("未取得正文，请补充有权处理的原文。",),
            metadata={"text_basis": "article_body" if body else "metadata_only"},
        )


class XiaoheiheExtractor:
    """Parse a single public post or an explicitly supplied link/tree response.

    The upstream response structure was checked against the MIT-licensed
    Zhalslar/astrbot_plugin_parser, core/parsers/xiaoheihe.py (2026-10-19).
    No upstream signing, device fingerprint or cookie-acquisition code is copied.
    """

    capabilities = AdapterCapabilities("xiaoheihe-public", "xiaoheihe")

    def extract(
        self, share: NormalizedShare, *, url: str, content: bytes, content_type: str
    ) -> ExtractedDocument:
        """Read ordered text/image blocks; game metadata is not a post body."""
        page = read_page(content, content_type)
        selected = None
        for document in page.documents:
            for candidate in objects(document):
                candidate_id = str(candidate.get("link_id") or candidate.get("linkid") or "")
                if (
                    candidate_id and (share.content_id is None or candidate_id == share.content_id)
                ) and any(key in candidate for key in ("text", "body_text")):
                    selected = candidate
                    break
            if selected is not None:
                break
        if selected is None:
            base = PublicPageExtractor().extract(
                share, url=url, content=content, content_type=content_type
            )
            return ExtractedDocument(
                url,
                "xiaoheihe",
                "xiaoheihe-public",
                "1",
                base.state,
                title=base.title,
                author=base.author,
                body_text=base.body_text,
                description=base.description,
                body_complete=base.body_complete,
                media=base.media,
                warnings=base.warnings,
                metadata={**base.metadata, "api_auth_not_attempted": True},
            )
        body = plain_text(selected.get("body_text"))
        items: list[MediaReference | None] = []
        raw = selected.get("text")
        try:
            blocks = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, RecursionError):
            blocks = None
        if isinstance(blocks, list):
            paragraphs: list[str] = []
            for block in blocks[:1000]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "img":
                    items.append(media_reference(block.get("url"), "image", url))
                else:
                    text = plain_text(block.get("text"))
                    if text:
                        paragraphs.append(text)
            body = body or "\n\n".join(paragraphs)
        else:
            body = body or plain_text(raw)
        items.append(media_reference(selected.get("video_url"), "video", url))
        user = selected.get("user") or selected.get("user_info") or {}
        author = text_value(user.get("username")) if isinstance(user, dict) else ""
        return ExtractedDocument(
            url,
            "xiaoheihe",
            "xiaoheihe-public",
            "1",
            "extracted" if body else "needs_input",
            title=plain_text(selected.get("title")),
            author=author,
            body_text=body,
            body_complete=bool(body),
            media=unique_media(items),
            warnings=() if body else ("帖子正文不可用，未生成摘要。",),
            metadata={"text_basis": "post_body" if body else "metadata_only"},
        )


def _first_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        values = value.get("url_list") or value.get("urlList")
        if isinstance(values, list) and values:
            return text_value(values[0])
    return ""


class DouyinExtractor:
    """Parse public hydration or a supplied single-aweme response; no fake transcript."""

    capabilities = AdapterCapabilities(
        "douyin-public", "douyin", body_text="partial", captions="partial"
    )

    def extract(
        self, share: NormalizedShare, *, url: str, content: bytes, content_type: str
    ) -> ExtractedDocument:
        """Return partial when only video description/media are available."""
        page = read_page(content, content_type)
        selected = None
        for document in page.documents:
            for candidate in objects(document):
                candidate_id = str(
                    candidate.get("aweme_id")
                    or candidate.get("awemeId")
                    or candidate.get("itemId")
                    or ""
                )
                if candidate_id and (share.content_id is None or candidate_id == share.content_id):
                    selected = candidate
                    break
            if selected is not None:
                break
        title = page.meta.get("og:title") or "".join(page.title).strip()
        description = page.meta.get("og:description") or page.meta.get("description", "")
        author = ""
        transcript = ""
        items: list[MediaReference | None] = [
            media_reference(page.meta.get("og:image"), "image", url),
            media_reference(page.meta.get("og:video"), "video", url),
        ]
        items.extend(media_reference(value, kind, url) for value, kind in page.media)
        if selected is not None:
            description = (
                text_value(selected.get("desc"))
                or text_value(selected.get("description"))
                or description
            )
            title = text_value(selected.get("title")) or title or description[:160]
            user = selected.get("author")
            if isinstance(user, dict):
                author = text_value(user.get("nickname"))
            transcript = text_value(selected.get("transcript"))
            video = selected.get("video")
            if isinstance(video, dict):
                items.append(
                    media_reference(
                        _first_url(video.get("play_addr") or video.get("playAddr")), "video", url
                    )
                )
            images = selected.get("images") or []
            if isinstance(images, list):
                items.extend(
                    media_reference(_first_url(item), "image", url) for item in images[:100]
                )
        media = unique_media(items)
        return ExtractedDocument(
            url,
            "douyin",
            "douyin-public",
            "1",
            "partial" if transcript or description or media else "needs_input",
            title=title,
            author=author,
            body_text=transcript,
            description=description,
            body_complete=False,
            media=media,
            warnings=(
                "字幕来源尚未完成完整性校验。"
                if transcript
                else "仅取得作品文案或元数据，不代表完整视频；请补充字幕或有权处理的媒体。",
            ),
            metadata={
                "text_basis": "supplied_transcript" if transcript else "description_only",
                "asr_state": "not_run",
            },
        )
