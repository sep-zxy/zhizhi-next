"""Real local Git/SQLite projection fixtures, not Quartz build or public deployment."""
import json

import pytest

from ahadiff.knowledge_platform.operations_preview import PreviewError
from ahadiff.knowledge_platform.operations_preview.publication import (
    eligibility, publish_preview, publish_build_plan,
)
from ahadiff.knowledge_platform.operations_preview.__main__ import main


@pytest.fixture
def publication(domain, owner, writer, proposal, tmp_path_factory):
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="preview-apply")
    output = tmp_path_factory.mktemp("isolated-publication-output")
    policy = output / "policy.json"
    policy.write_text("{}")
    return entry, output, dict(repository=domain.writer.repository, database=domain.store.path,
                               owner_id=owner.owner_id), policy


def test_current_entry_eligibility_is_summary_only(publication):
    entry, _, common, _ = publication
    result = eligibility(entry["entry_id"], **common)
    assert result["eligible"] is True
    assert entry["body"] not in json.dumps(result) and entry["title"] not in json.dumps(result)
    assert eligibility(entry["entry_id"], **{**common, "owner_id": "wrong-owner"})["eligible"] is False


def test_fixed_preview_is_deterministic_and_build_is_only_a_plan(publication):
    entry, output, common, policy = publication
    first = publish_preview(**common, revision=entry["commit_sha"], staging=output / "one",
                            policy_path=policy)
    second = publish_preview(**common, revision=entry["commit_sha"], staging=output / "two",
                             policy_path=policy)
    assert first["build_hash"] == second["build_hash"]
    assert first["counts"]["content_pages"] == 1
    assert first["deploy_executed"] is False
    assert not (output / "one/internal-manifest.json").exists()
    plan = publish_build_plan(**common, staging=output / "one", policy_path=policy)
    assert plan["status"] == "build_plan_only" and plan["site_build_executed"] is False


def test_revoked_source_cannot_be_revived_from_old_git(publication, domain, owner, source):
    entry, output, common, policy = publication
    publish_preview(**common, revision=entry["commit_sha"], staging=output / "before-withdrawal",
                    policy_path=policy)
    with domain.store.transaction() as connection:
        record = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
        record["rights"] = "unknown"
        domain.store.put(connection, "source", source["source_id"], record)
    assert eligibility(entry["entry_id"], **common)["eligible"] is False
    with pytest.raises(ValueError):
        publish_build_plan(**common, staging=output / "before-withdrawal", policy_path=policy)
    result = publish_preview(**common, revision=entry["commit_sha"], staging=output / "revoked",
                             policy_path=policy)
    assert result["counts"]["content_pages"] == 0
    assert entry["body"] not in "".join(p.read_text() for p in (output / "revoked").rglob("*.md"))


@pytest.mark.parametrize("target", ["existing", "symlink", "inside-repository", "inside-source"])
def test_preview_requires_new_nonoverlapping_unredirected_target(publication, target):
    entry, output, common, policy = publication
    staging = output / "destination"
    if target == "existing":
        staging.mkdir()
    elif target == "symlink":
        staging.symlink_to(output / "missing", target_is_directory=True)
    elif target == "inside-repository":
        staging = common["repository"] / "preview"
    else:
        staging = common["database"].parent / "preview"
    with pytest.raises(PreviewError):
        publish_preview(**common, revision=entry["commit_sha"], staging=staging, policy_path=policy)


def test_preview_never_accepts_moving_head(publication):
    _, output, common, policy = publication
    with pytest.raises(PreviewError, match="INVALID_REVISION"):
        publish_preview(**common, revision="HEAD", staging=output / "head", policy_path=policy)
    assert not (output / "head").exists()


def test_build_plan_detects_tampered_staging(publication):
    entry, output, common, policy = publication
    staging = output / "site"
    publish_preview(**common, revision=entry["commit_sha"], staging=staging, policy_path=policy)
    (staging / "index.md").write_text("tampered")
    with pytest.raises(ValueError):
        publish_build_plan(**common, staging=staging, policy_path=policy)


