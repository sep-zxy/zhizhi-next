"""Normalize a single explicit URL or a copied share message without fetching it."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from .contracts import IntakeError, NormalizedShare

_URL = re.compile(r"https?://[^\s<>\"'\u3000]+", re.IGNORECASE)
_TRAILING = ".,;!?)\u3002\uff0c\uff1b\uff01\uff1f\uff09\u3011\u300b"
MAX_TEXT_CHARS = 100_000
MAX_URL_CHARS = 8_192


def clean_url(value: str) -> str:
    """Reject ambiguous URL syntax before any DNS lookup or network request."""
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS:
        raise IntakeError("invalid_url", "链接不是有效字符串或超过长度限制。")
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise IntakeError("invalid_url", "链接包含不允许的控制字符或反斜杠。")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise IntakeError("invalid_url", "链接格式无效。") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise IntakeError("invalid_url", "仅接受完整的 HTTP 或 HTTPS 链接。")
    if parsed.username is not None or parsed.password is not None:
        raise IntakeError("invalid_url", "链接不能包含登录凭据。")
    if "%" in hostname or hostname.endswith("."):
        raise IntakeError("invalid_url", "链接主机名不明确。")
    try:
        hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise IntakeError("invalid_url", "链接主机名无效。") from exc
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    if port is not None and port != default_port:
        raise IntakeError("unsafe_url", "链接只能使用标准 HTTP/HTTPS 端口。")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit(
        (
            scheme,
            netloc,
            quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
            quote(parsed.query, safe="%/?@!$&'()*+,;=:-._~"),
            "",
        )
    )


def _is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def normalize_share(*, text: str = "", url: str | None = None) -> NormalizedShare:
    """Recognize only first-release platforms; never reinterpret a game link as a post."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        raise IntakeError("invalid_text", "分享文本超过限制或格式无效。")
    if url is not None and not isinstance(url, str):
        raise IntakeError("invalid_url", "链接必须是字符串。")
    supplied = url.strip() if url else None
    if not supplied:
        matches = [match.group().rstrip(_TRAILING) for match in _URL.finditer(text)]
        unique = list(dict.fromkeys(matches))
        if len(unique) > 1:
            raise IntakeError("multiple_urls", "请每次提交一个链接，或用 url 字段明确指定。")
        supplied = unique[0] if unique else None
    if not supplied:
        if not text.strip():
            raise IntakeError("empty_share", "请提供链接或正文。")
        return NormalizedShare(text, None, None, "text")
    normalized = clean_url(supplied)
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    platform = "web"
    content_id = None
    if _is_host(host, "xiaoheihe.cn"):
        platform = "xiaoheihe"
        match = re.fullmatch(r"/app/bbs/link/([A-Za-z0-9]+)/*", parsed.path)
        if match:
            content_id = match.group(1)
        elif parsed.path.rstrip("/") in {"/v3/bbs/app/api/web/share", "/bbs/post_share"}:
            values = parse_qs(parsed.query).get("link_id", [])
            if len(values) == 1 and re.fullmatch(r"[A-Za-z0-9]+", values[0]):
                content_id = values[0]
        if content_id:
            normalized = f"https://www.xiaoheihe.cn/app/bbs/link/{content_id}"
    elif _is_host(host, "douyin.com") or _is_host(host, "iesdouyin.com"):
        platform = "douyin"
        match = re.fullmatch(r"/(?:video|note|share/video)/(\d+)/*", parsed.path)
        if match:
            content_id = match.group(1)
        if content_id:
            kind = "note" if parsed.path.startswith("/note/") else "video"
            normalized = f"https://www.douyin.com/{kind}/{content_id}"
        elif host != "v.douyin.com":
            values = parse_qs(parsed.query).get("modal_id", [])
            if len(values) == 1 and values[0].isdigit():
                content_id = values[0]
                normalized = "https://www.douyin.com/video/" + content_id
    return NormalizedShare(text, supplied, normalized, platform, content_id)
