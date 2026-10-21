"""OpenBao protocol fixtures, not proof of a configured live secret service."""

import json

import httpx
import pytest

from ahadiff.knowledge_platform.secrets import OpenBaoReader, SecretReference, SecretUnavailable


def test_exact_secret_reference_and_no_redirect(tmp_path):
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"token": "fixture-bootstrap-only"}))
    identity.chmod(0o600)
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.path == "/v1/kv/data/zhizhi/channel"
        return httpx.Response(200, json={"data": {"data": {"token": "fixture-result-only"}}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        reader = OpenBaoReader("https://bao.example", identity,
                               {"channel": SecretReference("kv", "zhizhi/channel", "token")},
                               client=client)
        assert reader.resolve("channel") == "fixture-result-only"
        with pytest.raises(SecretUnavailable, match="NOT_ALLOWED"):
            reader.resolve("admin/root")
        assert len(seen) == 1


def test_secret_failure_does_not_expose_response_or_identity(tmp_path):
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"token": "fixture-bootstrap-only"}))
    identity.chmod(0o600)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            302, headers={"Location": "https://other.example"}, text="private-diagnostic"))) as client:
        reader = OpenBaoReader("https://bao.example", identity,
                               {"channel": SecretReference("kv", "zhizhi/channel", "token")},
                               client=client)
        with pytest.raises(SecretUnavailable) as caught:
            reader.resolve("channel")
        assert str(caught.value) == "SECRET_UNAVAILABLE"
        assert caught.value.__suppress_context__


@pytest.mark.parametrize("path", ["../root", "a/../root", "a?version=1", "/root"])
def test_references_cannot_escape_policy(path):
    with pytest.raises(ValueError):
        SecretReference("kv", path, "token")
