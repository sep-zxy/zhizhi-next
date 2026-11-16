"""The release gate checks emitted HTML, search JSON and exact asset versions."""

from __future__ import annotations

import hashlib
import json

import pytest

from ahadiff.knowledge_platform.publish.artifacts import validate_public_artifacts
from ahadiff.knowledge_platform.publish.projection import ProjectionError, _json
from ahadiff.knowledge_platform.publish.quartz import quartz_generated_files


def test_final_artifact_binds_policy_snapshot_and_exact_emitted_manifest(tmp_path):
    manifest = site(tmp_path)
    policy = {"sources": {}, "forbidden_fingerprints": ["private"]}
    manifest.update(
        {
            "format_version": 2,
            "revision": "a" * 40,
            "input_snapshot_hash": "b" * 64,
            "policy_hash": hashlib.sha256(_json(policy)).hexdigest(),
        }
    )
    emitted = tmp_path / "publish-manifest.json"
    emitted.write_bytes(_json(manifest))
    report = validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[], policy=policy)
    assert report["policy_hash"] == manifest["policy_hash"]
    assert report["input_snapshot_hash"] == manifest["input_snapshot_hash"]
    assert report["public_manifest_hash"] == hashlib.sha256(_json(manifest)).hexdigest()
    with pytest.raises(ProjectionError, match="policy"):
        validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[], policy={})
    emitted.write_bytes(_json({**manifest, "revision": "c" * 40}))
    with pytest.raises(ProjectionError, match="differs"):
        validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[], policy=policy)


def site(tmp_path):
    (tmp_path / "architecture.html").write_text(
        '<h1>架构取舍</h1><a href="/">首页</a>', encoding="utf-8"
    )
    (tmp_path / "index.html").write_text('<a href="/architecture">架构</a>', encoding="utf-8")
    return {"pages": [{"slug": "architecture"}], "assets": []}


def test_final_output_links_and_repeatable_digest(tmp_path):
    manifest = site(tmp_path)
    first = validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=["私密标题"])
    assert first["files_checked"] == 2
    assert first == validate_public_artifacts(
        tmp_path, manifest, forbidden_fingerprints=["私密标题"]
    )


def test_private_title_in_generated_search_json_blocks_release(tmp_path):
    manifest = site(tmp_path)
    (tmp_path / "static").mkdir()
    (tmp_path / "static/contentIndex.json").write_text(
        json.dumps({"title": "私密标题"}), encoding="utf-8"
    )
    with pytest.raises(ProjectionError, match="fingerprint"):
        validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=["私密标题"])


def test_unlisted_attachment_or_unresolved_link_cannot_publish(tmp_path):
    manifest = site(tmp_path)
    (tmp_path / "index.html").write_text('<a href="/secret.pdf">download</a>', encoding="utf-8")
    with pytest.raises(ProjectionError, match="reference"):
        validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[])
    (tmp_path / "secret.pdf").write_bytes(b"private attachment")
    with pytest.raises(ProjectionError, match="allowlist"):
        validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[])


def test_exact_approved_asset_is_required_in_final_site(tmp_path):
    manifest = site(tmp_path)
    data = b"approved image"
    (tmp_path / "image.png").write_bytes(data)
    (tmp_path / "index.html").write_text(
        '<img src="/image.png"><a href="/architecture">架构</a>', encoding="utf-8"
    )
    manifest["assets"] = [{"path": "image.png", "hash": hashlib.sha256(data).hexdigest()}]
    assert validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[])
    (tmp_path / "image.png").write_bytes(b"different content")
    with pytest.raises(ProjectionError, match="digest"):
        validate_public_artifacts(tmp_path, manifest, forbidden_fingerprints=[])


def test_quartz_generated_allowlist_comes_from_manifest_not_output(tmp_path):
    from ahadiff.knowledge_platform.publish.projection import build_projection

    build_projection(
        {
            "architecture/example.md": (
                b"---\ntitle: Example\npublish: true\ncreated: '2026-01-01'\n"
                b"updated: '2026-02-01'\ntags: ['Architecture/Tradeoffs']\n---\nEvidence.\n"
            )
        },
        tmp_path,
        revision="a" * 40,
        policy={},
        policy_version="1",
        template_version="1",
    )
    paths = quartz_generated_files(tmp_path)
    assert "architecture/example.html" in paths
    assert "architecture/index.html" in paths
    assert "tags/architecture/tradeoffs.html" in paths
    assert "tags/architecture/tradeoffs-og-image.webp" in paths
    assert "unknown.html" not in paths
    (tmp_path / "unexpected.md").write_text("private", encoding="utf-8")
    with pytest.raises(ProjectionError, match="build hash"):
        quartz_generated_files(tmp_path)


def test_binary_asset_fingerprints_are_checked_without_parsing_compression_as_paths(tmp_path):
    manifest = site(tmp_path)
    data = b"RIFF\xffI:/\x00compressed"
    (tmp_path / "image.webp").write_bytes(data)
    assets = {"image.webp": hashlib.sha256(data).hexdigest()}
    assert validate_public_artifacts(
        tmp_path,
        manifest,
        forbidden_fingerprints=["private phrase"],
        trusted_template_assets=assets,
    )
    data += b"private phrase"
    (tmp_path / "image.webp").write_bytes(data)
    assets["image.webp"] = hashlib.sha256(data).hexdigest()
    with pytest.raises(ProjectionError, match="fingerprint"):
        validate_public_artifacts(
            tmp_path,
            manifest,
            forbidden_fingerprints=["private phrase"],
            trusted_template_assets=assets,
        )
