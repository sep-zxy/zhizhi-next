"""Read-only plans preserve legacy publication policy and user-authored bytes."""

from __future__ import annotations

import json

from ahadiff.knowledge_platform.migrations import build_migration_plan


def make_schema(tmp_path):
    path = tmp_path / "SCHEMA.md"
    registry = {
        "version": 5,
        "types": {
            "wiki": {
                "paths": ["wiki/concepts/*.md"],
                "order": ["title", "publish", "type", "sources"],
                "defaults": {"publish": True},
            },
            "raw": {
                "paths": ["raw/*.md"],
                "order": ["title", "publish"],
                "defaults": {"publish": True},
            },
        },
    }
    path.write_text(
        "```json vault-schema-registry\n" + json.dumps(registry) + "\n```\n", encoding="utf-8"
    )
    return path


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def test_dry_run_preserves_unknown_fields_quotes_and_managed_body(tmp_path):
    root = tmp_path / "vault"
    original = (
        "---\ntitle: 中文条目\npublish: false\ncreated: '2026-10-19'\n"
        "custom_nested:\n  value: yes\nsources: [old-source]\n---\n"
        "<!-- growth:managed:start -->\n原始正文\n<!-- growth:managed:end -->\n"
    )
    target = write(root, "wiki/concepts/one.md", original)
    schema = make_schema(tmp_path)
    plan = build_migration_plan(root, schema)
    item = plan["items"][0]
    assert target.read_text(encoding="utf-8") == original
    assert "created: '2026-10-19'" in item["proposed_text"]
    assert "custom_nested:\n  value: yes" in item["proposed_text"]
    assert item["managed_blocks_preserved"]
    assert item["publication_intent"] == "private"
    assert plan["writes_performed"] == 0
    repeated = build_migration_plan(root, schema, stable_mapping=plan["stable_mapping"])
    assert plan == repeated


def test_explicit_and_registry_legacy_publish_are_preserved(tmp_path):
    root = tmp_path / "vault"
    write(root, "wiki/concepts/default.md", "---\ntitle: Default wiki\n---\nBody\n")
    write(root, "raw/default.md", "---\ntitle: Default RAW\n---\nRaw\n")
    write(
        root,
        "wiki/concepts/private.md",
        "---\ntitle: Explicit private\npublish: false\n---\nBody\n",
    )
    write(root, "other/unknown.md", "---\ntitle: Unknown intent\n---\nBody\n")
    plan = build_migration_plan(root, make_schema(tmp_path))
    intents = {item["path"]: item["publication_intent"] for item in plan["items"]}
    assert intents == {
        "wiki/concepts/default.md": "public",
        "raw/default.md": "public",
        "wiki/concepts/private.md": "private",
        "other/unknown.md": "legacy_unclassified",
    }
    assert all(item["public_projection_allowed"] is False for item in plan["items"])


def test_same_title_is_not_identity_and_duplicate_ids_block(tmp_path):
    root = tmp_path / "vault"
    for name in ("one", "two"):
        write(root, f"wiki/concepts/{name}.md", "---\ntitle: Same title\n---\nDifferent concept\n")
    plan = build_migration_plan(root, make_schema(tmp_path))
    assert len({item["entry_id"] for item in plan["items"]}) == 2
    assert "Same title" in plan["same_title_distinct_entries"]
    duplicate_mapping = {item["path"]: "ent_duplicate" for item in plan["items"]}
    blocked = build_migration_plan(root, tmp_path / "SCHEMA.md", stable_mapping=duplicate_mapping)
    assert blocked["counts"]["blocked"] == 2
    assert all("duplicate_entry_id" in item["issues"] for item in blocked["items"])


def test_external_stable_mapping_supports_rename(tmp_path):
    root = tmp_path / "vault"
    write(
        root,
        "wiki/concepts/new-name.md",
        "---\ntitle: Renamed\ngrowth_concept_id: original-concept\n"
        "card_id: original-card\n---\nUnchanged body\n",
    )
    plan = build_migration_plan(
        root, make_schema(tmp_path), stable_mapping={"wiki/concepts/new-name.md": "ent_stable"}
    )
    item = plan["items"][0]
    assert item["entry_id"] == "ent_stable"
    assert item["growth_concept_id"] == "original-concept"
    assert item["card_id"] == "original-card"


def test_dry_run_never_follows_symlink(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    target = tmp_path / "private.md"
    target.write_text("---\ntitle: Outside\n---\nprivate\n", encoding="utf-8")
    (root / "escape.md").symlink_to(target)
    plan = build_migration_plan(root, make_schema(tmp_path))
    assert plan["items"][0]["state"] == "blocked"
    assert "proposed_text" not in plan["items"][0]
