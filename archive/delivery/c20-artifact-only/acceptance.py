"""Real Quartz on synthetic canonical Git/SQLite; not live or deployment approval.

Run only through the reviewed GitHub-hosted namespace launcher. No guard mocks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from bounded_command import run_synthetic


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def tree_hashes(root):
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Artifact symlink")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def reset_cache(cache, seed):
    """Replace children of this isolated cache only; preserve its bind mount."""
    for root in (cache, seed):
        require(root.is_dir() and not root.is_symlink(), "Unsafe cache root")
        for path in root.rglob("*"):
            require(not path.is_symlink() and (path.is_dir() or path.is_file()), "Unsafe cache member")
            if path.is_file():
                require(path.stat().st_nlink == 1, "Cache hardlink")
    require(not cache.is_relative_to(seed) and not seed.is_relative_to(cache), "Cache seed overlap")
    for path in cache.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(seed, cache, dirs_exist_ok=True)
    require(tree_hashes(cache) == tree_hashes(seed), "Cache reset differs from seed")


def rejected(operation, destination, expected_type, expected_message):
    try:
        operation()
    except Exception as error:
        if type(error) is not expected_type or str(error) != expected_message:
            raise AssertionError("Negative gate failed for an unexpected reason") from error
        require(not destination.exists(), "Rejected build left output")
        return True
    raise AssertionError("Negative gate unexpectedly succeeded")


def fixture(work):
    from ahadiff.knowledge_platform.auth import Actor
    from ahadiff.knowledge_platform.domain import DomainService
    directory = work / "fixture"
    directory.mkdir()
    domain = DomainService(directory / "platform.db")
    owner = Actor("c20-synthetic-owner", "c20-human", "user", frozenset({"*"}))
    generator = Actor(owner.owner_id, "c20-generator", "agent", frozenset({"knowledge:read", "knowledge:write"}))
    writer = Actor(owner.owner_id, "c20-writer", "service", frozenset({"*"}))
    intake = domain.create_intake({"text": "Synthetic public evidence preserves history.",
                                  "data_classification": "public"}, actor=owner, idempotency_key="c20-intake")
    preview = domain.update_intake(intake["intake_id"],
        {"state": "preview_ready", "preview": {"summary": "Synthetic evidence"}},
        actor=owner, idempotency_key="c20-preview")
    source = domain.retain_source(intake["intake_id"],
        {"raw_text": "Synthetic public evidence preserves history.",
         "preview_revision": preview["preview_revision"], "rights": "summary_allowed"},
        actor=owner, idempotency_key="c20-retain")
    proposal = domain.propose_change({"title": "Synthetic C20 evidence",
        "body": "Synthetic public evidence preserves history.\n",
        "evidence_refs": [{"source_id": source["source_id"]}]},
        actor=generator, idempotency_key="c20-proposal")
    entry = domain.apply_change(proposal["change_id"], actor=writer, idempotency_key="c20-apply")
    return domain, owner, source, entry


def independent_assets(work, source_sha):
    from ahadiff.knowledge_platform.publish.projection import build_projection
    from ahadiff.knowledge_platform.operations_preview import build
    build._hosted_runner()
    build._fonts(build._SITE)
    mappings = []
    for index in (1, 2):
        staging, output = work / f"template-input-{index}", work / f"template-output-{index}"
        page = ("---\ntitle: Independent template\npublish: true\n"
                "created: '2026-01-01'\nupdated: '2026-02-01'\n---\n\n"
                f"Independent synthetic baseline {index}.\n").encode()
        build_projection({"template.md": page}, staging, revision=source_sha,
                         policy_version="c20-template", template_version="c20-template", policy={})
        env = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG") if key in os.environ}
        env.update(TZ="UTC", QUARTZ_OFFLINE_PLUGINS="1", QUARTZ_PUBLIC_BASE_URL="https://example.org")
        run_synthetic([shutil.which("node"), "quartz/bootstrap-cli.mjs", "build", "-d", str(staging),
                        "-o", str(output), "--concurrency=1"], cwd=build._SITE, env=env,
                       timeout=900, label=f"template-{index}")
        mappings.append({name: digest for name, digest in tree_hashes(output).items()
                         if Path(name).suffix.lower() in {".css", ".js", ".woff", ".woff2", ".ttf", ".svg", ".png", ".ico"}})
    # Only stable assets from TWO unrelated inputs; never inspect target outputs
    # to authorize an asset. Content-specific generated OGs use the existing gate.
    assets = {name: digest for name, digest in mappings[0].items() if mappings[1].get(name) == digest}
    require(bool(assets), "Independent asset baseline empty")
    target = work / "template-assets.json"
    write_json(target, {"source_commit": source_sha, "assets": assets,
                        "scope": "independent synthetic template baseline"})
    return target


def run(work, source_sha):
    from ahadiff.knowledge_platform.operations_preview.build import publish_build, _hosted_runner
    from ahadiff.knowledge_platform.operations_preview import build
    from ahadiff.knowledge_platform.operations_preview.publication import publish_preview
    from ahadiff.knowledge_platform.publish.artifacts import validate_public_artifacts
    from ahadiff.knowledge_platform.publish.quartz import quartz_generated_files
    from ahadiff.knowledge_platform.publish.projection import ProjectionError
    from ahadiff.knowledge_platform.operations_preview.checks import PreviewError
    _hosted_runner()
    require(not work.exists(), "Use a new acceptance directory")
    work.mkdir(parents=True)
    # The launcher owns namespace verification. This receipt does not upgrade
    # the product's isolation_attested=false into an assertion based on env vars.
    build._builder_cache(build._SITE, (work,))
    build._fonts(build._SITE)
    cache_pairs = []
    for index, relative in enumerate(("quartz/.quartz-cache", ".quartz-cache")):
        cache, seed = build._SITE / relative, work / f"prepared-cache-seed-{index}"
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cache, seed)
        cache_pairs.append((cache, seed))
    def fresh_cache():
        build._builder_cache(build._SITE, (work,))
        for index, (cache, seed) in enumerate(cache_pairs):
            require(tree_hashes(seed) == cache_seed_hashes[index], "Prepared cache seed changed")
            reset_cache(cache, seed)
    cache_seed_hashes = [tree_hashes(seed) for _, seed in cache_pairs]
    assets = independent_assets(work, source_sha)
    domain, owner, source, entry = fixture(work)
    sentinel = "C20_SYNTHETIC_FORBIDDEN_FINGERPRINT_7d5c"
    policy = {"forbidden_fingerprints": [sentinel]}
    policy_path = work / "policy.json"
    write_json(policy_path, policy)
    outputs = work / "outputs"
    outputs.mkdir()
    common = dict(repository=domain.writer.repository, database=domain.store.path,
                  owner_id=owner.owner_id, policy_path=policy_path)
    try:
        staging = work / "approved-staging"
        publish_preview(**common, revision=entry["commit_sha"], staging=staging)
        manifest_path = staging / "publish-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        require(len(manifest["pages"]) > 0, "Empty canonical fixture")
        args = dict(**common, manifest_path=manifest_path, staging=staging,
                    template_assets=assets, public_base_url="https://example.org")
        receipts = []
        for label in ("a", "b"):
            fresh_cache()
            receipts.append(publish_build(**args, output=outputs / label))
        hashes = [tree_hashes(outputs / label) for label in ("a", "b")]
        require(hashes[0] == hashes[1], "Double-build bytes differ")
        require(receipts[0]["artifact_hash"] == receipts[1]["artifact_hash"], "Double-build receipt mismatch")
        for receipt in receipts:
            require(receipt["site_build_executed"] is True and receipt["deploy_executed"] is False
                    and receipt["isolation_attested"] is False, "Incorrect product receipt")

        # Corrupt a COPY of the real output to exercise the real full scanner.
        leaked = outputs / "leak-scan-copy"
        shutil.copytree(outputs / "a", leaked)
        scan_args = dict(policy=policy, forbidden_fingerprints=[sentinel],
            trusted_template_assets=json.loads(assets.read_text())["assets"],
            generated_files=quartz_generated_files(staging))
        # Unchanged copy is a positive control for the exact scanner invocation.
        validate_public_artifacts(leaked, manifest, **scan_args)
        with (leaked / "index.html").open("a", encoding="utf-8") as stream:
            stream.write(sentinel)
        try:
            validate_public_artifacts(leaked, manifest, **scan_args)
        except ProjectionError as error:
            require(type(error) is ProjectionError and str(error) == "Private fingerprint leaked into final output",
                    "Leak scanner failed for an unrelated reason")
            leak_blocked = True
        else:
            raise AssertionError("Leak scanner accepted fingerprint")
        shutil.rmtree(leaked)

        page = staging / "index.md"
        original = page.read_bytes()
        page.write_bytes(original + b"\nTampered synthetic input.\n")
        tamper_blocked = rejected(lambda: publish_build(**args, output=outputs / "tamper"),
            outputs / "tamper", ProjectionError, "Public staging differs from its build hash")
        page.write_bytes(original)
        fresh_cache()
        tamper_control = publish_build(**args, output=outputs / "tamper-restored-control")
        require(tree_hashes(outputs / "tamper-restored-control") == hashes[0], "Restored staging control differs")
        with domain.store.transaction() as connection:
            record = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
            original_rights = record["rights"]
            record["rights"] = "unknown"
            domain.store.put(connection, "source", source["source_id"], record)
        revoked_blocked = rejected(lambda: publish_build(**args, output=outputs / "revoked"),
            outputs / "revoked", PreviewError, "DOMAIN_PROJECTION_CHANGED")
        # Restore only this synthetic fixture's deliberately changed field.
        with domain.store.transaction() as connection:
            record = domain.store.get(connection, "source", source["source_id"], owner.owner_id)
            record["rights"] = original_rights
            domain.store.put(connection, "source", source["source_id"], record)
        fresh_cache()
        revocation_control = publish_build(**args, output=outputs / "rights-restored-control")
        require(tree_hashes(outputs / "rights-restored-control") == hashes[0], "Restored rights control differs")
        require([tree_hashes(seed) for _, seed in cache_pairs] == cache_seed_hashes,
                "Prepared cache seed changed after final build")
        result = {"source_commit": source_sha, "scope": "real Quartz with synthetic canonical Git and SQLite",
                  "status": "synthetic_real_build_verified", "product_receipts": receipts,
                  "double_build_identical": True, "artifact_files": hashes[0],
                  "cache_reset_before_each_successful_publication_build": True,
                  "prepared_cache_seed_hashes": cache_seed_hashes,
                  "leak_scan_negative_gate": leak_blocked, "tamper_negative_gate": tamper_blocked,
                  "revocation_negative_gate": revoked_blocked, "deploy_executed": False,
                  "positive_control_receipts": [tamper_control, revocation_control],
                  "live_model": "not_run", "live_user_data": "not_run", "windows_e2e": "not_run"}
        write_json(work / "acceptance-receipt.json", result)
        return result
    finally:
        domain.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    require(len(args.source) == 40 and all(c in "0123456789abcdef" for c in args.source), "Invalid source SHA")
    run(args.work.resolve(), args.source)
