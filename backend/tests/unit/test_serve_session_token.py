"""Ephemeral launcher configuration fixtures; no user tokens or persistent identity."""

import pytest

from ahadiff.serve import session_token


def test_unconfigured_serve_retains_random_token(monkeypatch):
    monkeypatch.delenv(session_token.SESSION_TOKEN_ENV, raising=False)
    monkeypatch.setattr(session_token.secrets, "token_urlsafe", lambda size: f"random-{size}")
    assert session_token.serve_session_token() == "random-24"


def test_parent_session_token_consumed_once_without_persistence(monkeypatch):
    token = "fixture-local-viewer-session-0001"
    monkeypatch.setenv("ZHIZHI_PORTABLE_ROOT", "/fixture")
    monkeypatch.setenv(session_token.SESSION_TOKEN_ENV, token)
    assert session_token.serve_session_token() == token
    assert session_token.SESSION_TOKEN_ENV not in session_token.os.environ
    monkeypatch.setattr(session_token.secrets, "token_urlsafe", lambda _: "fresh-session")
    assert session_token.serve_session_token() == "fresh-session"


@pytest.mark.parametrize("token", ["", "short", "a" * 129, "x" * 32 + "\n", "x y" * 12])
def test_invalid_inherited_token_fails_without_echo(monkeypatch, token):
    monkeypatch.setenv("ZHIZHI_PORTABLE_ROOT", "/fixture")
    monkeypatch.setenv(session_token.SESSION_TOKEN_ENV, token)
    with pytest.raises(ValueError, match="Invalid packaged Viewer session configuration"):
        session_token.serve_session_token()
    assert session_token.SESSION_TOKEN_ENV not in session_token.os.environ


def test_token_handoff_requires_portable_launcher(monkeypatch):
    monkeypatch.delenv("ZHIZHI_PORTABLE_ROOT", raising=False)
    monkeypatch.setenv(session_token.SESSION_TOKEN_ENV, "x" * 32)
    with pytest.raises(ValueError):
        session_token.serve_session_token()
    assert session_token.SESSION_TOKEN_ENV not in session_token.os.environ


def test_serve_consumes_token_before_runtime_or_workspace_discovery(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from ahadiff.cli import app
    from ahadiff.knowledge_platform import portable

    token = "fixture-local-viewer-session-0001"
    monkeypatch.setenv("ZHIZHI_PORTABLE_ROOT", str(tmp_path))
    monkeypatch.setenv(session_token.SESSION_TOKEN_ENV, token)
    checked = []

    def check_runtime(*args):
        checked.append(session_token.SESSION_TOKEN_ENV not in session_token.os.environ)
        raise ValueError("fixture-stop-before-server")

    monkeypatch.setattr(portable, "validate_portable_runtime", check_runtime)
    result = CliRunner().invoke(app(), ["serve", "--repo-root", str(tmp_path), "--no-browser"])
    assert checked == [True]
    assert token not in result.output
