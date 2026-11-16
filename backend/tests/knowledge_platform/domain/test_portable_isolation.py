"""Real Git policy checks and portable startup path validation, not Windows E2E."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.portable import (
    require_portable_path,
    validate_portable_runtime,
)
from ahadiff.knowledge_platform.writer import GitWriter, ReadOnlyGitWriter


@pytest.fixture
def portable_environment(tmp_path, monkeypatch):
    root = tmp_path / "package"
    state = root / "data"
    temp = root / "temp"
    state.mkdir(parents=True)
    temp.mkdir()
    git = root / "bin" / "git"
    git.parent.mkdir()
    git.write_text("bundled placeholder used only for path preflight", encoding="utf-8")
    monkeypatch.setenv("ZHIZHI_PORTABLE_ROOT", str(root))
    monkeypatch.setenv("ZHIZHI_PORTABLE_STATE", str(state))
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
    ):
        monkeypatch.setenv(name, str(temp))
    for name in (
        "PYTHONPYCACHEPREFIX",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME",
        "MPLCONFIGDIR",
        "NUMBA_CACHE_DIR",
        "PIP_CACHE_DIR",
        "UV_CACHE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "executable", str(root / "python" / "python.exe"))
    monkeypatch.setattr(sys, "path", [str(root)])
    flags = {name: getattr(sys.flags, name) for name in dir(sys.flags) if not name.startswith("_")}
    flags["no_user_site"] = 1
    monkeypatch.setattr(sys, "flags", SimpleNamespace(**flags))
    monkeypatch.chdir(root)
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setattr("ahadiff.knowledge_platform.portable.shutil.which", lambda _: str(git))
    return root, state, temp


def test_nonportable_mode_keeps_existing_server_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIZHI_PORTABLE_ROOT", raising=False)
    monkeypatch.delenv("ZHIZHI_PORTABLE_STATE", raising=False)
    assert require_portable_path("relative/server.sqlite", state=True) == Path(
        "relative/server.sqlite"
    )
    assert validate_portable_runtime(tmp_path)["portable"] is False


def test_preflight_no_files_created_and_temp_cannot_fallback(portable_environment):
    root, state, temp = portable_environment
    before = sorted(str(p) for p in root.rglob("*"))
    result = validate_portable_runtime(state, write_paths=(root / "logs" / "not-created",))
    assert result["status"] == "ok" and result["portable"] is True
    assert sorted(str(p) for p in root.rglob("*")) == before
    assert tempfile.tempdir == str(temp)
    temp.rmdir()
    with pytest.raises(FileNotFoundError), tempfile.TemporaryFile():
        pytest.fail("Temporary files must not fall back outside the package")


@pytest.mark.parametrize("name", ["HOME", "APPDATA", "TEMP", "XDG_CACHE_HOME", "HF_HOME"])
def test_external_environment_paths_rejected(portable_environment, tmp_path, monkeypatch, name):
    _, state, _ = portable_environment
    monkeypatch.setenv(name, str(tmp_path / "outside"))
    with pytest.raises(ValueError, match="escapes"):
        validate_portable_runtime(state)


def test_missing_required_env_fails_closed(portable_environment, monkeypatch):
    _, state, _ = portable_environment
    monkeypatch.delenv("APPDATA")
    with pytest.raises(ValueError, match="APPDATA"):
        validate_portable_runtime(state)


@pytest.mark.parametrize("target", ["outside", "../escape", "relative"])
def test_state_path_escapes_rejected(portable_environment, tmp_path, target):
    _, state, _ = portable_environment
    candidate = {
        "outside": tmp_path / "outside",
        "../escape": state / ".." / "escape",
        "relative": Path("relative"),
    }[target]
    with pytest.raises(ValueError):
        DomainService(candidate / "db.sqlite")
    assert not (candidate / "db.sqlite").exists()


def test_missing_descendant_of_external_symlink_rejected(portable_environment, tmp_path):
    _, state, _ = portable_environment
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        require_portable_path(state / "link" / "missing" / "db.sqlite", state=True)
    assert list(outside.iterdir()) == []


def test_all_domain_paths_checked_before_database_open(portable_environment, tmp_path):
    _, state, _ = portable_environment
    with pytest.raises(ValueError, match="escapes"):
        DomainService(state / "db.sqlite", cache_path=tmp_path / "outside")
    assert not (state / "db.sqlite").exists()


def test_bytecode_and_external_git_rejected(portable_environment, tmp_path, monkeypatch):
    _, state, _ = portable_environment
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    with pytest.raises(ValueError, match="-B"):
        validate_portable_runtime(state)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(
        "ahadiff.knowledge_platform.portable.shutil.which", lambda _: str(tmp_path / "system-git")
    )
    with pytest.raises(ValueError, match="Git executable"):
        validate_portable_runtime(state)


def test_cached_system_temp_rejected(portable_environment, tmp_path, monkeypatch):
    _, state, _ = portable_environment
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ValueError, match="Cached temporary"):
        validate_portable_runtime(state)


def test_redirected_package_anchor_rejected(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "redirected"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("ZHIZHI_PORTABLE_ROOT", str(link))
    with pytest.raises(ValueError, match="redirected"):
        require_portable_path(real / "data")


def test_sibling_prefix_is_not_inside_package(portable_environment):
    root, _, _ = portable_environment
    with pytest.raises(ValueError, match="escapes"):
        require_portable_path(root.with_name(root.name + "-outside") / "new-file")


def test_knowledge_cli_checks_identity_path_before_read(
    portable_environment, tmp_path, monkeypatch
):
    from ahadiff.knowledge_platform.__main__ import main

    _, state, _ = portable_environment
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge",
            "--state-directory",
            str(state),
            "--owner",
            "portable-test",
            "--identities-file",
            str(tmp_path / "external-identities.json"),
        ],
    )
    with pytest.raises(ValueError, match="escapes"):
        main()


def test_git_ignores_ambient_global_system_templates_and_command_config(tmp_path, monkeypatch):
    poison = tmp_path / "template"
    poison.mkdir()
    (poison / "leaked-template").write_text("must not be copied", encoding="utf-8")
    config = tmp_path / "global.gitconfig"
    config.write_text(
        f"[init]\n templateDir = {poison.as_posix()}\n[probe]\n secret = outside\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(config))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(poison))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "probe.secret")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "injected")
    writer = GitWriter(tmp_path / "vault.git")
    writer.initialize()
    assert not (writer.repository / "leaked-template").exists()
    assert not (writer.repository / "hooks").exists()
    assert writer._git("config", "--get", "probe.secret", allow_failure=True) == b""
    assert writer._git("config", "--get", "core.hooksPath").strip() == os.fsencode(os.devnull)
    assert writer._git("config", "--get", "gc.auto").strip() == b"0"
    assert "GIT_CONFIG_COUNT" not in writer._environment()
    assert writer._environment()["GIT_CONFIG_SYSTEM"] == os.devnull


def test_local_hooks_disabled_and_index_stays_in_vault(
    domain, writer, proposal, tmp_path, monkeypatch
):
    domain.writer.initialize()
    preserved = domain.writer.repository / "knowledge-index-existing"
    preserved.mkdir()
    (preserved / "user-file").write_text("preserve", encoding="utf-8")
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    hook = hooks / "reference-transaction"
    hook.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    hook.chmod(0o755)
    domain.writer._git("config", "--local", "core.hooksPath", str(hooks))
    calls = []
    original = tempfile.TemporaryDirectory

    def record(*args, **kwargs):
        calls.append(kwargs.get("dir"))
        return original(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", record)
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="isolated")
    assert entry["body"] == proposal["body"]
    assert calls and all(Path(p) == domain.writer.repository for p in calls)
    assert list(domain.writer.repository.glob("knowledge-index-*")) == [preserved]
    assert (preserved / "user-file").read_text(encoding="utf-8") == "preserve"
    assert domain.writer.contains(entry["commit_sha"])
    reader = ReadOnlyGitWriter(domain.writer.repository)
    assert reader.head() == entry["commit_sha"]


def test_pi_inherits_only_validated_portable_paths(portable_environment, monkeypatch):
    from ahadiff.knowledge_platform.model.pi_runner import PiModelRunner, PiRunnerConfig

    root, state, _ = portable_environment
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.example")
    monkeypatch.setenv("NODE_OPTIONS", "--require=untrusted")
    runner = PiModelRunner(PiRunnerConfig(agent_dir=state / "pi"))
    env = runner._environment()
    assert env["ZHIZHI_PORTABLE_ROOT"] == str(root)
    assert env["ZHIZHI_PORTABLE_STATE"] == str(state)
    for name in ("APPDATA", "LOCALAPPDATA", "USERPROFILE", "XDG_CACHE_HOME", "TMPDIR"):
        assert Path(env[name]).is_relative_to(root)
    assert env["HOME"] == str(state)
    assert not {"OPENAI_API_KEY", "HTTP_PROXY", "NODE_OPTIONS"} & env.keys()
    monkeypatch.delenv("APPDATA")
    with pytest.raises(ValueError, match="APPDATA"):
        runner._environment()
    monkeypatch.setenv("APPDATA", str(root.parent / "outside"))
    with pytest.raises(ValueError, match="escapes"):
        runner._environment()


def test_pi_rejects_external_identity_before_lock(portable_environment):
    from ahadiff.knowledge_platform.model.pi_runner import PiModelRunner, PiRunnerConfig

    root, _, _ = portable_environment
    with pytest.raises(ValueError, match="escapes"):
        PiModelRunner(PiRunnerConfig(agent_dir=root.parent / "outside"))
