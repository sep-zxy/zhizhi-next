"""Synthetic storage/CLI tests; fake DPAPI is not real Windows acceptance."""

from __future__ import annotations

import base64
import json
import sys

import pytest
import uvicorn

from ahadiff.knowledge_platform import local_test_identity as identity
from ahadiff.knowledge_platform.__main__ import main as service_main

TOKEN = "A" * 64
DEVICE = "a" * 32


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "portable"
    (root / "data").mkdir(parents=True)
    monkeypatch.setattr(identity, "_test_root", lambda: root)
    monkeypatch.setattr(identity.secrets, "token_urlsafe", lambda _: TOKEN)
    monkeypatch.setattr(identity.secrets, "token_hex", lambda _: DEVICE)

    def fake_dpapi(data, *, decrypt=False):
        if decrypt:
            assert data.startswith(b"SYNTHETIC_DPAPI_FIXTURE:")
            return data.removeprefix(b"SYNTHETIC_DPAPI_FIXTURE:")
        return b"SYNTHETIC_DPAPI_FIXTURE:" + data

    monkeypatch.setattr(identity, "_dpapi", fake_dpapi)
    return root


def test_initializer_only_creates_dedicated_fixture_files_and_never_overwrites(isolated):
    production = isolated / "config"
    production.mkdir()
    original = b'{"existing":"never-modify"}'
    (production / "launch.json").write_bytes(original)
    assert identity.initialize_local_test_identity(8766) == "initialized"
    directory = isolated / "data/local-test"
    assert {path.name for path in directory.iterdir()} == {
        "identity.json", "launch.json", "platform"
    }
    assert not list((directory / "platform").iterdir())
    assert TOKEN not in (directory / "identity.json").read_text()
    saved = (directory / "identity.json").read_bytes()
    loaded = identity.load_local_test_identity(
        directory / "identity.json", state_directory=directory / "platform",
        host="127.0.0.1", port=8766,
    )
    assert loaded.token == TOKEN and loaded.device_id == DEVICE
    assert TOKEN not in repr(loaded) and DEVICE not in repr(loaded)
    assert identity.initialize_local_test_identity(8766) == "already_initialized"
    assert (directory / "identity.json").read_bytes() == saved
    assert (production / "launch.json").read_bytes() == original
    with pytest.raises(identity.LocalTestIdentityError):
        identity.initialize_local_test_identity(8767)


@pytest.mark.parametrize("case", ["directory", "identity_only", "foreign_state"])
def test_partial_or_preexisting_directory_is_not_adopted_or_overwritten(isolated, case):
    directory = isolated / "data/local-test"
    directory.mkdir()
    if case == "identity_only":
        (directory / "identity.json").write_text("{}")
    elif case == "foreign_state":
        (directory / "platform").mkdir()
        (directory / "platform/production.txt").write_text("leave unchanged")
    before = sorted(str(path.relative_to(directory)) for path in directory.rglob("*"))
    with pytest.raises((identity.LocalTestIdentityError, OSError)):
        identity.initialize_local_test_identity(8766)
    assert before == sorted(str(path.relative_to(directory)) for path in directory.rglob("*"))


def test_local_protection_failure_writes_no_identity(isolated, monkeypatch):
    def denied(*args, **kwargs):
        raise identity.LocalTestIdentityError("LOCAL_PROTECTION_FAILED")
    monkeypatch.setattr(identity, "_dpapi", denied)
    with pytest.raises(identity.LocalTestIdentityError, match="LOCAL_PROTECTION_FAILED"):
        identity.initialize_local_test_identity(8766)
    assert not (isolated / "data/local-test").exists()


@pytest.mark.parametrize("field", ["token", "owner_id", "package_root", "scopes", "port", "extra"])
def test_tampered_protected_identity_is_rejected_even_with_updated_checksum(isolated, field):
    identity.initialize_local_test_identity(8766)
    path = isolated / "data/local-test/identity.json"
    document = json.loads(path.read_text())
    wrapped = json.loads(identity._dpapi(base64.b64decode(document["protected"]), decrypt=True))
    wrapped["payload"][field] = "tampered"
    wrapped["sha256"] = identity.hashlib.sha256(identity._canonical(wrapped["payload"])).hexdigest()
    document["protected"] = base64.b64encode(
        identity._dpapi(identity._canonical(wrapped))
    ).decode()
    path.write_text(json.dumps(document))
    with pytest.raises(identity.LocalTestIdentityError):
        identity._load(isolated)


@pytest.mark.parametrize(
    "path,state,host,port",
    [
        ("data/production.json", "data/local-test/platform", "127.0.0.1", 8766),
        ("data/local-test/identity.json", "data/platform", "127.0.0.1", 8766),
        ("data/local-test/identity.json", "data/local-test/platform", "0.0.0.0", 8766),
        ("data/local-test/identity.json", "data/local-test/platform", "127.0.0.1", 8767),
    ],
)
def test_service_refuses_production_path_host_and_port(isolated, path, state, host, port):
    identity.initialize_local_test_identity(8766)
    with pytest.raises(identity.LocalTestIdentityError):
        identity.load_local_test_identity(
            isolated / path, state_directory=isolated / state, host=host, port=port,
        )


