"""Mocked official transports exercise redaction and boundaries, not live accounts."""

import asyncio
import logging
import traceback

import httpx
import pytest

from ahadiff.knowledge_platform.channels.http_client import official_json_post
from ahadiff.knowledge_platform.intake.contracts import IntakeError


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", client)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.telegram.org/bot123:synthetic_fixture_token/getUpdates",
        "https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token=synthetic_fixture_token",
    ],
)
def test_httpx_info_log_never_exposes_injected_credential(monkeypatch, caplog, url):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"ok": True}))
    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(official_json_post(url, {})) == {"ok": True}
    assert "credential-redacted" in caplog.text
    assert "synthetic_fixture_token" not in caplog.text


def test_transport_exception_does_not_echo_token_url(monkeypatch):
    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)

    install_transport(monkeypatch, fail)
    url = "https://api.telegram.org/bot123:fixture_secret/getUpdates"
    with pytest.raises(IntakeError) as error:
        asyncio.run(official_json_post(url, {}))
    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.code == "CHANNEL_UNAVAILABLE"
    assert "fixture_secret" not in rendered


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
def test_official_failure_is_explicit_and_does_not_follow_redirect(monkeypatch, status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/"})

    install_transport(monkeypatch, respond)
    with pytest.raises(IntakeError):
        asyncio.run(official_json_post("https://api.telegram.org/bot123:fixture/getUpdates", {}))
    assert len(requests) == 1


def test_official_response_size_limit(monkeypatch):
    install_transport(
        monkeypatch, lambda request: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
    )
    with pytest.raises(IntakeError) as error:
        asyncio.run(official_json_post("https://api.telegram.org/bot123:fixture/getUpdates", {}))
    assert error.value.code == "CHANNEL_RESPONSE_TOO_LARGE"
