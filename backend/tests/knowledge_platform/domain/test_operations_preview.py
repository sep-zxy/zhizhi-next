"""Real synthetic SQLite/Git backup checks, never a live restore or publication."""
import hashlib
import json
import sqlite3

import pytest

from ahadiff.knowledge_platform.operations_preview import (
    PreviewError, inspect_backup, restore_plan, verify_publication_manifest,
)
from ahadiff.knowledge_platform.operations_preview.__main__ import main
from ahadiff.knowledge_platform.publish.projection import project_git_revision
from ahadiff.knowledge_platform.publish.revalidate import bind_domain_publication


@pytest.fixture
def snapshot(domain, owner, writer, source, proposal, tmp_path):
    domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="operator-test-apply")
    destination = tmp_path / "snapshot"
    domain.backup(destination, actor=writer)
    return destination


def manifest(snapshot):
    return json.loads((snapshot / "manifest.json").read_text())


def write_manifest(snapshot, value):
    (snapshot / "manifest.json").write_text(json.dumps(value))


def files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_real_backup_inspection_and_plan_are_readonly(snapshot):
    before = files(snapshot)
    report = inspect_backup(snapshot)
    assert report["object_count"] == 1 and report["entry_revision_count"] == 1
    assert report["restore_executed"] is False and report["recovery_proven"] is False
    plan = restore_plan(snapshot, dry_run=True)
    assert plan["dry_run"] and plan["steps"][-1] == "request_explicit_cutover_approval"
    assert files(snapshot) == before


def test_backup_database_handle_is_released(snapshot):
    database = snapshot / "platform.db"
    content = database.read_bytes()
    # Windows rejects replacement/deletion while the backup writer owns a handle.
    database.unlink()
    database.write_bytes(content)
    assert inspect_backup(snapshot)["object_count"] == 1


def test_dry_run_required_even_in_python(snapshot):
    with pytest.raises(PreviewError, match="DRY_RUN_REQUIRED"):
        restore_plan(snapshot, dry_run=False)


@pytest.mark.parametrize("component", ["platform.db", "vault.git/HEAD", "manifest.json"])
def test_missing_component_blocks(snapshot, component):
    (snapshot / component).unlink()
    with pytest.raises(PreviewError):
        inspect_backup(snapshot)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecars_refused_without_checkpoint(snapshot, suffix):
    sidecar = snapshot / ("platform.db" + suffix)
    sidecar.write_bytes(b"PRIVATE_SIDECAR")
    before = files(snapshot)
    with pytest.raises(PreviewError, match="SIDECAR"):
        inspect_backup(snapshot)
    assert files(snapshot) == before


def test_database_corruption_blocks(snapshot):
    with (snapshot / "platform.db").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(PreviewError, match="DATABASE_HASH"):
        inspect_backup(snapshot)


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "%2e%2e/outside", "a\\b"])
def test_manifest_paths_cannot_escape(snapshot, path):
    data = manifest(snapshot)
    data["objects"][0]["path"] = path
    write_manifest(snapshot, data)
    with pytest.raises(ValueError):
        inspect_backup(snapshot)


def test_source_reference_must_match_even_if_db_hash_updated(snapshot):
    db = snapshot / "platform.db"
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM kp_records WHERE kind='source'")
    connection.close()
    data = manifest(snapshot)
    data["database_sha256"] = hashlib.sha256(db.read_bytes()).hexdigest()
    write_manifest(snapshot, data)
    with pytest.raises(PreviewError, match="SOURCE_REFERENCE"):
        inspect_backup(snapshot)


def test_tampered_object_blocks(snapshot):
    obj = manifest(snapshot)["objects"][0]["path"]
    (snapshot / "objects" / obj).write_bytes(b"changed")
    with pytest.raises(PreviewError, match="OBJECT_HASH"):
        inspect_backup(snapshot)


@pytest.mark.parametrize("kind", ["object", "parent", "git-alternate"])
def test_links_and_external_git_refused(snapshot, tmp_path, kind):
    if kind == "object":
        obj = snapshot / "objects" / manifest(snapshot)["objects"][0]["path"]
        outside = tmp_path / "outside"
        outside.write_bytes(obj.read_bytes())
        obj.unlink()
        obj.symlink_to(outside)
    elif kind == "parent":
        link = tmp_path / "link"
        link.symlink_to(snapshot, target_is_directory=True)
        snapshot = link
    else:
        (snapshot / "vault.git/objects/info/alternates").write_text("/outside")
    with pytest.raises(PreviewError):
        inspect_backup(snapshot)


def test_unlisted_object_blocks(snapshot):
    (snapshot / "objects/extra").write_bytes(b"private")
    with pytest.raises(PreviewError, match="OBJECT_SET"):
        inspect_backup(snapshot)