def test_redirected_local_test_directory_is_rejected_before_writing(isolated, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (isolated / "data/local-test").symlink_to(outside, target_is_directory=True)
    with pytest.raises(identity.LocalTestIdentityError, match="REDIRECTED_TEST_PATH"):
        identity.initialize_local_test_identity(8766)
    assert not list(outside.iterdir())


def test_non_windows_runtime_cannot_initialize_or_encrypt(monkeypatch):
    monkeypatch.setattr(identity.sys, "platform", "linux")
    for operation in (identity._test_root, lambda: identity._dpapi(b"synthetic")):
        with pytest.raises(identity.LocalTestIdentityError, match="WINDOWS_REQUIRED"):
            operation()


@pytest.mark.parametrize(
    "relative,allowed",
    [
        ("", True), ("git-delivery/releases/abcdef012345-123-1", True),
        ("releases/abcdef012345-123-1", False),
        ("git-delivery/releases/arbitrary", False),
        ("git-delivery/releases/abcdef012345-123-1/nested", False),
        ("other", False),
    ],
)
def test_windows_root_policy_is_exact_and_cannot_expand_to_arbitrary_subdirectories(
    tmp_path, monkeypatch, relative, allowed
):
    approved = tmp_path / "approved-fixture"
    root = approved / relative
    root.mkdir(parents=True)
    monkeypatch.setattr(identity.sys, "platform", "win32")
    monkeypatch.setattr(identity, "_APPROVED_ROOT", str(approved))
    monkeypatch.setattr(identity, "portable_root", lambda: root)
    monkeypatch.setattr(identity, "validate_portable_runtime", lambda *args, **kwargs: None)
    if allowed:
        assert identity._test_root() == root
    else:
        with pytest.raises(identity.LocalTestIdentityError, match="UNAPPROVED_TEST_ROOT"):
            identity._test_root()


def test_pairing_posts_only_to_pinned_loopback_without_redirects_or_secret_output(
    isolated, monkeypatch, capsys
):
    identity.initialize_local_test_identity(8766)
    recorded = {}

    class Connection:
        def __init__(self, host, port, timeout):
            recorded.update(host=host, port=port, timeout=timeout)

        def request(self, method, path, body, headers):
            recorded.update(method=method, path=path, body=body, headers=headers)

        def getresponse(self):
            return self

        status = 200

        def read(self, limit):
            assert limit == 4097
            return b'{"status":"pairing_approved","fixture":true}'

        def close(self):
            recorded["closed"] = True

    monkeypatch.setattr(identity.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(sys, "argv", ["identity", "pair", "--pairing-code", "A" * 20])
    identity.main()
    output = capsys.readouterr().out
    assert TOKEN not in output and DEVICE not in output and "A" * 20 not in output
    assert recorded["host"] == "127.0.0.1" and recorded["port"] == 8766
    assert recorded["path"] == "/api/knowledge/v1/auth/local-test/approve"
    assert recorded["headers"]["Authorization"] == "Bearer " + TOKEN
    assert recorded["closed"] is True


def test_cli_failure_is_controlled_and_redacts_exception_content(monkeypatch, capsys):
    def fail(_):
        raise OSError("secret-value-user-path-must-not-print")
    monkeypatch.setattr(identity, "initialize_local_test_identity", fail)
    monkeypatch.setattr(
        sys, "argv", ["identity", "initialize", "--approve-local-test-identity"]
    )
    with pytest.raises(SystemExit) as caught:
        identity.main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert "secret-value" not in output.out + output.err
    assert json.loads(output.out)["code"] == "LOCAL_TEST_OPERATION_FAILED"


def test_initialize_cli_requires_explicit_approved_action_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["identity", "initialize"])
    with pytest.raises(SystemExit) as caught:
        identity.main()
    assert caught.value.code == 2


def test_service_cli_uses_only_test_identity_without_model_or_worker(isolated, monkeypatch):
    identity.initialize_local_test_identity(8766)
    launch = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launch.update(app=app, **kwargs))
    monkeypatch.setattr(
        sys, "argv",
        ["knowledge", "--state-directory", str(isolated / "data/local-test/platform"),
         "--local-test-identity", str(isolated / "data/local-test/identity.json")],
    )
    service_main()
    try:
        assert launch["host"] == "127.0.0.1" and launch["port"] == 8766
        assert launch["access_log"] is False
        assert launch["app"].state.workflow is None
        assert launch["app"].state.channel_supervisor is None
    finally:
        launch["app"].state.domain.close()


@pytest.mark.parametrize(
    "flags",
    [["--identities-file", "must-not-read.json"], ["--owner", "production"],
     ["--run-worker"], ["--auto-commit"], ["--provider", "openai"],
     ["--model-config", "must-not-read.json"], ["--channel-config", "must-not-read.json"],
     ["--asr-model-directory", "must-not-read"], ["--input-classification", "employer"]],
)
def test_service_cli_rejects_mixing_local_test_and_production_options(
    isolated, monkeypatch, flags
):
    monkeypatch.setattr(
        sys, "argv",
        ["knowledge", "--state-directory", str(isolated / "data/local-test/platform"),
         "--local-test-identity", str(isolated / "data/local-test/identity.json"), *flags],
    )
    with pytest.raises(SystemExit) as caught:
        service_main()
    assert caught.value.code == 2
    assert not (isolated / "data/local-test").exists()
