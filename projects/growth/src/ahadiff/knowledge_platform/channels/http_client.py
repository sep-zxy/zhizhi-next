"""Bounded HTTPS requests to fixed official channel endpoints only."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..intake.contracts import IntakeError

_OFFICIAL_HOSTS = {"api.telegram.org", "qyapi.weixin.qq.com"}


class _CredentialURLFilter(logging.Filter):
    """httpx logs request URLs at INFO, including Bot API path credentials."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            cleaned: list[Any] = []
            for value in record.args:
                if isinstance(value, str | httpx.URL):
                    try:
                        parsed = urlsplit(str(value))
                        if parsed.hostname in _OFFICIAL_HOSTS:
                            value = f"https://{parsed.hostname}/[credential-redacted]"
                    except ValueError:
                        value = "[invalid-url-redacted]"
                cleaned.append(value)
            record.args = tuple(cleaned)
        return True


logging.getLogger("httpx").addFilter(_CredentialURLFilter())


async def official_json_post(
    url: str, payload: dict[str, Any], *, timeout: float = 40.0
) -> dict[str, Any]:
    """Do not log URLs, follow redirects, trust proxy env, or surface credential errors."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _OFFICIAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("Only fixed official channel API hosts are allowed")
    try:
        async with (
            asyncio.timeout(timeout + 5),
            httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
                headers={"Accept-Encoding": "identity"},
            ) as client,
            client.stream("POST", url, json=payload) as response,
        ):
            if response.status_code == 429:
                raise IntakeError("RATE_LIMITED", "官方渠道暂时限制请求。", retryable=True)
            if response.status_code in {401, 403}:
                raise IntakeError("NEEDS_AUTH", "官方渠道认证失效。", status_code=503)
            if response.status_code != 200:
                raise IntakeError(
                    "CHANNEL_UNAVAILABLE",
                    "官方渠道请求未成功。",
                    status_code=503,
                    retryable=True,
                )
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise IntakeError(
                    "CHANNEL_ENCODING_UNSUPPORTED", "渠道响应压缩方式不受支持。", status_code=502
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise IntakeError(
                        "CHANNEL_RESPONSE_TOO_LARGE", "渠道响应超过大小限制。", status_code=502
                    )
                chunks.append(chunk)
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (httpx.HTTPError, ValueError, UnicodeError, TimeoutError):
        raise IntakeError(
            "CHANNEL_UNAVAILABLE", "无法读取官方渠道响应。", status_code=503, retryable=True
        ) from None
