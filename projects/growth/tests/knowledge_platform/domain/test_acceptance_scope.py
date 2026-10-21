"""Synthetic activation/filesystem tests; no Windows identity or model request."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from ahadiff.knowledge_platform import owner_acceptance as activation
from ahadiff.knowledge_platform.acceptance_model import (
    SOURCE_TEXT,
    USER_TEXT,
    AcceptanceScopeError,
    ScopeReceipts,
    material_manifest,
)

SOURCE = "a" * 40
RELEASE = "a" * 12 + "-123-1"


def write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def package(tmp_path):
    root = tmp_path / RELEASE
    root.mkdir()
    (root / "app").mkdir()
    (root / "runtime").mkdir()
    index = root / "app/wiki-private/index.html"
    index.parent.mkdir()
    index.write_bytes(b"<!doctype html><title>Synthetic static fixture</title>")
    build = {
        "schema_version": 1,
        "windows_e2e": "not_run",
        "source_commit": SOURCE,
        "repository": "sep-zxy/zhizhi-platform",
        "runner": "github-hosted",
        "event": "workflow_dispatch",
        "status": "ci_candidate_built",
        "final_delivery": False,
        "run_id": "123",
        "run_attempt": "1",
        "phases": [
            {"phase": name, "status": "passed"}
            for name in (
                "backend-isolated-fixtures",
                "viewer-learning-browser-fixtures",
                "private-wiki-browser-fixtures",
                "assemble-windows-product",
            )
        ],
    }
    build_hash = write(root / "provenance/github-build.json", build)
    distribution = {
        "target": "windows-x64",
        "files": {
            "provenance/github-build.json": build_hash,
            "app/wiki-private/index.html": hashlib.sha256(index.read_bytes()).hexdigest(),
        },
    }
    distribution_hash = write(root / "provenance/distribution.json", distribution)
    config_hash = write(root / "config/launch.json", {"synthetic_fixture": True})
    write(
        root / "release.json",
        {
            "source_commit": SOURCE,
            "release_id": RELEASE,
            "final_delivery": False,
            "github_run_id": "123",
            "github_run_attempt": "1",
            "distribution_sha256": distribution_hash,
            "files": {
                **distribution["files"],
                "config/launch.json": config_hash,
                "provenance/distribution.json": distribution_hash,
            },
        },
    )
    directory = root / "data/owner-acceptance"
    (directory / "platform").mkdir(parents=True)
    (directory / "desktop").mkdir()
    write(directory / "desktop.json", {"model": {"enabled": False}, "providers": []})
    exe = tmp_path / "synthetic-codex-not-executed.exe"
    exe.write_text("SYNTHETIC NOT EXECUTABLE")
    home = tmp_path / "synthetic-auth-home"
    home.mkdir()
    (home / "must-not-read.txt").write_text("SYNTHETIC; not an actual credential")
    identity = Mock(token="SYNTHETIC-INERT-OWNER-NOT-A-CREDENTIAL", port=8766)
    with (
        patch.object(activation, "_acceptance_root", return_value=root),
        patch.object(activation, "load_owner_identity", return_value=identity),
    ):
        yield root, exe, home


def activate(package, **extra):
    _, exe, home = package
    return activation.activate(
        **{
            "approved": True,
            "expected_source": SOURCE,
            "expected_release": RELEASE,
            "codex_exe": exe,
            "codex_home": home,
            **extra,
        }
    )


def test_manifest_is_two_fixed_synthetic_utf8_strings_without_newlines():
    manifest = material_manifest()
    assert manifest["synthetic"] is True and manifest["real_user_expression"] is False
    assert manifest["model"] == "gpt-6.1-sol"
    assert manifest["cli_version"] == "0.163.0-alpha.11"
    for name, text in (("source", SOURCE_TEXT), ("explanation", USER_TEXT)):
        assert not text.endswith("\n")
        assert manifest["materials"][name]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    for field in ("public_publish", "channels", "asr", "legacy_answer"):
        assert manifest[field] is False


def test_missing_approval_never_reads_identity_or_state():
    with patch.object(activation, "_acceptance_root") as root:
        with pytest.raises(AcceptanceScopeError, match="EXPLICIT_APPROVAL"):
            activation.activate(
                approved=False,
                expected_source=SOURCE,
                expected_release=RELEASE,
                codex_exe=Path("unused"),
                codex_home=Path("unused"),
            )
        root.assert_not_called()


def test_activation_is_exact_idempotent_and_never_executes_cli(package):
    root, _, home = package
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(home), "Existing auth home contents must not be read"
        return original(path, *args, **kwargs)

    with patch.object(Path, "open", guarded):
        assert activate(package) == "activated_not_started"
        files = {
            path.name: path.read_bytes()
            for path in (root / "data/owner-acceptance/acceptance-receipts").iterdir()
        }
        (root / "data/owner-acceptance/platform/after-activation.txt").write_text("SYNTHETIC STATE")
        assert activate(package) == "already_activated"
        assert files == {
            path.name: path.read_bytes()
            for path in (root / "data/owner-acceptance/acceptance-receipts").iterdir()
        }
    assert "SYNTHETIC-INERT-OWNER-NOT-A-CREDENTIAL" not in next(iter(files.values())).decode()


@pytest.mark.parametrize(
    "field,value",
    [("expected_source", "b" * 40), ("expected_release", "a" * 12 + "-124-1"), ("approved", 1)],
)
def test_binding_or_approval_change_rejected_without_receipt(package, field, value):
    with pytest.raises(AcceptanceScopeError):
        activate(package, **{field: value})
    assert not (package[0] / "data/owner-acceptance/acceptance-receipts").exists()


@pytest.mark.parametrize("directory", ["platform", "desktop"])
def test_no_existing_even_empty_ledger_is_adopted(package, directory):
    (package[0] / "data/owner-acceptance" / directory / "existing.db").write_bytes(b"")
    with pytest.raises(AcceptanceScopeError, match="NEW_EMPTY_STATE"):
        activate(package)


def test_different_cli_path_cannot_rebind_existing_activation(package, tmp_path):
    assert activate(package) == "activated_not_started"
    other = tmp_path / "other-not-an-executable.exe"
    other.write_text("synthetic")
    with pytest.raises(AcceptanceScopeError, match="CONFLICT"):
        activate(package, codex_exe=other)


def test_provenance_and_unlisted_runtime_changes_fail_closed(package):
    root = package[0]
    (root / "app/extra.py").write_text("# synthetic unexpected code")
    with pytest.raises(AcceptanceScopeError, match="UNLISTED_CODE"):
        activate(package)
    (root / "app/extra.py").unlink()
    (root / "provenance/github-build.json").write_text("{}")
    with pytest.raises(AcceptanceScopeError):
        activate(package)


def test_receipts_reject_tampering_rebinding_or_different_identity(package):
    root = package[0]
    directory = root / "data/owner-acceptance/proofs"
    receipts = ScopeReceipts(root, directory, {"source": SOURCE}, "synthetic-key")
    receipts.put("one", {"hash": "1" * 64})
    receipts.put("one", {"hash": "1" * 64})
    with pytest.raises(AcceptanceScopeError, match="CONFLICT"):
        receipts.put("one", {"hash": "2" * 64})
    for binding, token in [
        ({"source": "other"}, "synthetic-key"),
        ({"source": SOURCE}, "different"),
    ]:
        with pytest.raises(AcceptanceScopeError, match="INVALID"):
            ScopeReceipts(root, directory, binding, token).read("one")
    file = next(directory.iterdir())
    record = json.loads(file.read_text())
    record["payload"]["value"]["hash"] = "3" * 64
    file.write_text(json.dumps(record))
    with pytest.raises(AcceptanceScopeError, match="INVALID"):
        receipts.read("one")


def test_redirected_or_relative_cli_path_rejected(package, tmp_path):
    for path in (Path("codex.exe"), tmp_path / "link"):
        if path.is_absolute():
            path.symlink_to(package[1])
        with pytest.raises(AcceptanceScopeError):
            activate(package, codex_exe=path)


def test_receipt_exclusive_claim_distinguishes_replay_from_creation(tmp_path):
    receipts = ScopeReceipts(tmp_path, tmp_path / "receipts", {"synthetic": True}, "INERT")
    assert receipts.put("attempt", {"same": True}) is True
    other_process = ScopeReceipts(tmp_path, tmp_path / "receipts", {"synthetic": True}, "INERT")
    assert other_process.put("attempt", {"same": True}) is False


def test_partial_receipt_never_becomes_retry_permission(tmp_path):
    receipts = ScopeReceipts(tmp_path, tmp_path / "receipts", {"synthetic": True}, "INERT")
    receipts.directory.mkdir()
    receipts._path("attempt").write_bytes(b'{"payload":')
    with pytest.raises(AcceptanceScopeError, match="INVALID"):
        receipts.put("attempt", {"same": True})


@pytest.mark.parametrize("target", ["config/launch.json", "app/wiki-private/index.html"])
def test_release_member_change_is_rejected_before_activation(package, target):
    (package[0] / target).write_text("synthetic changed file")
    with pytest.raises(AcceptanceScopeError, match="DISTRIBUTION_CHANGED"):
        activate(package)


def test_a_self_consistent_but_missing_required_phase_is_rejected(package):
    root = package[0]
    build = json.loads((root / "provenance/github-build.json").read_text())
    build["phases"] = [{"phase": "synthetic-incomplete", "status": "passed"}]
    build_hash = write(root / "provenance/github-build.json", build)
    distribution = json.loads((root / "provenance/distribution.json").read_text())
    distribution["files"]["provenance/github-build.json"] = build_hash
    digest = write(root / "provenance/distribution.json", distribution)
    release = json.loads((root / "release.json").read_text())
    release["distribution_sha256"] = digest
    release["files"]["provenance/distribution.json"] = digest
    release["files"]["provenance/github-build.json"] = build_hash
    write(root / "release.json", release)
    with pytest.raises(AcceptanceScopeError, match="PROVENANCE_MISMATCH"):
        activate(package)


def test_factory_never_initializes_or_activates_missing_scope(package):
    with pytest.raises(AcceptanceScopeError, match="ACTIVATION_REQUIRED"):
        activation.create_activated_application(expected_source=SOURCE, expected_release=RELEASE)
    assert list((package[0] / "data/owner-acceptance/platform").iterdir()) == []


def test_factory_installs_only_fixed_scope_and_never_calls_model_on_construction(package):
    from ahadiff.knowledge_platform.auth import Actor
    from ahadiff.knowledge_platform.owner_identity import ACTOR, OWNER

    root, _, _ = package
    identity = activation.load_owner_identity.return_value
    identity.device_id = "a" * 32
    identity.origin = "http://127.0.0.1:8766"
    identity.actor.return_value = Actor(
        OWNER, ACTOR, "user", frozenset({"knowledge:read", "knowledge:write", "knowledge:review"})
    )
    assert activate(package) == "activated_not_started"
    with patch("ahadiff.knowledge_platform.model.codex_runner.CodexModelRunner.run") as model:
        app = activation.create_activated_application(
            expected_source=SOURCE,
            expected_release=RELEASE,
            learning_viewer_url="http://127.0.0.1:9000/",
        )
        try:
            assert app.state.workflow.settings.auto_commit_enabled is True
            assert app.state.workflow.settings.allow_fixture is False
            assert app.state.workflow.settings.worker_id == "portable-acceptance-worker"
            assert app.state.channel_supervisor is None
            assert app.state.acceptance_port == 8766
            assert (
                app.state.domain.scope_receipts.read("activation")["policy"][
                    "maximum_model_attempts"
                ]
                == 5
            )
            model.assert_not_called()
        finally:
            app.state.domain.close()


def test_factory_verifies_the_actual_launcher_viewer_store_without_fallback(package):
    """Real launcher command + CLI state resolution; no native process/model/DPAPI."""
    from ahadiff.cli import _state_dir_for_root
    from ahadiff.knowledge_platform.auth import Actor
    from ahadiff.knowledge_platform.learning import wiki_learning
    from ahadiff.knowledge_platform.learning.backend import DurableOutbox, canonical_json
    from ahadiff.knowledge_platform.learning.entry_context import context_authority_key
    from ahadiff.knowledge_platform.owner_identity import ACTOR, OWNER

    root, _, _ = package
    directory = root / "data/owner-acceptance"
    identity = activation.load_owner_identity.return_value
    identity.device_id = "a" * 32
    identity.origin = "http://127.0.0.1:8766"
    identity.actor.return_value = Actor(
        OWNER, ACTOR, "user", frozenset({"knowledge:read", "knowledge:write", "knowledge:review"})
    )
    write(directory / "identity.json", {"synthetic": "not a credential; never decrypted"})
    write(
        directory / "launch.json",
        {
            "mode": "owner-acceptance",
            "owner_dpapi_identity": "data/owner-acceptance/identity.json",
            "platform_port": 8766,
        },
    )
    write(
        directory / "desktop.json",
        {
            "service_url": identity.origin,
            "identity_file": "identity.json",
            "identity_format": "zhizhi-owner-acceptance-dpapi-v1",
            "device_id": identity.device_id,
            "input_classification": "personal",
            "providers": [],
            "model": {"enabled": False},
        },
    )
    assert activate(package) == "activated_not_started"

    repository = Path(__file__).resolve().parents[5]
    spec = importlib.util.spec_from_file_location(
        "owner_context_assembly_launcher",
        repository / "deploy/windows-portable/runtime/launcher.py",
    )
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    launcher.ROOT = root
    stop = root / "data/stop.request"
    with (
        patch.dict(sys.modules, {"msvcrt": Mock()}),
        patch.object(launcher, "spawn") as spawn,
        patch.object(launcher, "await_health"),
        patch.object(launcher, "free_port", return_value=9876),
        patch.object(launcher.time, "sleep", side_effect=lambda _: stop.write_text("stop")),
    ):
        launcher.start({}, owner_acceptance=True, owner_model=True)
    viewer = next(call for call in spawn.call_args_list if call.args[1] == "viewer")
    command = viewer.args[0]
    workspace = Path(command[command.index("--repo-root") + 1])
    assert workspace == viewer.args[3] == directory / "desktop"
    state = _state_dir_for_root(workspace, has_git_repo=False)
    expected = state / "growth.sqlite"
    assert expected == directory / "desktop/.ahadiff/growth.sqlite"

    # A stale database in the old, incorrect parent must not be consulted or
    # adopted. The missing real viewer store remains a read-only denial.
    decoy = directory / "desktop/growth.sqlite"
    decoy.write_bytes(b"SYNTHETIC DECOY; DO NOT OPEN OR REPAIR")
    verifier = wiki_learning.verify_saved_learning_outcome
    with (
        patch.object(wiki_learning, "verify_saved_learning_outcome", wraps=verifier) as verify,
        patch("ahadiff.knowledge_platform.model.codex_runner.CodexModelRunner.run") as model,
    ):
        app = activation.create_activated_application(
            expected_source=SOURCE,
            expected_release=RELEASE,
            learning_viewer_url="http://127.0.0.1:9876/",
        )
        try:
            assert app.state.domain.outcome_verifier({}) is False
            verify.assert_called_once_with(
                expected,
                {},
                backend_url=identity.origin,
                device_id=identity.device_id,
                owner_id=OWNER,
            )
            assert not expected.exists()
            assert decoy.read_bytes() == b"SYNTHETIC DECOY; DO NOT OPEN OR REPAIR"

            # Persist through the real context/CAS/outbox producers at the
            # launched viewer's path, then read through the factory callback.
            # Only identity, transport and native launch are synthetic here.
            scope = hashlib.sha256(
                canonical_json([identity.origin, identity.device_id]).encode()
            ).hexdigest()
            authority = context_authority_key(identity.origin, identity.device_id, OWNER)
            entry = {
                "entry_id": str(uuid4()),
                "revision": 1,
                "title": "合成装配来源",
                "body": SOURCE_TEXT,
                "content_hash": hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(),
                "data_classification": "personal",
                "classification_labels": ["personal"],
            }
            backend = SimpleNamespace(outbox=DurableOutbox(state / "synthetic-outbox"))
            with (
                patch.object(wiki_learning, "_scope", return_value=scope),
                patch.object(wiki_learning, "desktop_backend", return_value=backend),
                patch.object(wiki_learning, "desktop_device_id", return_value=identity.device_id),
                patch.object(wiki_learning, "legacy_input_classification", return_value="personal"),
            ):
                row = wiki_learning._mutate(
                    expected,
                    scope,
                    authority,
                    {
                        "request_id": str(uuid4()),
                        "entry_id": entry["entry_id"],
                        "revision": 1,
                        "expected_content_hash": entry["content_hash"],
                    },
                    "create",
                )
                row = wiki_learning._mutate(
                    expected,
                    scope,
                    authority,
                    {
                        "request_id": str(uuid4()),
                        "context_id": row["context_id"],
                        "expected_context_version": row["context_version"],
                        "note_text": USER_TEXT,
                    },
                    "note",
                )
                snapshot = wiki_learning._snapshot(
                    expected, row, entry, row["context_version"], row["note_sha256"]
                )
            facts = snapshot["payload"]
            assert app.state.domain.outcome_verifier(facts) is True
            assert app.state.domain.outcome_verifier({**facts, "device_id": "other"}) is False
            assert app.state.domain.outcome_verifier({**facts, "user_text": "other"}) is False
            assert decoy.read_bytes() == b"SYNTHETIC DECOY; DO NOT OPEN OR REPAIR"
            model.assert_not_called()
        finally:
            app.state.domain.close()
