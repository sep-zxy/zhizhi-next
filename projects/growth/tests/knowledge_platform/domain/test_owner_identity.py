"""Synthetic DPAPI adapter and real file/assembly checks, not Windows protection proof."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import sys

import pytest
import uvicorn

from ahadiff.knowledge_platform import owner_identity as identity
from ahadiff.knowledge_platform import owner_pairing
from ahadiff.knowledge_platform.__main__ import main as service_main
from ahadiff.knowledge_platform.desktop_config import configure_desktop, reset_desktop
from ahadiff.knowledge_platform.local_protection import LocalProtectionError

TOKEN = "B" * 64
DEVICE = "b" * 32


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "portable"
    (root / "data").mkdir(parents=True)
    monkeypatch.setattr(identity, "_acceptance_root", lambda: root)
    monkeypatch.setattr(identity.secrets, "token_urlsafe", lambda _: TOKEN)
    monkeypatch.setattr(identity.secrets, "token_hex", lambda _: DEVICE)

    def fake_dpapi(data, *, entropy_bytes, decrypt=False):
        assert entropy_bytes == identity.ENTROPY
        marker = b"SYNTHETIC_OWNER_DPAPI_FIXTURE:"
        if decrypt:
            assert data.startswith(marker)
            return data.removeprefix(marker)
        return marker + data

    monkeypatch.setattr(identity, "protect_current_user", fake_dpapi)
    yield root
    reset_desktop()


def load(root):
    return identity.load_owner_identity(root / "data/owner-acceptance/identity.json")


def test_initialization_is_separate_empty_and_non_overwriting(isolated):
    old = isolated / "data/local-test"
    old.mkdir()
    (old / "untouched").write_text("do not adopt", encoding="utf-8")
    assert identity.initialize_owner_identity(8766) == "initialized"
    directory = isolated / "data/owner-acceptance"
    assert {path.name for path in directory.iterdir()} == {
        "identity.json",
        "launch.json",
        "desktop.json",
        "platform",
        "desktop",
    }
    assert not list((directory / "platform").iterdir()) and not list(
        (directory / "desktop").iterdir()
    )
    loaded = load(isolated)
    assert loaded.token == TOKEN and loaded.device_id == DEVICE
    assert TOKEN not in repr(loaded) and DEVICE not in repr(loaded)
    assert TOKEN not in (directory / "identity.json").read_text()
    assert loaded.actor().owner_id != "portable-e2e-fixture"
    assert loaded.actor().scopes == frozenset(
        {"knowledge:read", "knowledge:write", "knowledge:review"}
    )
    before = (directory / "identity.json").read_bytes()
    assert identity.initialize_owner_identity(8766) == "already_initialized"
    assert (directory / "identity.json").read_bytes() == before
    assert (old / "untouched").read_text() == "do not adopt"
    assert json.loads((directory / "desktop.json").read_text())["model"] == {"enabled": False}
    with pytest.raises(identity.OwnerIdentityError):
        identity.initialize_owner_identity(8767)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", "portable-e2e-fixture"),
        ("actor_id", "other"),
        ("scopes", ["*"]),
        ("kind", "service"),
        ("fixture", True),
        ("acceptance_only", False),
        ("package_root", "other"),
        ("port", True),
        ("token", "invalid"),
        ("extra", True),
    ],
)
def test_protected_payload_cannot_swap_roles_or_adopt_other_state(isolated, field, value):
    identity.initialize_owner_identity(8766)
    path = isolated / "data/owner-acceptance/identity.json"
    document = json.loads(path.read_text())
    payload = json.loads(
        identity.protect_current_user(
            base64.b64decode(document["protected"]), entropy_bytes=identity.ENTROPY, decrypt=True
        )
    )
    payload[field] = value
    document["protected"] = base64.b64encode(
        identity.protect_current_user(identity._canonical(payload), entropy_bytes=identity.ENTROPY)
    ).decode()
    path.write_text(json.dumps(document))
    with pytest.raises(identity.OwnerIdentityError):
        load(isolated)


@pytest.mark.parametrize("case", ["directory", "partial", "outside-link"])
def test_partial_existing_or_redirected_path_never_adopted(isolated, tmp_path, case):
    directory = isolated / "data/owner-acceptance"
    if case == "outside-link":
        outside = tmp_path / "outside"
        outside.mkdir()
        directory.symlink_to(outside, target_is_directory=True)
    else:
        directory.mkdir()
        if case == "partial":
            (directory / "identity.json").write_text("{}")
    with pytest.raises((identity.OwnerIdentityError, OSError)):
        identity.initialize_owner_identity(8766)
    assert not (directory / "launch.json").exists()


def test_crypto_failure_creates_no_owner_directory(isolated, monkeypatch):
    def fail(*args, **kwargs):
        raise LocalProtectionError("LOCAL_PROTECTION_FAILED")

    monkeypatch.setattr(identity, "protect_current_user", fail)
    with pytest.raises(LocalProtectionError):
        identity.initialize_owner_identity(8766)
    assert not (isolated / "data/owner-acceptance").exists()


def test_service_load_requires_exact_owner_state_host_and_port(isolated):
    identity.initialize_owner_identity(8766)
    path = isolated / "data/owner-acceptance/identity.json"
    for extra in [
        {"state_directory": isolated / "data/local-test/platform"},
        {"host": "0.0.0.0"},
        {"port": 8767},
    ]:
        with pytest.raises(identity.OwnerIdentityError):
            identity.load_owner_identity(path, **extra)
    path.rename(path.with_name("renamed.json"))
    with pytest.raises(identity.OwnerIdentityError):
        identity.load_owner_identity(path.with_name("renamed.json"))


def service_args(root):
    return [
        "knowledge",
        "--state-directory",
        str(root / "data/owner-acceptance/platform"),
        "--owner-dpapi-identity",
        str(root / "data/owner-acceptance/identity.json"),
        "--owner",
        identity.OWNER,
        "--owner-browser-actor",
        identity.ACTOR,
    ]


def test_cli_requires_existing_protected_identity_and_defaults_model_worker_off(
    isolated, monkeypatch
):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: apps.append((app, kwargs)))
    monkeypatch.setattr(sys, "argv", service_args(isolated))
    with pytest.raises(SystemExit):
        service_main()
    assert not (isolated / "data/owner-acceptance").exists()
    identity.initialize_owner_identity(8766)
    service_main()
    app, kwargs = apps[0]
    assert kwargs["host"] == "127.0.0.1" and kwargs["access_log"] is False
    assert app.state.workflow.settings.auto_commit_enabled is False
    assert app.state.workflow.runner.__class__.__name__ == "DisabledModelRunner"
    assert app.state.workflow.generator.actor_id != app.state.workflow.reviewer.actor_id
    assert app.state.workflow.writer.kind == "service"
    app.state.domain.close()


@pytest.mark.parametrize(
    "flags",
    [
        ["--channel-config", "do-not-read"],
        ["--asr-model-directory", "do-not-read"],
        ["--provider", "paid-fallback"],
    ],
)
def test_acceptance_cli_rejects_unapproved_services_or_provider(isolated, monkeypatch, flags):
    identity.initialize_owner_identity(8766)
    monkeypatch.setattr(sys, "argv", service_args(isolated) + flags)
    with pytest.raises(SystemExit) as result:
        service_main()
    assert result.value.code == 2


def test_desktop_reads_same_ciphertext_per_request_without_secret_copy(isolated):
    identity.initialize_owner_identity(8766)
    directory = isolated / "data/owner-acceptance"

    async def check():
        backend = configure_desktop(
            directory / "desktop.json",
            state_directory=directory / "desktop/.ahadiff",
            loop=asyncio.get_running_loop(),
        )
        assert backend.token_provider() == TOKEN
        assert not list((directory / "desktop/.ahadiff/knowledge-outbox").glob("*.json"))
        assert TOKEN not in (directory / "desktop.json").read_text()
        if backend.client is not None:
            await backend.client.aclose()

    asyncio.run(check())


def test_desktop_origin_device_and_state_cannot_be_rebound(isolated):
    identity.initialize_owner_identity(8766)
    path = isolated / "data/owner-acceptance/identity.json"
    values = {
        "state_directory": isolated / "data/owner-acceptance/desktop/.ahadiff",
        "service_url": "http://127.0.0.1:8766",
        "device_id": DEVICE,
    }
    for changed in [
        {"state_directory": isolated / "data/production"},
        {"service_url": "http://127.0.0.1:8767"},
        {"device_id": "other"},
    ]:
        with pytest.raises(identity.OwnerIdentityError):
            identity.load_desktop_owner_identity(path, **(values | changed))


def test_pair_cli_uses_stdin_and_protected_owner_without_url_or_token_output(
    isolated, monkeypatch, capsys
):
    identity.initialize_owner_identity(8766)
    observed = []

    def approve(config, code, *, protected_current_user):
        assert protected_current_user is True
        assert config["identities"][0]["token"] == TOKEN
        observed.append(code)
        return "pairing_approved"

    monkeypatch.setattr(owner_pairing, "_approve", approve)
    monkeypatch.setattr(sys, "stdin", io.StringIO("0123456789ABCDEF0123\n"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["pair", "--owner-dpapi-identity", str(isolated / "data/owner-acceptance/identity.json")],
    )
    owner_pairing.main()
    assert observed == ["0123456789ABCDEF0123"]
    assert capsys.readouterr().out == "pairing_approved\n"


def test_initializer_requires_explicit_approval_flag_and_does_not_echo_failure(
    isolated, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "argv", ["owner-init"])
    with pytest.raises(SystemExit):
        identity.main()
    assert not (isolated / "data/owner-acceptance").exists()
    capsys.readouterr()
    monkeypatch.setattr(
        sys, "argv", ["owner-init", "--approve-owner-acceptance-identity", "--port", "1"]
    )
    with pytest.raises(SystemExit):
        identity.main()
    output = capsys.readouterr()
    assert output.out == "" and output.err == "OWNER_IDENTITY_NOT_INITIALIZED\n"


def test_non_windows_has_no_real_dpapi_or_acceptance_root(monkeypatch):
    from ahadiff.knowledge_platform.local_protection import protect_current_user

    monkeypatch.setattr(identity.sys, "platform", "linux")
    with pytest.raises(identity.OwnerIdentityError, match="WINDOWS_REQUIRED"):
        identity._acceptance_root()
    with pytest.raises(LocalProtectionError, match="WINDOWS_REQUIRED"):
        protect_current_user(b"synthetic", entropy_bytes=identity.ENTROPY)


@pytest.mark.parametrize("relative", ["git-delivery/releases/0123456789ab-1-1", ""])
def test_approved_portable_root_shape_is_checked_before_identity_access(
    tmp_path, monkeypatch, relative
):
    approved = tmp_path / "approved"
    root = approved / relative
    root.mkdir(parents=True)
    monkeypatch.setattr(identity.sys, "platform", "win32")
    monkeypatch.setattr(identity, "APPROVED_ROOT", str(approved))
    monkeypatch.setattr(identity, "portable_root", lambda: root)
    validations = []
    monkeypatch.setattr(
        identity,
        "validate_portable_runtime",
        lambda *args, **kwargs: validations.append((args, kwargs)),
    )
    assert identity._acceptance_root() == root
    assert validations[0][1]["write_paths"] == (root / "data/owner-acceptance",)


@pytest.mark.parametrize(
    "relative",
    [
        "../outside",
        "releases/0123456789ab-1-1",
        "git-delivery/releases/arbitrary",
        "git-delivery/releases/0123456789ab-1-1/extra",
    ],
)
def test_unapproved_root_shapes_never_reach_runtime_or_identity(tmp_path, monkeypatch, relative):
    approved = tmp_path / "approved"
    approved.mkdir()
    root = (approved / relative).resolve()
    root.mkdir(parents=True)
    monkeypatch.setattr(identity.sys, "platform", "win32")
    monkeypatch.setattr(identity, "APPROVED_ROOT", str(approved))
    monkeypatch.setattr(identity, "portable_root", lambda: root)
    with pytest.raises(identity.OwnerIdentityError, match="ROOT_NOT_APPROVED"):
        identity._acceptance_root()


def test_test_policy_and_owner_protection_cannot_be_substituted():
    from ahadiff.knowledge_platform import local_test_identity

    assert identity.ENTROPY != local_test_identity._ENTROPY
    assert identity.FORMAT != local_test_identity._FORMAT
    assert identity.OWNER != local_test_identity.TEST_OWNER
    assert identity.ACTOR != local_test_identity.TEST_ACTOR
