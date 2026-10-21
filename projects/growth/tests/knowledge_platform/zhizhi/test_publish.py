"""Model-free projection fixtures, not a Quartz build or publication."""

import hashlib

import pytest

from ahadiff.knowledge_platform.errors import DomainError
from ahadiff.knowledge_platform.publish import ProjectionError, build_projection, parse_markdown


def page(body="知识正文。", *, sources="[]", metadata="", updated='"2026-10-19"'):
    return (
        '---\ntitle: 认证与授权\npublish: true\ncreated: "2026-07-08"\n'
        f"updated: {updated}\ntags:\n  - 中文标签\n  - 权限\n"
        f"sources: {sources}\n{metadata}---\n\n{body}\n"
    ).encode()


def project(files, directory, policy=None):
    return build_projection(
        files,
        directory,
        revision="a" * 40,
        policy_version="1",
        template_version="1",
        policy=policy or {},
    )


def test_real_yaml_nested_tags_and_quoted_dates():
    metadata, body = parse_markdown(page(metadata="nested:\n  child: true\n").decode())
    assert metadata["tags"] == ["中文标签", "权限"]
    assert metadata["nested"] == {"child": True}
    assert metadata["updated"] == "2026-10-19"


def test_duplicate_yaml_keys_are_rejected():
    with pytest.raises(ProjectionError):
        parse_markdown("---\npublish: false\npublish: true\n---\n")


def test_canonical_certificate_requires_trusted_resolver_and_exports_only_verified_artifact(
    tmp_path,
):
    content = page(
        "原始私聊内容",
        metadata=(
            "schema_version: 6\nentry_id: entry-one\n"
            "publication_eligible: true\nvisibility: public\n"
            "publication_certificate: {certificate_id: fake_self_report}\n"
        ),
    )
    result = project({"entries/one.md": content}, tmp_path / "no-trust")
    assert result["public_manifest"]["counts"]["content_pages"] == 0
    seen = []

    def resolver(entry):
        seen.append(entry)
        return {
            "title": "已独立复核的原创摘要",
            "body": "只包含公开技术机制。\n",
            "entry_type": "concept",
            "metadata": {"tags": ["架构"]},
            "citations": [{"url": "https://example.org/article", "title": "example.org"}],
        }

    result = build_projection(
        {"entries/one.md": content},
        tmp_path / "verified",
        revision="a" * 40,
        policy_version="1",
        template_version="1",
        policy={},
        publication_resolver=resolver,
    )
    assert seen[0]["body"] == "\n原始私聊内容\n"
    assert result["public_manifest"]["requires_domain_revalidation"] is True
    assert result["public_manifest"]["pages"][0]["public_artifact_hash"]
    public = (tmp_path / "verified/knowledge/entry-one.md").read_text(encoding="utf-8")
    assert "只包含公开技术机制" in public
    assert "原始私聊内容" not in public and "fake_self_report" not in public
    assert "https://example.org/article" in public

    def stale(_entry):
        raise DomainError("PUBLICATION_CERTIFICATE_INVALID", "Stale certificate")

    rejected = build_projection(
        {"entries/one.md": content},
        tmp_path / "stale",
        revision="a" * 40,
        policy_version="1",
        template_version="1",
        policy={},
        publication_resolver=stale,
    )
    assert rejected["public_manifest"]["pages"] == []


def test_explicit_private_wins_and_internal_review_material_never_exports(tmp_path):
    private = page(metadata="visibility: private\n")
    safe = page(
        metadata=(
            "metadata:\n  claims:\n    - quote: 私聊原话绝不外发\n"
            "  open_question_records:\n    - quote: 私聊原话绝不外发\n"
            "  source_id: src_internal_identity\n  owner_id: internal-owner\n"
        )
    )
    result = project(
        {"wiki/private.md": private, "wiki/public.md": safe},
        tmp_path,
        {"forbidden_fingerprints": ["私聊原话绝不外发", "src_internal_identity", "internal-owner"]},
    )
    assert result["public_manifest"]["counts"]["content_pages"] == 1
    assert not (tmp_path / "wiki/private.md").exists()
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert "私聊原话绝不外发" not in path.read_text(encoding="utf-8")
            assert "src_internal_identity" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/api/knowledge/v1/sources/src_hidden",
        "https://example.org/%72aw/hidden.md",
        "https://example.org/account/profile",
        "https://t.me/c/1234/99",
        "https://t.me/+privateInvite",
        "https://workspace.slack.com/archives/channel/thread",
        "https://discord.com/channels/123/456",
        "https://example.org/page?owner_id=private-owner",
    ],
)
def test_private_routes_never_become_public_source_citations(tmp_path, url):
    result = project(
        {"wiki/safe.md": page(sources="[source-one]")},
        tmp_path,
        {"sources": {"source-one": {"title": "Source", "url": url}}},
    )
    assert result["public_manifest"]["counts"]["content_pages"] == 0


