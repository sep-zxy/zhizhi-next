"""C20 synthetic Git/SQLite and fake-process tests; never execute Quartz."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ahadiff.knowledge_platform.operations_preview import build
from ahadiff.knowledge_platform.operations_preview.checks import PreviewError
from ahadiff.knowledge_platform.operations_preview.projection import project_domain_revision
from ahadiff.knowledge_platform.publish.revalidate import bind_domain_publication


@pytest.fixture
def inputs(domain, owner, writer, proposal, tmp_path, tmp_path_factory, monkeypatch):
    domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="c20-build")
    staging = tmp_path / "staging"
    readonly, resolver = bind_domain_publication(domain.store.path, owner.owner_id, domain.writer.repository)
    try:
        project_domain_revision(domain.writer.repository, domain.writer.head(), staging,
                                owner_id=owner.owner_id, policy={}, publication_resolver=resolver)
    finally:
        readonly.close()
    policy, assets = tmp_path / "policy.json", tmp_path / "template-assets.json"
    policy.write_text("{}")
    assets.write_text('{"assets":{}}')
    # Stub the policy guard, not CI environment. This never authorizes a real build.
    monkeypatch.setattr(build, "_hosted_runner", lambda: None)
    monkeypatch.setattr(build, "_fonts", lambda site: None)
    monkeypatch.setattr(build.shutil, "which", lambda name: "/fake/node")
    return dict(manifest_path=staging / "publish-manifest.json", staging=staging,
                output=tmp_path_factory.mktemp("c20-output") / "site", template_assets=assets,
                public_base_url="https://example.com", repository=domain.writer.repository,
                database=domain.store.path, owner_id=owner.owner_id, policy_path=policy)


def fake_build(argv, **kwargs):
    assert argv[0] == "/fake/node" and argv[1:3] == ["quartz/bootstrap-cli.mjs", "build"]
    assert "shell" not in kwargs and kwargs["timeout"] == 900
    assert "OPENAI_API_KEY" not in kwargs["env"] and "NODE_OPTIONS" not in kwargs["env"]
    assert kwargs["env"]["QUARTZ_OFFLINE_PLUGINS"] == "1"
    staging, output = Path(argv[4]), Path(argv[6])
    assert not output.exists()
    manifest = json.loads((staging / "publish-manifest.json").read_text())
    output.mkdir()
    (output / "index.html").write_text("<html>Public</html>")
    for page in manifest["pages"]:
        path = output / (page["slug"] + ".html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html>Public</html>")
    (output / "publish-manifest.json").write_text(json.dumps(manifest))
    return SimpleNamespace(returncode=0)


def test_verified_fake_build_has_bounded_receipt(inputs, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_SENTINEL")
    monkeypatch.setenv("NODE_OPTIONS", "--require=private")
    monkeypatch.setattr(build.subprocess, "run", fake_build)
    receipt = build.publish_build(**inputs)
    assert receipt["status"] == "verified_site_build"
    assert receipt["site_build_executed"] and not receipt["deploy_executed"]
    assert not receipt["isolation_attested"] and receipt["revalidation_required_before_deploy"]
    assert len(receipt["artifact_hash"]) == 64
    assert str(inputs["database"]) not in json.dumps(receipt)


@pytest.mark.parametrize("failure", ["exit", "leak", "input", "revoked", "staging", "nested_manifest"])
def test_failures_never_publish_output(inputs, monkeypatch, failure, domain, owner, source):
    def fail(argv, **kwargs):
        result = fake_build(argv, **kwargs)
        if failure == "exit":
            return SimpleNamespace(returncode=1)
        if failure == "leak":
            (Path(argv[6]) / "PRIVATE_RAW.txt").write_text("private")
        if failure == "input":
            inputs["policy_path"].write_text('{"changed":true}')
        if failure == "staging":
            (Path(argv[4]) / "index.md").write_text("changed")
        if failure == "nested_manifest":
            nested = Path(argv[4]) / "nested"
            nested.mkdir()
            (nested / "publish-manifest.json").write_text("private")
        if failure == "revoked":
            with domain.store.transaction() as connection:
                record = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
                record["rights"] = "unknown"
                domain.store.put(connection, "source", source["source_id"], record)
        return result
    monkeypatch.setattr(build.subprocess, "run", fail)
    with pytest.raises(ValueError):
        build.publish_build(**inputs)
    assert not inputs["output"].exists()
    assert not list(inputs["output"].parent.glob("knowledge-build-*"))


@pytest.mark.parametrize("failure", ["existing", "overlap", "manifest", "link", "revoked"])
def test_preflight_blocks_without_process(inputs, monkeypatch, failure, domain, owner, source):
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: pytest.fail("process must not run"))
    if failure == "existing":
        inputs["output"].mkdir()
        (inputs["output"] / "old-site").write_text("preserve")
    elif failure == "overlap":
        inputs["output"] = inputs["staging"] / "nested"
    elif failure == "manifest":
        alternate = inputs["policy_path"].parent / "alternate.json"
        alternate.write_text("{}")
        inputs["manifest_path"] = alternate
    elif failure == "link":
        (inputs["staging"] / "linked").symlink_to(inputs["policy_path"])
    else:
        with domain.store.transaction() as connection:
            record = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
            record["rights"] = "unknown"
            domain.store.put(connection, "source", source["source_id"], record)
    with pytest.raises(ValueError):
        build.publish_build(**inputs)
    if failure == "existing":
        assert (inputs["output"] / "old-site").read_text() == "preserve"


def test_real_guard_rejects_local_without_spoofing_ci(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    with pytest.raises(PreviewError, match="GITHUB_HOSTED_BUILD_REQUIRED"):
        build._hosted_runner()


def test_cli_receipt_and_errors_are_redacted(inputs, monkeypatch, capsys):
    from ahadiff.knowledge_platform.operations_preview.__main__ import main
    args = ["publish-build"]
    names = {"manifest_path": "manifest", "policy_path": "policy"}
    for key, value in inputs.items():
        args.extend(["--" + names.get(key, key).replace("_", "-"), str(value)])
    monkeypatch.setattr(build.subprocess, "run", fake_build)
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified_site_build"
    assert main(args) == 2
    result = capsys.readouterr().out
    assert json.loads(result)["code"] == "DESTINATION_EXISTS"
    assert str(inputs["output"]) not in result


def test_cli_timeout_does_not_echo_process_or_private_paths(inputs, monkeypatch, capsys):
    from ahadiff.knowledge_platform.operations_preview.__main__ import main
    def timeout(*args, **kwargs):
        raise build.subprocess.TimeoutExpired("PRIVATE_PATH_AND_SECRET", 900)
    monkeypatch.setattr(build.subprocess, "run", timeout)
    args = ["publish-build"]
    names = {"manifest_path": "manifest", "policy_path": "policy"}
    for key, value in inputs.items():
        args.extend(["--" + names.get(key, key).replace("_", "-"), str(value)])
    assert main(args) == 2
    result = capsys.readouterr().out
    assert json.loads(result)["code"] == "VERIFICATION_FAILED"
    assert "PRIVATE" not in result and not inputs["output"].exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "protected"])
def test_builder_cache_cannot_redirect_writes(tmp_path, kind):
    import os
    site = tmp_path / "site"
    cache = site / "quartz/.quartz-cache"
    cache.mkdir(parents=True)
    protected = tmp_path / "protected"
    protected.write_text("unchanged")
    target = cache / "transpiled-build.mjs"
    if kind == "symlink":
        target.symlink_to(protected)
    elif kind == "hardlink":
        os.link(protected, target)
    else:
        protected = cache / "ledger.db"
        protected.write_text("unchanged")
    with pytest.raises(PreviewError):
        build._builder_cache(site, (protected,))
    assert protected.read_text() == "unchanged"


def test_cache_link_is_blocked_before_subprocess(inputs, tmp_path_factory, monkeypatch):
    site = tmp_path_factory.mktemp("fake-site")
    cache = site / "quartz/.quartz-cache"
    cache.mkdir(parents=True)
    (site / "quartz/bootstrap-cli.mjs").write_text("fake")
    (cache / "transpiled-build.mjs").symlink_to(inputs["policy_path"])
    monkeypatch.setattr(build, "_SITE", site)
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(PreviewError, match="LINK_REJECTED"):
        build.publish_build(**inputs)
    assert inputs["policy_path"].read_text() == "{}"


def test_nested_manifest_cannot_bypass_staging_hash(inputs, monkeypatch):
    folder = inputs["staging"] / "nested"
    folder.mkdir()
    (folder / "publish-manifest.json").write_text('{"private":"not in approved hash"}')
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(PreviewError, match="NESTED_MANIFEST_REJECTED"):
        build.publish_build(**inputs)
