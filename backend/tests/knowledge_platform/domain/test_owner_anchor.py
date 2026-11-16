"""Real-file fixtures with synthetic DPAPI/binding adapters, not Windows proof.

No native DPAPI, model invocation, runtime activation or real credential is used.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ahadiff.knowledge_platform import owner_anchor as anchor
from ahadiff.knowledge_platform import owner_identity as owner
from ahadiff.knowledge_platform import owner_acceptance
from ahadiff.knowledge_platform.acceptance_model import ScopeReceipts, AcceptanceScopeError

TOKEN = "S" * 64
DEVICE = "a" * 32


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    approved = tmp_path / "approved"
    (approved / "data").mkdir(parents=True)
    roots = []
    for n in (18, 19, 20):
        root = approved / "git-delivery/releases" / f"abcdef123456-{n}-1"
        (root / "data").mkdir(parents=True)
        (root / "release.json").write_text(json.dumps({"source_commit": "abcdef123456" + "0" * 28,
                                                       "release_id": root.name}))
        roots.append(root)
    selected = [roots[0]]
    monkeypatch.setattr(owner, "APPROVED_ROOT", str(approved))
    monkeypatch.setattr(owner, "_acceptance_root", lambda: selected[0])
    monkeypatch.setattr(owner.secrets, "token_urlsafe", lambda _: TOKEN)
    monkeypatch.setattr(owner.secrets, "token_hex", lambda _: DEVICE)
    marker = b"SYNTHETIC_DPAPI_NOT_WINDOWS:"

    def dpapi(data, *, entropy_bytes, decrypt=False):
        assert entropy_bytes == owner.ENTROPY
        if decrypt:
            assert data.startswith(marker)
            return data[len(marker):]
        return marker + data

    monkeypatch.setattr(owner, "protect_current_user", dpapi)
    assert owner.initialize_owner_identity(8766) == "initialized"
    old_receipts = roots[0] / "data/owner-acceptance/acceptance-receipts"
    old_receipts.mkdir()
    (old_receipts / "spent.json").write_bytes(b'{"fixture":"old spent run must remain unchanged"}\n')
    before = snapshot(roots[0])

    def decrypt_only(data, *, entropy_bytes, decrypt=False):
        assert decrypt is True, "Reuse must not create or re-protect an identity"
        return dpapi(data, entropy_bytes=entropy_bytes, decrypt=True)

    protection = Mock(side_effect=decrypt_only)
    monkeypatch.setattr(owner, "protect_current_user", protection)
    new_token = Mock(side_effect=AssertionError("New token forbidden"))
    new_device = Mock(side_effect=AssertionError("New device forbidden"))
    monkeypatch.setattr(owner.secrets, "token_urlsafe", new_token)
    monkeypatch.setattr(owner.secrets, "token_hex", new_device)

    def verified(root, source, release):
        # Explicit fixture substitution: distribution verification is not claimed.
        assert root in roots and release == root.name
        assert source == "abcdef123456" + "0" * 28
        assert json.loads((root / "release.json").read_text())["release_id"] == release
        return {"source_commit": source, "release_id": release, "fixture_binding": True}

    binding = Mock(side_effect=verified)
    monkeypatch.setattr(owner_acceptance, "verified_binding", binding)
    selected[0] = roots[1]
    return SimpleNamespace(approved=approved, old=roots[0], current=roots[1], next=roots[2],
                           selected=selected, before=before, protection=protection,
                           new_token=new_token, new_device=new_device, binding=binding)


def reuse(tree, **kwargs):
    return anchor.reuse_owner_identity(approved=True, new_run=True,
                                       anchor_release=tree.old.name, **kwargs)


def test_reuse_preserves_anchor_ciphertext_identity_receipts_and_creates_no_grant(tree):
    assert reuse(tree) == "identity_reused_new_run_not_activated"
    directory = tree.current / "data/owner-acceptance"
    loaded = owner.load_owner_identity(directory / "identity.json")
    assert (loaded.token, loaded.device_id, loaded.port) == (TOKEN, DEVICE, 8766)
    assert snapshot(tree.old) == tree.before
    record = json.loads(anchor.registry_path().read_text())
    assert record["identity_sha256"] == hashlib.sha256(tree.before["data/owner-acceptance/identity.json"]).hexdigest()
    reference = json.loads((directory / "identity.json").read_text())
    assert reference == {"format": anchor.REFERENCE, "anchor": record, "run_release": tree.current.name}
    assert set(p.name for p in directory.iterdir()) == {"identity.json", "launch.json", "desktop.json", "platform", "desktop"}
    assert not list((directory / "platform").iterdir()) and not list((directory / "desktop").iterdir())
    assert json.loads((directory / "desktop.json").read_text())["model"] == {"enabled": False}
    assert json.loads((directory / "desktop.json").read_text())["providers"] == []
    reservation = anchor._runs(record, loaded).read("run:" + tree.current.name)
    assert reservation["budget_created"] is False and reservation["old_budget_replenished"] is False
    assert reservation["previous_anchor_run"] == tree.old.name
    ciphertext = json.loads(tree.before["data/owner-acceptance/identity.json"])["protected"].encode()
    for root in [tree.current, tree.approved / "data"]:
        for content in snapshot(root).values():
            assert TOKEN.encode() not in content and ciphertext not in content
    tree.new_token.assert_not_called()
    tree.new_device.assert_not_called()
    assert tree.protection.call_count > 0
    assert all(call.kwargs["decrypt"] is True for call in tree.protection.call_args_list)


@pytest.mark.parametrize("approved,new_run", [(False, False), (False, True), (True, False), (1, True), (True, 1)])
def test_both_exact_explicit_approvals_required_before_reads_or_writes(tree, approved, new_run):
    before = snapshot(tree.approved)
    with pytest.raises(owner.OwnerIdentityError, match="EXPLICIT_NEW_RUN_REQUIRED"):
        anchor.reuse_owner_identity(approved=approved, new_run=new_run, anchor_release=tree.old.name)
    assert snapshot(tree.approved) == before
    tree.binding.assert_not_called()
    tree.protection.assert_not_called()


def test_repeat_reuse_is_idempotent_but_deleted_current_data_cannot_recreate_run(tree):
    reuse(tree)
    before = snapshot(tree.approved)
    assert anchor.reuse_owner_identity(approved=True, new_run=True) == "already_reused"
    assert snapshot(tree.approved) == before
    shutil.rmtree(tree.current / "data")
    (tree.current / "data").mkdir()
    with pytest.raises(owner.OwnerIdentityError, match="RUN_ALREADY_RESERVED"):
        anchor.reuse_owner_identity(approved=True, new_run=True)
    assert not (tree.current / "data/owner-acceptance").exists()
    assert snapshot(tree.old) == tree.before


def test_fixed_registry_conflicting_selection_and_identity_tampering_fail_closed(tree):
    reuse(tree)
    registry_bytes = anchor.registry_path().read_bytes()
    tree.selected[0] = tree.next
    with pytest.raises(owner.OwnerIdentityError, match="FIXED_ANCHOR_CONFLICT"):
        anchor.reuse_owner_identity(approved=True, new_run=True, anchor_release=tree.current.name)
    assert anchor.registry_path().read_bytes() == registry_bytes
    path = tree.old / "data/owner-acceptance/identity.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(owner.OwnerIdentityError, match="ANCHOR_CHANGED"):
        anchor.reuse_owner_identity(approved=True, new_run=True)
    assert not (tree.next / "data/owner-acceptance").exists()


@pytest.mark.parametrize("bad", [None, "../outside", "ABCDEF123456-18-1", "abcdef123456-18-1\n", "abcdef123456-999-1"])
def test_bad_or_missing_first_anchor_never_creates_registry_or_state(tree, bad):
    with pytest.raises((owner.OwnerIdentityError, AcceptanceScopeError, OSError)):
        anchor.reuse_owner_identity(approved=True, new_run=True, anchor_release=bad)
    assert not anchor.registry_path().exists()
    assert not (tree.current / "data/owner-acceptance").exists()


def test_missing_anchor_identity_and_same_release_fail_closed(tree):
    tree.selected[0] = tree.old
    with pytest.raises(owner.OwnerIdentityError, match="DISTINCT_RELEASE"):
        reuse(tree)
    tree.selected[0] = tree.current
    (tree.old / "data/owner-acceptance/identity.json").unlink()
    with pytest.raises((owner.OwnerIdentityError, AcceptanceScopeError, OSError)):
        reuse(tree)
    assert not anchor.registry_path().exists()


@pytest.mark.parametrize("target", ["identity", "registry", "current"])
def test_symlink_paths_are_not_followed(tree, target):
    if target == "identity":
        path = tree.old / "data/owner-acceptance/identity.json"
        saved = path.with_suffix(".saved")
        path.rename(saved)
        path.symlink_to(saved)
    elif target == "registry":
        path = anchor.registry_path()
        saved = tree.approved / "data/foreign.json"
        saved.write_text("{}")
        path.symlink_to(saved)
    else:
        path = tree.current / "data/owner-acceptance"
        path.symlink_to(tree.old / "data/owner-acceptance", target_is_directory=True)
    with pytest.raises(owner.OwnerIdentityError, match="PATH_REDIRECTED"):
        reuse(tree)


def test_reparse_attribute_fixture_fails_before_decryption(tree, monkeypatch):
    path = tree.old / "data/owner-acceptance/identity.json"
    original = Path.lstat
    def lstat(candidate, *args, **kwargs):
        if candidate == path:
            return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(owner.OwnerIdentityError, match="PATH_REDIRECTED"):
        reuse(tree)
    tree.protection.assert_not_called()


def test_existing_legacy_still_loads_but_new_identity_is_forbidden_after_registry(tree):
    # Fixture bootstrap above exercised the first legacy initialization.
    tree.selected[0] = tree.old
    assert owner.initialize_owner_identity(8766) == "already_initialized"
    tree.selected[0] = tree.current
    reuse(tree)
    tree.selected[0] = tree.next
    with pytest.raises(owner.OwnerIdentityError, match="FIXED_ANCHOR_REUSE_REQUIRED"):
        owner.initialize_owner_identity(8766)
    assert not (tree.next / "data/owner-acceptance").exists()
    tree.new_token.assert_not_called()
    tree.new_device.assert_not_called()


def test_separate_runs_do_not_replenish_previous_scope_receipts(tree):
    reuse(tree)
    loaded = owner.load_owner_identity(tree.current / "data/owner-acceptance/identity.json")
    base = tree.approved / "data/owner-runs"
    prior_directory = base / tree.current.name
    prior = anchor.scope_receipts(tree.current, "pi-receipts", {"run": tree.current.name}, loaded.token)
    assert prior.directory == prior_directory / "pi-receipts"
    assert prior.put("model-attempt:1", {"spent": True}) is True
    before = snapshot(prior_directory)
    tree.selected[0] = tree.next
    assert anchor.reuse_owner_identity(approved=True, new_run=True) == "identity_reused_new_run_not_activated"
    next_directory = base / tree.next.name
    fresh = anchor.scope_receipts(tree.next, "pi-receipts", {"run": tree.next.name}, loaded.token)
    assert fresh.directory == next_directory / "pi-receipts"
    assert fresh.read("model-attempt:1") is None
    assert prior.read("model-attempt:1") == {"spent": True}
    assert prior.put("model-attempt:1", {"spent": True}) is False
    with pytest.raises(AcceptanceScopeError, match="CONFLICT"):
        prior.put("model-attempt:1", {"spent": False})
    assert snapshot(prior_directory) == before
    assert snapshot(tree.old) == tree.before


@pytest.mark.parametrize("flags", [[], ["--approve-owner-identity-reuse"], ["--new-acceptance-run"]])
def test_cli_requires_both_flags_without_touching_files(tree, monkeypatch, flags):
    before = snapshot(tree.approved)
    monkeypatch.setattr(sys, "argv", ["owner-anchor", *flags])
    with pytest.raises(SystemExit) as error:
        anchor.main()
    assert error.value.code == 2
    assert snapshot(tree.approved) == before


@pytest.mark.parametrize("name", ["acceptance-receipts", "pi-receipts"])
def test_scope_ledgers_stay_outside_current_release_and_missing_ledger_fails_closed(tree, name):
    reuse(tree)
    ledger = anchor.scope_receipts(tree.current, name, {"run": tree.current.name}, TOKEN)
    assert ledger.directory == tree.approved / "data/owner-runs" / tree.current.name / name
    assert not list(ledger.directory.iterdir())
    assert ledger.put("run", {"spent": True}) is True
    original_reference = (tree.current / "data/owner-acceptance/identity.json").read_bytes()
    before = snapshot(ledger.directory)
    shutil.rmtree(tree.current / "data")
    with pytest.raises(owner.OwnerIdentityError, match="RUN_ALREADY_RESERVED"):
        reuse(tree)
    assert snapshot(ledger.directory) == before
    # Explicit fixture restoration of the reference cannot delete the durable ledger.
    (tree.current / "data/owner-acceptance").mkdir(parents=True)
    (tree.current / "data/owner-acceptance/identity.json").write_bytes(original_reference)
    assert anchor.scope_receipts(tree.current, name, {"run": tree.current.name}, TOKEN).read("run") == {"spent": True}
    shutil.rmtree(ledger.directory)
    with pytest.raises(owner.OwnerIdentityError, match="RUN_LEDGER_MISSING"):
        anchor.scope_receipts(tree.current, name, {"run": tree.current.name}, TOKEN)
    assert not ledger.directory.exists()


def test_legacy_scope_receipts_stay_in_original_release_and_invalid_kind_is_rejected(tree):
    tree.selected[0] = tree.old
    ledger = anchor.scope_receipts(tree.old, "acceptance-receipts", {"legacy": True}, TOKEN)
    assert ledger.directory == tree.old / "data/owner-acceptance/acceptance-receipts"
    assert anchor.run_annotation(tree.old) == {}
    with pytest.raises(owner.OwnerIdentityError, match="LEDGER_KIND_INVALID"):
        anchor.scope_receipts(tree.old, "../elsewhere", {}, TOKEN)
    assert snapshot(tree.old) == tree.before


def test_new_run_annotation_reports_separate_run_not_budget_replenishment(tree):
    reuse(tree)
    annotation = anchor.run_annotation(tree.current)["acceptance_run"]
    assert annotation["kind"] == "explicit_new_acceptance_run"
    assert annotation["release"] == tree.current.name
    assert annotation["old_budget_replenished"] is False
    assert annotation["anchor"] == json.loads(anchor.registry_path().read_text())
    assert TOKEN not in json.dumps(annotation)


@pytest.mark.parametrize("change", [{"version": 2}, {"extra": "no"}, {"identity_sha256": "0" * 64}])
def test_invalid_registry_does_not_create_new_run(tree, change):
    reuse(tree)
    path = anchor.registry_path()
    value = json.loads(path.read_text()) | change
    path.write_text(json.dumps(value))
    tree.selected[0] = tree.next
    with pytest.raises(owner.OwnerIdentityError):
        anchor.reuse_owner_identity(approved=True, new_run=True)
    assert not (tree.next / "data/owner-acceptance").exists()


def test_cli_output_has_no_credentials_and_does_not_claim_activation(tree, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["owner-anchor", "--approve-owner-identity-reuse", "--new-acceptance-run", "--anchor-release", tree.old.name])
    anchor.main()
    raw = capsys.readouterr().out
    assert TOKEN not in raw and DEVICE not in raw
    assert json.loads(raw) == {"status": "identity_reused_new_run_not_activated", "credentials_created": False,
                              "budget_created": False, "live_e2e": "not_run"}


@pytest.mark.parametrize("which", ["current", "anchor"])
def test_verified_binding_failure_creates_no_registry_or_run(tree, which):
    original = tree.binding.side_effect
    def denied(root, source, release):
        if root == (tree.current if which == "current" else tree.old):
            raise AcceptanceScopeError("ACCEPTANCE_PROVENANCE_INVALID")
        return original(root, source, release)
    tree.binding.side_effect = denied
    before = snapshot(tree.approved)
    with pytest.raises(AcceptanceScopeError, match="PROVENANCE_INVALID"):
        reuse(tree)
    assert snapshot(tree.approved) == before
    tree.protection.assert_not_called()


@pytest.mark.parametrize("change", [{"run_release": "abcdef123456-20-1"}, {"extra": True}, {"anchor": {}}])
def test_reference_document_cannot_rebind_release_or_anchor(tree, change):
    reuse(tree)
    path = tree.current / "data/owner-acceptance/identity.json"
    document = json.loads(path.read_text()) | change
    path.write_text(json.dumps(document))
    with pytest.raises(owner.OwnerIdentityError, match="REFERENCE_MISMATCH"):
        owner.load_owner_identity(path)
    assert snapshot(tree.old) == tree.before


def test_release_manifest_or_run_reservation_changes_invalidate_reference(tree):
    reuse(tree)
    identity_path = tree.current / "data/owner-acceptance/identity.json"
    manifest = tree.current / "release.json"
    before = manifest.read_bytes()
    manifest.write_bytes(before + b" ")
    with pytest.raises(owner.OwnerIdentityError, match="RUN_RESERVATION_REQUIRED"):
        owner.load_owner_identity(identity_path)
    manifest.write_bytes(before)
    record = json.loads(anchor.registry_path().read_text())
    runs = anchor._runs(record, owner.load_owner_identity(identity_path))
    path = runs._path("run:" + tree.current.name)
    payload = json.loads(path.read_text())
    payload["payload"]["value"]["budget_created"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(AcceptanceScopeError, match="RECEIPT_INVALID"):
        owner.load_owner_identity(identity_path)
    assert snapshot(tree.old) == tree.before


@pytest.mark.parametrize("which", ["current", "anchor", "both"])
def test_full_distribution_manifest_larger_than_identity_limit_is_supported(tree, which):
    roots = [tree.current, tree.old] if which == "both" else [tree.current if which == "current" else tree.old]
    for root in roots:
        path = root / "release.json"
        document = json.loads(path.read_text())
        document["fixture_distribution_inventory"] = "x" * 40_000
        path.write_text(json.dumps(document))
        assert path.stat().st_size > 32768
    assert reuse(tree) == "identity_reused_new_run_not_activated"
    assert owner.load_owner_identity(tree.current / "data/owner-acceptance/identity.json").token == TOKEN


def test_bootstrap_and_reuse_share_configuration_lock(tree):
    import portalocker

    with anchor.configuration_lock():
        with pytest.raises(portalocker.exceptions.LockException):
            anchor.reuse_owner_identity(approved=True, new_run=True, anchor_release=tree.old.name)
        with pytest.raises(portalocker.exceptions.LockException):
            owner.initialize_owner_identity(8766)
    assert not anchor.registry_path().exists()
    tree.new_token.assert_not_called()


def test_reference_service_health_names_current_run_without_credentials(tree, monkeypatch):
    import asyncio
    import uvicorn
    from ahadiff.knowledge_platform.__main__ import main
    from starlette.requests import Request

    anchor.reuse_owner_identity(approved=True, new_run=True, anchor_release=tree.old.name)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: apps.append(app))
    monkeypatch.setattr(sys, "argv", ["knowledge", "--state-directory",
        str(tree.current / "data/owner-acceptance/platform"), "--owner-dpapi-identity",
        str(tree.current / "data/owner-acceptance/identity.json"), "--owner", owner.OWNER,
        "--owner-browser-actor", owner.ACTOR])
    main()
    app = apps[0]
    endpoint = next(route.endpoint for route in app.routes if route.path == "/health")
    response = asyncio.run(endpoint(Request({"type": "http", "app": app})))
    record = json.loads(response.body)
    assert record["owner_runtime_binding"] == {"release_id": tree.current.name,
        "source_commit": "abcdef123456" + "0" * 28}
    assert TOKEN not in response.body.decode() and DEVICE not in response.body.decode()
    assert record["worker_enabled"] is False
    app.state.domain.close()


_REAL_VERIFIED_BINDING = owner_acceptance.verified_binding


def test_reuse_verifies_actual_large_distribution_and_rejects_changed_code(tree, monkeypatch):
    """Real manifest/hash verification plus synthetic DPAPI, not a GitHub build."""
    from ahadiff.knowledge_platform.acceptance_model import material_manifest

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    for root in (tree.old, tree.current):
        source = "abcdef123456" + "0" * 28
        run = root.name.split("-")[1]
        (root / "runtime").mkdir()
        files = {}
        for n in range(450):
            name = f"app/synthetic-manifest-artifact-{n:04d}.txt"
            path = root / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"SYNTHETIC INERT FIXTURE")
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        files["provenance/github-build.json"] = write(root / "provenance/github-build.json", {
            "schema_version": 1, "source_commit": source, "run_id": run, "run_attempt": "1",
            "repository": "sep-zxy/zhizhi-platform", "runner": "github-hosted",
            "event": "workflow_dispatch", "status": "ci_candidate_built", "windows_e2e": "not_run",
            "final_delivery": False, "phases": [{"phase": phase, "status": "passed"} for phase in (
                "backend-isolated-fixtures", "viewer-learning-browser-fixtures",
                "private-wiki-browser-fixtures", "assemble-windows-product")]})
        distribution = write(root / "provenance/distribution.json", {"target": "windows-x64", "files": files})
        config = write(root / "config/launch.json", {"fixture": True})
        write(root / "release.json", {"source_commit": source, "release_id": root.name,
            "distribution_sha256": distribution, "github_run_id": run, "github_run_attempt": "1",
            "final_delivery": False, "files": {**files, "config/launch.json": config,
                "provenance/distribution.json": distribution}})
        assert (root / "release.json").stat().st_size > 32768
    monkeypatch.setattr(owner_acceptance, "verified_binding", _REAL_VERIFIED_BINDING)
    changed = tree.current / "app/synthetic-manifest-artifact-0000.txt"
    changed.write_bytes(b"CHANGED FIXTURE")
    with pytest.raises(AcceptanceScopeError, match="DISTRIBUTION_CHANGED"):
        reuse(tree)
    assert not anchor.registry_path().exists()
    changed.write_bytes(b"SYNTHETIC INERT FIXTURE")
    assert reuse(tree) == "identity_reused_new_run_not_activated"
    record = json.loads(anchor.registry_path().read_text())
    ledger = anchor._runs(record, owner.load_owner_identity(tree.current / "data/owner-acceptance/identity.json"))
    reservation = ledger.read("run:" + tree.current.name)
    assert reservation["binding"]["policy"] == material_manifest()
    assert reservation["budget_created"] is False