@pytest.mark.parametrize(
    "metadata",
    [
        "deleted: true\n",
        "publication_eligible: false\n",
        "schema_version: 6\n",
        "schema_version: 6\npublication_eligible: 'true'\n",
    ],
)
def test_canonical_deleted_or_ineligible_entry_never_publishes(tmp_path, metadata):
    result = project({"wiki/removed.md": page(metadata=metadata)}, tmp_path / "site")
    assert result["public_manifest"]["pages"] == []
    assert "ineligible" in result["internal_manifest"]["excluded"][0]["reason"]


def test_projection_is_deterministic_and_normal_page_is_not_index(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "socket.create_connection", lambda *a, **k: pytest.fail("projection must not use network")
    )
    files = {"wiki/auth.md": page()}
    first = project(files, tmp_path / "one")
    second = project(files, tmp_path / "two")
    assert first["public_manifest"] == second["public_manifest"]
    assert (tmp_path / "one/wiki/auth.md").exists()
    assert not (tmp_path / "one/wiki/auth/index.md").exists()
    metadata, _ = parse_markdown((tmp_path / "one/wiki/auth.md").read_text())
    assert metadata["tags"] == ["中文标签", "权限"]
    assert str(metadata["updated"]) == "2026-10-19"


def test_source_projection_keeps_approved_reference_without_raw_identity(tmp_path):
    result = project(
        {"wiki/auth.md": page(sources="[private-source-id]")},
        tmp_path / "site",
        {
            "sources": {
                "private-source-id": {"url": "https://example.org/paper", "title": "公开论文"}
            }
        },
    )
    rendered = "".join(
        path.read_text() for path in (tmp_path / "site").rglob("*") if path.is_file()
    )
    assert "https://example.org/paper" in rendered
    assert "private-source-id" not in rendered
    assert result["public_manifest"]["counts"]["content_pages"] == 1


@pytest.mark.parametrize(
    "body",
    [
        "![secret](../raw/private.png)",
        "[[private-diary]]",
        '<img src="raw/private.png">',
        "![tracker](https://example.org/tracker.png)",
    ],
)
def test_unapproved_references_quarantine_page(tmp_path, body):
    result = project({"wiki/auth.md": page(body)}, tmp_path / "site")
    assert result["public_manifest"]["pages"] == []
    assert result["internal_manifest"]["excluded"]
    assert "private" not in (tmp_path / "site/publish-manifest.json").read_text()


def test_unknown_source_cannot_be_deleted_to_make_page_public(tmp_path):
    result = project({"wiki/auth.md": page(sources="[unknown]")}, tmp_path / "site")
    assert result["public_manifest"]["pages"] == []


def test_assets_require_exact_approved_hash_and_only_referenced_assets_copy(tmp_path):
    image = b"fixture-image-not-a-real-image"
    files = {
        "wiki/auth.md": page("![diagram](../assets/diagram.png)"),
        "assets/diagram.png": image,
        "assets/unused.png": b"unused-private",
    }
    result = project(
        files,
        tmp_path / "site",
        {"assets": {"assets/diagram.png": hashlib.sha256(image).hexdigest()}},
    )
    assert result["public_manifest"]["assets"][0]["path"] == "assets/diagram.png"
    assert not (tmp_path / "site/assets/unused.png").exists()
    assert "/assets/diagram.png" in (tmp_path / "site/wiki/auth.md").read_text()


def test_private_asset_fingerprint_blocks_entire_reference(tmp_path):
    image = b"image-with-private-fingerprint"
    result = project(
        {"wiki/auth.md": page("![x](../assets/x.png)"), "assets/x.png": image},
        tmp_path / "site",
        {
            "assets": {"assets/x.png": hashlib.sha256(image).hexdigest()},
            "forbidden_fingerprints": ["private-fingerprint"],
        },
    )
    assert not result["public_manifest"]["pages"]
    assert not result["public_manifest"]["assets"]


def test_existing_staging_is_never_deleted(tmp_path):
    (tmp_path / "keep.txt").write_text("existing site")
    with pytest.raises(ProjectionError, match="empty"):
        project({"wiki/auth.md": page()}, tmp_path)
    assert (tmp_path / "keep.txt").read_text() == "existing site"


def test_latest_order_uses_parsed_dates(tmp_path):
    result = project(
        {"wiki/old.md": page(updated="2026-07-01"), "wiki/new.md": page(updated='"2026-10-19"')},
        tmp_path,
    )
    text = (tmp_path / "index.md").read_text()
    assert text.index("wiki/new") < text.index("wiki/old")
    assert result["public_manifest"]["counts"]["content_pages"] == 2
