"""Deterministic security fixtures; these are not live-platform acceptance tests."""

import asyncio

import pytest

from ahadiff.knowledge_platform.intake.contracts import IntakeError
from ahadiff.knowledge_platform.intake.normalize import clean_url, normalize_share
from ahadiff.knowledge_platform.media.fetch import (
    FetchPolicy,
    FetchResponse,
    SafeFetcher,
    require_public_address,
)


def test_xiaoheihe_share_has_stable_post_identity():
    share = normalize_share(
        text="分享 https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=abc12&share=1。"
    )
    assert share.platform == "xiaoheihe"
    assert share.content_id == "abc12"
    assert share.url == "https://www.xiaoheihe.cn/app/bbs/link/abc12"
    assert "share=1" in share.original_url


def test_game_link_is_not_a_post():
    assert (
        normalize_share(url="https://api.xiaoheihe.cn/game/share_game_detail?appid=1").content_id
        is None
    )


def test_xiaoheihe_public_post_share_alias_has_same_identity():
    share = normalize_share(url="https://www.xiaoheihe.cn/bbs/post_share?link_id=173399228")
    assert share.content_id == "173399228"
    assert share.url == "https://www.xiaoheihe.cn/app/bbs/link/173399228"


def test_douyin_long_link_identity_and_lookalike():
    share = normalize_share(url="https://www.douyin.com/video/12345?utm_source=test")
    assert share.content_id == "12345"
    assert share.url == "https://www.douyin.com/video/12345"
    assert normalize_share(url="https://douyin.com.attacker.example/video/123").platform == "web"


def test_multiple_urls_require_explicit_selection():
    with pytest.raises(IntakeError, match="一个链接"):
        normalize_share(text="https://one.example/a https://two.example/b")
    assert normalize_share(
        text="https://one.example/a https://two.example/b", url="https://two.example/b"
    ).url.endswith("/b")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/a",
        "https://u:p@example.com/",
        "http://example.com:8080/",
        "http://example.com\\@127.0.0.1/",
        "http://example.com/\r\nX: a",
        "http://[::1%25eth0]/",
        "http://example.com./",
    ],
)
def test_unsafe_url_syntax(url):
    with pytest.raises(IntakeError):
        clean_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "2002:7f00:0001::",
        "64:ff9b::7f00:1",
        "2001:db8::1",
    ],
)
def test_nonpublic_addresses_are_rejected(address):
    with pytest.raises(IntakeError):
        require_public_address(address)


def test_every_redirect_is_checked_before_network_access():
    calls = []

    async def resolver(host, port):
        return ["169.254.169.254"] if host == "metadata.example" else ["93.184.216.34"]

    async def transport(url, address, *, policy, timeout):
        calls.append((url, address))
        return FetchResponse(
            url, 302, {"location": "http://metadata.example/latest/meta-data"}, b""
        )

    fetcher = SafeFetcher(resolver=resolver, transport=transport)
    with pytest.raises(IntakeError) as error:
        asyncio.run(fetcher.fetch("http://public.example/start"))
    assert error.value.code == "UNSAFE_URL"
    assert calls == [("http://public.example/start", "93.184.216.34")]


def test_mixed_public_private_dns_is_rejected_without_fetching():
    async def resolver(host, port):
        return ["93.184.216.34", "127.0.0.1"]

    async def transport(*args, **kwargs):
        raise AssertionError("No connection may happen")

    with pytest.raises(IntakeError):
        asyncio.run(
            SafeFetcher(resolver=resolver, transport=transport).fetch("https://public.example/")
        )


def test_validated_address_is_passed_to_transport_without_second_dns():
    calls = []

    async def resolver(host, port):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    async def transport(url, address, *, policy, timeout):
        assert address == "93.184.216.34"
        return FetchResponse(url, 200, {"content-type": "text/plain"}, b"evidence")

    response = asyncio.run(
        SafeFetcher(resolver=resolver, transport=transport).fetch("https://public.example/")
    )
    assert response.content == b"evidence"
    assert calls == ["public.example"]


@pytest.mark.parametrize(
    "response,code",
    [
        (FetchResponse("", 200, {"content-type": "text/plain"}, b"12345"), "CONTENT_TOO_LARGE"),
        (
            FetchResponse("", 200, {"content-type": "application/octet-stream"}, b"x"),
            "UNSUPPORTED_MEDIA_TYPE",
        ),
        (
            FetchResponse(
                "", 200, {"content-type": "text/plain", "content-encoding": "gzip"}, b"x"
            ),
            "UNSUPPORTED_ENCODING",
        ),
        (FetchResponse("", 403, {}, b""), "NEEDS_AUTH"),
        (FetchResponse("", 429, {}, b""), "RATE_LIMITED"),
    ],
)
def test_fetch_limits_and_access_denials(response, code):
    async def resolver(host, port):
        return ["93.184.216.34"]

    async def transport(*args, **kwargs):
        return response

    with pytest.raises(IntakeError) as error:
        asyncio.run(
            SafeFetcher(FetchPolicy(max_bytes=4), resolver=resolver, transport=transport).fetch(
                "https://public.example/"
            )
        )
    assert error.value.code == code


def test_timeout_covers_dns_and_redirect_budget():
    async def resolver(host, port):
        await asyncio.sleep(0.05)
        return ["93.184.216.34"]

    with pytest.raises(IntakeError) as error:
        asyncio.run(
            SafeFetcher(FetchPolicy(total_timeout=0.001), resolver=resolver).fetch(
                "https://public.example/"
            )
        )
    assert error.value.code == "FETCH_TIMEOUT"
