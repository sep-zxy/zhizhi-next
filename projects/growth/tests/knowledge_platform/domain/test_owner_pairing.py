"""Operator helper fixtures: no actual credentials, network or activation."""

from __future__ import annotations

import io
import json
import sys

import pytest

from ahadiff.knowledge_platform import owner_pairing

TOKEN = "synthetic-owner-helper-token-not-real-001"
CODE = "0123456789ABCDEF0123"


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / "synthetic-identities.json"
    value = {
        "owner_browser": {
            "owner_id": "synthetic-owner",
            "actor_id": "synthetic-person",
            "origin": "http://127.0.0.1:8766",
        },
        "identities": [
            {
                "owner_id": "synthetic-owner",
                "actor_id": "synthetic-person",
                "kind": "user",
                "scopes": ["knowledge:read", "knowledge:write"],
                "token": TOKEN,
            }
        ],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)
    return path, value


def fake_connection(monkeypatch, *, status=200, payload=None, fail=False):
    calls = []
    payload = (
        payload
        if payload is not None
        else {
            "status": "pairing_approved",
            "fixture": False,
            "authentication_mode": "owner_loopback",
        }
    )

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def request(self, *args, **kwargs):
            calls.append((args, kwargs))
            if fail:
                raise OSError("synthetic secret must not be printed")

        def getresponse(self):
            class Response:
                def read(self, limit):
                    return json.dumps(payload).encode()[:limit]

            response = Response()
            response.status = status
            return response

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(owner_pairing.http.client, "HTTPConnection", Connection)
    return calls


def test_existing_identity_approval_is_exact_one_fixed_loopback_request(configured, monkeypatch):
    path, _ = configured
    calls = fake_connection(monkeypatch)
    assert owner_pairing.approve_existing_owner(path, CODE) == "pairing_approved"
    assert calls[0] == ("127.0.0.1", 8766, 5)
    assert calls[1][0] == ("POST", "/api/knowledge/v1/auth/owner/approve")
    assert json.loads(calls[1][1]["body"]) == {"pairing_code": CODE}
    assert calls[1][1]["headers"]["Authorization"] == "Bearer " + TOKEN
    assert calls[2:] == ["closed"]


@pytest.mark.parametrize(
    "status,payload",
    [
        (302, {}),
        (403, {}),
        (200, {"status": "pairing_approved", "fixture": True}),
        (200, "x" * 5000),
    ],
)
def test_redirect_failure_or_wrong_protocol_never_retries(configured, monkeypatch, status, payload):
    path, _ = configured
    calls = fake_connection(monkeypatch, status=status, payload=payload)
    with pytest.raises(ValueError):
        owner_pairing.approve_existing_owner(path, CODE)
    assert len(calls) == 3 and calls[-1] == "closed"


@pytest.mark.parametrize(
    "policy",
    [
        None,
        {},
        {
            "owner_id": "synthetic-owner",
            "actor_id": "synthetic-person",
            "origin": "http://example.test:8766",
        },
        {
            "owner_id": "synthetic-owner",
            "actor_id": "synthetic-person",
            "origin": "http://127.0.0.1:8766",
            "enabled": True,
        },
    ],
)
def test_missing_or_unbounded_policy_refused_before_network(policy, configured, monkeypatch):
    path, value = configured
    value["owner_browser"] = policy
    path.write_text(json.dumps(value), encoding="utf-8")
    calls = fake_connection(monkeypatch)
    with pytest.raises(ValueError):
        owner_pairing.approve_existing_owner(path, CODE)
    assert calls == []


def test_protected_loader_rejects_symlink_and_permissive_file(configured, monkeypatch):
    path, _ = configured
    link = path.with_name("link.json")
    link.symlink_to(path)
    calls = fake_connection(monkeypatch)
    with pytest.raises(ValueError):
        owner_pairing.approve_existing_owner(link, CODE)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        owner_pairing.approve_existing_owner(path, CODE)
    assert calls == []


def test_helper_only_emits_fixed_failure_and_no_raw_exception(configured, monkeypatch, capsys):
    path, _ = configured
    fake_connection(monkeypatch, fail=True)
    monkeypatch.setattr(sys, "argv", ["pair", "--identities-file", str(path)])
    monkeypatch.setattr(sys, "stdin", io.StringIO(CODE + "\n"))
    with pytest.raises(SystemExit) as failure:
        owner_pairing.main()
    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "OWNER_PAIRING_NOT_CONFIRMED\n"


def test_policy_presence_alone_does_not_enable_browser(configured, monkeypatch):
    import uvicorn

    from ahadiff.knowledge_platform.__main__ import main

    path, _ = configured
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: apps.append(app))
    arguments = [
        "knowledge",
        "--state-directory",
        str(path.parent / "state"),
        "--identities-file",
        str(path),
        "--owner",
        "synthetic-owner",
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    main()
    assert not any(
        getattr(route, "path", "").startswith("/api/knowledge/v1/auth/owner")
        for route in apps[0].routes
    )
    apps[0].state.domain.close()
    monkeypatch.setattr(sys, "argv", arguments + ["--owner-browser-actor", "synthetic-person"])
    main()
    assert any(
        getattr(route, "path", "") == "/api/knowledge/v1/auth/owner/request"
        for route in apps[1].routes
    )
    apps[1].state.domain.close()


def test_windows_plaintext_owner_file_policy_stays_blocked(configured, monkeypatch):
    from ahadiff.knowledge_platform import owner_browser
    _, value = configured
    monkeypatch.setattr(owner_browser.os, "name", "nt")
    with pytest.raises(ValueError, match="not yet verified"):
        owner_browser.configured_owner_policy(value)
