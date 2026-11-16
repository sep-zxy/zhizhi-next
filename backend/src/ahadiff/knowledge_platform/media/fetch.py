"""Bounded public-web GET with per-hop DNS validation and address pinning.

There is no proxy, cookie jar, authentication, JavaScript or automatic redirect.
DNS is validated before each hop and the connection uses that exact address,
while TLS retains the original hostname for SNI and certificate verification.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from ..intake.contracts import IntakeError
from ..intake.normalize import clean_url


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Conservative limits for one public-document fetch, including redirects."""

    max_bytes: int = 4 * 1024 * 1024
    total_timeout: float = 25.0
    connect_timeout: float = 8.0
    max_redirects: int = 4
    allowed_types: tuple[str, ...] = (
        "text/html",
        "text/plain",
        "application/json",
        "application/ld+json",
    )

    def __post_init__(self) -> None:
        if self.max_bytes < 1 or self.total_timeout <= 0 or self.connect_timeout <= 0:
            raise ValueError("Fetch limits must be positive")
        if not 0 <= self.max_redirects <= 10:
            raise ValueError("Redirect limit must be between 0 and 10")


@dataclass(frozen=True, slots=True)
class FetchResponse:
    """Bounded bytes and provenance returned by the guarded transport."""

    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    redirects: tuple[str, ...] = ()

    @property
    def content_type(self) -> str:
        """Return the normalized MIME type without an untrusted charset parameter."""
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()


class Resolver(Protocol):
    """Resolve a hostname; injection exists only for deterministic transport tests."""

    async def __call__(self, hostname: str, port: int) -> list[str]: ...


class HopTransport(Protocol):
    """Fetch one already validated and pinned address without following redirects."""

    async def __call__(
        self, url: str, address: str, *, policy: FetchPolicy, timeout: float
    ) -> FetchResponse: ...