def test_cli_new_commands_are_real_local_operations(publication, capsys):
    entry, output, common, policy = publication
    args = ["--repository", str(common["repository"]), "--database", str(common["database"]),
            "--owner-id", common["owner_id"]]
    assert main(["eligibility", entry["entry_id"], *args]) == 0
    assert json.loads(capsys.readouterr().out)["eligible"] is True
    assert main(["publish-preview", *args, "--revision", entry["commit_sha"],
                 "--staging", str(output / "cli"), "--policy", str(policy)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "sanitized_preview_created"
    assert main(["publish-build-plan", *args, "--staging", str(output / "cli"),
                 "--policy", str(policy)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "build_plan_only"


def legacy_commit(domain):
    body = (b'---\npublish: true\ntitle: Synthetic unowned\n'
            b'created: "2026-10-22"\nupdated: "2026-10-22"\n---\nSECRET_UNOWNED_BODY\n')
    blob = domain.writer._git("hash-object", "-w", "--stdin", input_data=body).decode().strip()
    tree = domain.writer._git("mktree", input_data=f"100644 blob {blob}\tordinary.md\n".encode()).decode().strip()
    return domain.writer._git("commit-tree", tree, input_data=b"synthetic legacy\n").decode().strip()


@pytest.mark.parametrize("selected_owner", ["owner-one", "wrong-owner"])
def test_legacy_publish_flag_cannot_bypass_domain_qualification(publication, domain, selected_owner):
    _, output, common, policy = publication
    commit = legacy_commit(domain)
    with pytest.raises(PreviewError, match="NON_DOMAIN_MARKDOWN"):
        publish_preview(**{**common, "owner_id": selected_owner}, revision=commit,
                        staging=output / "unowned", policy_path=policy)
    assert not (output / "unowned").exists()


def test_legacy_staging_cannot_pass_verify_or_build_plan(publication, domain):
    from ahadiff.knowledge_platform.publish.projection import project_git_revision
    from ahadiff.knowledge_platform.operations_preview import verify_publication_manifest
    _, output, common, policy = publication
    commit = legacy_commit(domain)
    staging = output / "legacy-staging"
    project_git_revision(common["repository"], commit, staging, policy={})
    assert "SECRET_UNOWNED_BODY" in (staging / "ordinary.md").read_text()
    with pytest.raises(PreviewError, match="NON_DOMAIN_MARKDOWN"):
        publish_build_plan(**common, staging=staging, policy_path=policy)
    with pytest.raises(PreviewError, match="NON_DOMAIN_MARKDOWN"):
        verify_publication_manifest(staging / "publish-manifest.json", **common, policy_path=policy)


def test_git_environment_cannot_redirect_verified_repository(publication, monkeypatch):
    entry, output, common, policy = publication
    monkeypatch.setenv("GIT_DIR", str(output / "not-the-verified-repository"))
    monkeypatch.setenv("GIT_WORK_TREE", str(output))
    result = publish_preview(**common, revision=entry["commit_sha"], staging=output / "safe",
                             policy_path=policy)
    assert result["counts"]["content_pages"] == 1


def test_copy_failure_cleans_only_owned_directory_and_allows_retry(publication, monkeypatch):
    import shutil
    from ahadiff.knowledge_platform.operations_preview import publication as module
    entry, output, common, policy = publication
    original = shutil.copytree
    def fail(source, destination, *args, **kwargs):
        if args:
            return original(source, destination, *args, **kwargs)
        def failed_copy(*args, **copy_kwargs):
            raise OSError("synthetic full device")
        return original(source, destination, copy_function=failed_copy, **kwargs)
    monkeypatch.setattr(module.shutil, "copytree", fail)
    staging = output / "interrupted"
    with pytest.raises(shutil.Error, match="synthetic full device"):
        publish_preview(**common, revision=entry["commit_sha"], staging=staging, policy_path=policy)
    assert not staging.exists()
    monkeypatch.setattr(module.shutil, "copytree", original)
    assert publish_preview(**common, revision=entry["commit_sha"], staging=staging,
                           policy_path=policy)["status"] == "sanitized_preview_created"


def test_copy_failure_never_deletes_replaced_directory(publication, monkeypatch):
    from ahadiff.knowledge_platform.operations_preview import publication as module
    entry, output, common, policy = publication
    staging = output / "replaced"
    def replace_then_fail(source, destination, **kwargs):
        destination.rename(output / "owned-moved")
        destination.mkdir()
        (destination / "keep").write_text("external replacement")
        raise OSError("replacement")
    monkeypatch.setattr(module.shutil, "copytree", replace_then_fail)
    with pytest.raises(OSError):
        publish_preview(**common, revision=entry["commit_sha"], staging=staging, policy_path=policy)
    assert (staging / "keep").read_text() == "external replacement"
