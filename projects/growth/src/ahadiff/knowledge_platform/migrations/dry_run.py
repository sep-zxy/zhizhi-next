"""Preserve legacy publication intent, unknown fields and exact managed content."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..contracts import fingerprint
from ..errors import invalid


def load_registry(schema_path: str | Path) -> dict[str, Any]:
    """Load the actual Schema registry; absence/parse failure is a hard error."""
    content = Path(schema_path).read_text(encoding="utf-8")
    match = re.search(r"```json vault-schema-registry\s*\n(.*?)\n```", content, re.S)
    if not match:
        raise invalid("Schema registry block was not found")
    try:
        registry = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise invalid("Schema registry is not valid JSON") from exc
    if not isinstance(registry.get("version"), int) or not isinstance(registry.get("types"), dict):
        raise invalid("Schema registry is incomplete")
    if registry["version"] > 6:
        raise invalid("A newer schema exists; migration must be reviewed before proceeding")
    return registry


def _matching_rule(relative: str, registry: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    path = PurePosixPath(relative)
    for name, rule in registry["types"].items():
        if any(path.match(pattern) for pattern in rule.get("paths", [])) and not any(
            path.match(pattern) for pattern in rule.get("exclude", [])
        ):
            return name, rule
    return None, {}


def _publication(metadata: dict[str, Any], rule: dict[str, Any]) -> tuple[str, str]:
    if isinstance(metadata.get("publish"), bool):
        return ("public" if metadata["publish"] else "private"), "explicit_publish"
    if metadata.get("visibility") in {"public", "private", "legacy_unclassified"}:
        return metadata["visibility"], "explicit_visibility"
    legacy_default = rule.get("defaults", {}).get("publish")
    if isinstance(legacy_default, bool):
        return ("public" if legacy_default else "private"), "existing_registry_policy"
    return "legacy_unclassified", "no_existing_publication_policy"


def build_migration_plan(
    vault_root: str | Path, schema_path: str | Path, *, stable_mapping: dict[str, str] | None = None
) -> dict[str, Any]:
    """Produce a deterministic proposal without writing the Vault or mapping file.

    The returned stable_mapping must be persisted by an authorized migration step
    before applying/renaming assets. Existing IDs always win; duplicate IDs block both
    files. Unknown YAML fields and original bytes are retained in proposed text.
    """
    root = Path(vault_root).resolve(strict=True)
    if not root.is_dir():
        raise invalid("Vault root must be a directory")
    registry = load_registry(schema_path)
    mapping = dict(stable_mapping or {})
    items: list[dict[str, Any]] = []
    id_paths: dict[str, list[str]] = {}
    title_paths: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            items.append(
                {"path": relative, "state": "blocked", "issues": ["symlink_or_path_escape"]}
            )
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            items.append(
                {
                    "path": relative,
                    "original_hash": digest,
                    "state": "blocked",
                    "issues": ["not_utf8"],
                }
            )
            continue
        newline = "\r\n" if "\r\n" in text else "\n"
        normalized = text.replace("\r\n", "\n")
        if not normalized.startswith("---\n") or "\n---\n" not in normalized[4:]:
            items.append(
                {
                    "path": relative,
                    "original_hash": digest,
                    "state": "blocked",
                    "issues": ["missing_frontmatter"],
                }
            )
            continue
        raw, body = normalized[4:].split("\n---\n", 1)
        try:
            metadata = yaml.safe_load(raw)
        except yaml.YAMLError:
            items.append(
                {
                    "path": relative,
                    "original_hash": digest,
                    "state": "blocked",
                    "issues": ["invalid_yaml"],
                }
            )
            continue
        if not isinstance(metadata, dict):
            items.append(
                {
                    "path": relative,
                    "original_hash": digest,
                    "state": "blocked",
                    "issues": ["invalid_frontmatter"],
                }
            )
            continue
        if isinstance(metadata.get("schema_version"), int) and metadata["schema_version"] > 6:
            items.append(
                {
                    "path": relative,
                    "original_hash": digest,
                    "state": "blocked",
                    "issues": ["newer_schema"],
                }
            )
            continue
        entry_id = (
            metadata.get("entry_id")
            or mapping.get(relative)
            or "ent_" + uuid.uuid5(uuid.NAMESPACE_URL, "knowledge:v6:" + relative).hex
        )
        mapping[relative] = entry_id
        kind, rule = _matching_rule(relative, registry)
        visibility, reason = _publication(metadata, rule)
        additions: list[str] = []
        for field, value in (
            ("entry_id", entry_id),
            ("schema_version", 6),
            ("visibility", visibility),
            ("publication_intent", visibility),
        ):
            if field not in metadata:
                additions.append(f"{field}: {json.dumps(value, ensure_ascii=False)}")
        if "schema_version" in metadata:
            raw = re.sub(r"(?m)^schema_version:[^\n]*$", "schema_version: 6", raw)
        # Do not re-serialize existing YAML. Comments, quoted dates, custom fields,
        # anchors and managed blocks remain byte-for-byte in the candidate.
        proposed = (
            "---\n" + raw + ("\n" + "\n".join(additions) if additions else "") + "\n---\n" + body
        )
        proposed = proposed.replace("\n", newline)
        issues = []
        if visibility == "legacy_unclassified":
            issues.append("publication_intent_unclassified")
        sources = metadata.get("sources", metadata.get("source", []))
        if not sources:
            issues.append("source_evidence_missing")
        item = {
            "path": relative,
            "entry_id": entry_id,
            "original_hash": digest,
            "proposed_hash": hashlib.sha256(proposed.encode("utf-8")).hexdigest(),
            "state": "planned",
            "schema_type": kind,
            "publication_intent": visibility,
            "publication_mapping_reason": reason,
            "public_projection_allowed": False,
            "unknown_fields_preserved": sorted(set(metadata) - set(rule.get("order", []))),
            "managed_blocks_preserved": "<!-- growth:managed:start -->" in body,
            "source_refs_preserved": sources,
            "proposed_text": proposed,
            "issues": issues,
            "growth_concept_id": metadata.get("growth_concept_id"),
            "card_id": metadata.get("card_id"),
            "body_hash": hashlib.sha256(body.encode()).hexdigest(),
        }
        items.append(item)
        id_paths.setdefault(str(entry_id), []).append(relative)
        title_paths.setdefault(str(metadata.get("title", "")), []).append(relative)
    duplicate_ids = {key: paths for key, paths in id_paths.items() if len(paths) > 1}
    for item in items:
        if str(item.get("entry_id")) in duplicate_ids:
            item["state"] = "blocked"
            item["issues"].append("duplicate_entry_id")
    # YAML date values in legacy metadata are represented losslessly as ISO strings in JSON reports.
    items = json.loads(json.dumps(items, ensure_ascii=False, default=str))
    result = {
        "dry_run": True,
        "schema_from": registry["version"],
        "schema_to": 6,
        "items": items,
        "stable_mapping": mapping,
        "duplicate_ids": duplicate_ids,
        "same_title_distinct_entries": {
            key: paths for key, paths in title_paths.items() if key and len(paths) > 1
        },
        "counts": {
            "total": len(items),
            "blocked": sum(item["state"] == "blocked" for item in items),
        },
        "writes_performed": 0,
    }
    result["plan_hash"] = fingerprint(result)
    return result