def require_public_address(address: str) -> str:
    """Reject all non-global, transition, multicast and mapped-private addresses."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise IntakeError("UNSAFE_URL", "DNS 返回了无效地址。") from exc
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            require_public_address(str(ip.ipv4_mapped))
        # Transition mechanisms can embed a different, unvalidated destination.
        if ip.sixtofour is not None or ip.teredo is not None:
            raise IntakeError("UNSAFE_URL", "不允许 IPv6 转换地址。")
        if ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            raise IntakeError("UNSAFE_URL", "不允许 NAT64 转换地址。")
    if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise IntakeError("UNSAFE_URL", "不允许访问本机、内网、保留或元数据地址。")
    return str(ip)


async def system_resolver(hostname: str, port: int) -> list[str]:
    """Resolve both address families through the event loop's bounded caller."""
    try:
        direct = ipaddress.ip_address(hostname)
    except ValueError:
        infos = await asyncio.get_running_loop().getaddrinfo(
            hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
        return list(dict.fromkeys(str(info[4][0]) for info in infos))
    return [str(direct)]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        raw_socket = self.sock
        try:
            self.sock = ssl.create_default_context().wrap_socket(
                raw_socket, server_hostname=self.host
            )
        except BaseException:
            raw_socket.close()
            raise


def _read_hop(url: str, address: str, policy: FetchPolicy, timeout: float) -> FetchResponse:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = 443 if parsed.scheme == "https" else 80
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_type(host, port, address, min(timeout, policy.connect_timeout))
    deadline = time.monotonic() + timeout

    # wait_for alone cannot stop a thread blocked by a slow header stream. A
    # separate timer shuts down the socket at the absolute request deadline.
    def abort_socket() -> None:
        sock = connection.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()

    timer = threading.Timer(timeout, abort_socket)
    timer.daemon = True
    timer.start()
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    try:
        connection.request(
            "GET",
            target,
            headers={
                "User-Agent": "ZhizhiIntake/1.0 (+public-content-only)",
                "Accept": ", ".join(policy.allowed_types),
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status in {301, 302, 303, 307, 308}:
            return FetchResponse(url, response.status, headers, b"")
        if response.status >= 400:
            # No need to download an error body, which can contain private URLs.
            return FetchResponse(url, response.status, headers, b"")
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in policy.allowed_types:
            raise IntakeError("UNSUPPORTED_MEDIA_TYPE", "来源返回了不支持的内容类型。")
        if headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
            raise IntakeError("UNSUPPORTED_ENCODING", "来源未遵守无压缩下载要求。")
        length = headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError as exc:
                raise IntakeError("INVALID_RESPONSE", "来源返回无效内容长度。") from exc
            if declared < 0 or declared > policy.max_bytes:
                raise IntakeError("CONTENT_TOO_LARGE", "来源内容超过大小限制。")
        chunks: list[bytes] = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            # HTTPResponse holds the socket after Connection: close; configure its
            # raw socket too so a slow body cannot reset the total time budget.
            if response.fp is not None:
                raw = getattr(response.fp, "raw", None)
                body_socket = getattr(raw, "_sock", None)
                if body_socket is not None:
                    body_socket.settimeout(min(remaining, policy.connect_timeout))
            chunk = response.read1(min(64 * 1024, policy.max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > policy.max_bytes:
                raise IntakeError("CONTENT_TOO_LARGE", "来源内容超过大小限制。")
        return FetchResponse(url, response.status, headers, b"".join(chunks))
    finally:
        timer.cancel()
        connection.close()


async def pinned_transport(
    url: str, address: str, *, policy: FetchPolicy, timeout: float
) -> FetchResponse:
    """Run blocking stdlib TLS I/O away from the server event loop."""
    return await asyncio.to_thread(_read_hop, url, address, policy, timeout)


class SafeFetcher:
    """Public-only fetcher; every redirect repeats validation before connecting."""

    def __init__(
        self,
        policy: FetchPolicy | None = None,
        *,
        resolver: Resolver = system_resolver,
        transport: HopTransport = pinned_transport,
    ) -> None:
        self.policy = policy or FetchPolicy()
        self._resolver = resolver
        self._transport = transport
        self._semaphore = asyncio.Semaphore(1)

    async def fetch(self, url: str) -> FetchResponse:
        """Fetch bounded data, refusing private DNS, redirect loops and status failures."""
        async with self._semaphore:
            return await self._fetch(url)

    async def _fetch(self, url: str) -> FetchResponse:
        current = clean_url(url)
        visited: list[str] = []
        deadline = time.monotonic() + self.policy.total_timeout
        try:
            for hop in range(self.policy.max_redirects + 1):
                if current in visited:
                    raise IntakeError("REDIRECT_LOOP", "来源链接重定向形成循环。")
                visited.append(current)
                parsed = urlsplit(current)
                host = parsed.hostname or ""
                if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                    raise IntakeError("UNSAFE_URL", "不允许访问本机或内网名称。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                addresses = await asyncio.wait_for(
                    self._resolver(host, 443 if parsed.scheme == "https" else 80), remaining
                )
                if not addresses:
                    raise IntakeError("DNS_FAILED", "未能解析来源主机。", retryable=True)
                if len(addresses) > 64:
                    raise IntakeError("DNS_FAILED", "来源 DNS 返回过多地址。")
                validated = [require_public_address(address) for address in addresses]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                response = await asyncio.wait_for(
                    self._transport(current, validated[0], policy=self.policy, timeout=remaining),
                    remaining,
                )
                if len(response.content) > self.policy.max_bytes:
                    raise IntakeError("CONTENT_TOO_LARGE", "来源内容超过大小限制。")
                if response.status_code in {301, 302, 303, 307, 308}:
                    if hop == self.policy.max_redirects:
                        raise IntakeError("TOO_MANY_REDIRECTS", "来源重定向次数超过限制。")
                    location = response.headers.get("location")
                    if not location:
                        raise IntakeError("INVALID_RESPONSE", "来源重定向缺少目标。")
                    destination = clean_url(urljoin(current, location))
                    if parsed.scheme == "https" and urlsplit(destination).scheme != "https":
                        raise IntakeError("UNSAFE_REDIRECT", "不允许从 HTTPS 降级到 HTTP。")
                    current = destination
                    continue
                if response.status_code in {401, 403}:
                    raise IntakeError(
                        "NEEDS_AUTH", "来源要求登录或拒绝访问，请补充有权处理的正文。"
                    )
                if response.status_code == 429:
                    raise IntakeError("RATE_LIMITED", "来源限制请求，请稍后重试。", retryable=True)
                if not 200 <= response.status_code < 300:
                    raise IntakeError(
                        "FETCH_FAILED",
                        f"来源返回 HTTP {response.status_code}。",
                        retryable=response.status_code >= 500,
                    )
                if response.content_type not in self.policy.allowed_types:
                    raise IntakeError("UNSUPPORTED_MEDIA_TYPE", "来源返回了不支持的内容类型。")
                if response.headers.get("content-encoding", "identity").lower() not in {
                    "",
                    "identity",
                }:
                    raise IntakeError("UNSUPPORTED_ENCODING", "来源未遵守无压缩下载要求。")
                return FetchResponse(
                    current,
                    response.status_code,
                    response.headers,
                    response.content,
                    tuple(visited[:-1]),
                )
        except TimeoutError as exc:
            raise IntakeError("FETCH_TIMEOUT", "来源下载超过时间限制。", retryable=True) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise IntakeError(
                "FETCH_FAILED", "无法连接来源，未使用代理或绕过访问限制。", retryable=True
            ) from exc
        raise IntakeError("FETCH_FAILED", "无法获取来源。")