def test_cli_sanitizes_errors_and_never_accepts_restore_without_dryrun(snapshot, capsys):
    assert main(["backup-inspect", str(snapshot)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified_snapshot"
    (snapshot / "manifest.json").write_text("PRIVATE_DATA malformed")
    assert main(["backup-inspect", str(snapshot)]) == 2
    result = capsys.readouterr().out
    assert "PRIVATE_DATA" not in result and str(snapshot) not in result
    with pytest.raises(SystemExit):
        main(["restore-plan", str(snapshot)])


def test_existing_manifest_revalidated_with_real_domain(domain, owner, writer, proposal, tmp_path):
    domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="public-preview-apply")
    repository = domain.writer.repository
    readonly, resolver = bind_domain_publication(domain.store.path, owner.owner_id, repository)
    try:
        projection = project_git_revision(repository, domain.writer.head(), tmp_path / "staging",
                                          policy={}, publication_resolver=resolver)
    finally:
        readonly.close()
    assert projection["public_manifest"]["counts"]["content_pages"] == 1
    manifest_path = tmp_path / "public-manifest.json"
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    manifest_path.write_text(json.dumps(projection["public_manifest"]))
    report = verify_publication_manifest(manifest_path, repository=repository,
                                        database=domain.store.path, owner_id=owner.owner_id,
                                        policy_path=policy)
    assert report["site_build_executed"] is False and report["deploy_executed"] is False
    data = projection["public_manifest"]
    data["build_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify_publication_manifest(manifest_path, repository=repository,
                                    database=domain.store.path, owner_id=owner.owner_id,
                                    policy_path=policy)


def test_source_rights_withdrawal_blocks_old_manifest(
    domain, owner, writer, source, proposal, tmp_path
):
    domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="withdraw-test")
    repo = domain.writer.repository
    readonly, resolver = bind_domain_publication(domain.store.path, owner.owner_id, repo)
    try:
        projection = project_git_revision(repo, domain.writer.head(), tmp_path / "staging",
                                          policy={}, publication_resolver=resolver)
    finally:
        readonly.close()
    assert projection["public_manifest"]["counts"]["content_pages"] == 1
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(projection["public_manifest"]))
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    with domain.store.transaction() as connection:
        record = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
        record["rights"] = "unknown"
        domain.store.put(connection, "source", source["source_id"], record)
    with pytest.raises(ValueError):
        verify_publication_manifest(manifest_path, repository=repo,
                                    database=domain.store.path, owner_id=owner.owner_id,
                                    policy_path=policy)


def test_external_git_config_is_rejected(snapshot):
    with (snapshot / "vault.git/config").open("a") as stream:
        stream.write("\n[include]\npath = /outside-private-config\n")
    with pytest.raises(PreviewError, match="EXTERNAL_GIT_CONFIG"):
        inspect_backup(snapshot)


def test_missing_history_object_is_rejected(snapshot):
    revision = manifest(snapshot)["git_revision"]
    (snapshot / "vault.git/objects" / revision[:2] / revision[2:]).unlink()
    with pytest.raises(Exception):
        inspect_backup(snapshot)


def test_duplicate_manifest_keys_fail_closed(snapshot):
    (snapshot / "manifest.json").write_text('{"complete":true,"complete":true}')
    with pytest.raises(PreviewError, match="DUPLICATE_JSON_KEY"):
        inspect_backup(snapshot)


def refresh_database_hash(snapshot):
    data = manifest(snapshot)
    data["database_sha256"] = hashlib.sha256((snapshot / "platform.db").read_bytes()).hexdigest()
    write_manifest(snapshot, data)


@pytest.mark.parametrize("field,value", [
    ("path", "entries/missing.md"), ("revision", 999), ("owner_id", "other-owner"),
    ("data_classification", "public"), ("visibility", "private"),
])
def test_current_catalog_cannot_diverge_from_history(snapshot, field, value):
    connection = sqlite3.connect(snapshot / "platform.db")
    try:
        (data,) = connection.execute("SELECT data FROM kp_records WHERE kind='entry'").fetchone()
        catalog = json.loads(data)
        # Force a changed classification even when the fixture already is public.
        if catalog.get(field) == value:
            value = "restricted"
        catalog[field] = value
        connection.execute("UPDATE kp_records SET data=? WHERE kind='entry'", (json.dumps(catalog),))
        connection.commit()
    finally:
        connection.close()
    refresh_database_hash(snapshot)
    with pytest.raises(PreviewError, match="CURRENT_CATALOG"):
        inspect_backup(snapshot)


@pytest.mark.parametrize("field,value", [
    ("path", "entries/missing.md"), ("revision", 999), ("owner_id", "other-owner"),
    ("visibility", "private"), ("title", "forged title"),
])
def test_history_json_and_git_document_are_bound(snapshot, field, value):
    connection = sqlite3.connect(snapshot / "platform.db")
    try:
        (data,) = connection.execute("SELECT metadata FROM kp_entry_revisions").fetchone()
        metadata = json.loads(data)
        metadata[field] = value
        connection.execute("UPDATE kp_entry_revisions SET metadata=?", (json.dumps(metadata),))
        connection.commit()
    finally:
        connection.close()
    refresh_database_hash(snapshot)
    with pytest.raises(PreviewError, match="HISTORY_(METADATA|DOCUMENT)_MISMATCH"):
        inspect_backup(snapshot)


def test_current_catalog_sql_revision_must_match(snapshot):
    connection = sqlite3.connect(snapshot / "platform.db")
    try:
        connection.execute("UPDATE kp_records SET revision=999 WHERE kind='entry'")
        connection.commit()
    finally:
        connection.close()
    refresh_database_hash(snapshot)
    with pytest.raises(PreviewError, match="CURRENT_CATALOG"):
        inspect_backup(snapshot)


def test_duplicate_identical_content_object_manifest_is_supported(snapshot):
    data = manifest(snapshot)
    data["objects"].append(dict(data["objects"][0]))
    write_manifest(snapshot, data)
    assert inspect_backup(snapshot)["object_count"] == 1
