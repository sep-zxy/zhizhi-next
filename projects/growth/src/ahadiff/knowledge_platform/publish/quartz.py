"""Derive Quartz's generated paths from approved staging, never from final output."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from .projection import ProjectionError, _json, _path, parse_markdown


def quartz_generated_files(staging: str | Path) -> tuple[str, ...]:
    root = Path(staging)
    manifest = json.loads((root / "publish-manifest.json").read_text(encoding="utf-8"))
    hashes = {}
    for file in sorted(root.rglob("*")):
        if file.is_symlink():
            raise ProjectionError("Public staging contains a symlink")
        if file.is_file() and file.name != "publish-manifest.json":
            hashes[file.relative_to(root).as_posix()] = hashlib.sha256(
                file.read_bytes()
            ).hexdigest()
    if hashlib.sha256(_json(hashes)).hexdigest() != manifest["build_hash"]:
        raise ProjectionError("Public staging differs from its build hash")
    slugs = {"index", "tags/index"}
    expected = {"index.md"}
    for page in manifest["pages"]:
        slug = _path(page["slug"])
        source = root / (slug + ".md")
        if (
            source.is_symlink()
            or hashlib.sha256(source.read_bytes()).hexdigest() != page["content_hash"]
        ):
            raise ProjectionError("Public staging page differs from its manifest")
        expected.add(slug + ".md")
        slugs.add(slug)
        parent = PurePosixPath(slug).parent
        while str(parent) != ".":
            slugs.add(str(parent) + "/index")
            parent = parent.parent
        metadata, _ = parse_markdown(source.read_text(encoding="utf-8"))
        for tag in metadata.get("tags", []):
            # Match the locked Quartz slugTag/slugifyPath implementation.
            normalized = re.sub(r"\s", "-", tag).replace("&", "-and-").replace("%", "-percent")
            normalized = normalized.replace("?", "").replace("#", "").lower().rstrip("/")
            _path(normalized)
            parts = normalized.split("/")
            slugs.update("tags/" + "/".join(parts[:length]) for length in range(1, len(parts) + 1))
    actual = {file.relative_to(root).as_posix() for file in root.rglob("*.md")}
    if actual != expected:
        raise ProjectionError("Unmanifested Markdown in public staging")
    generated = {"404.html", "index.xml", "sitemap.xml", "robots.txt", "static/contentIndex.json"}
    generated.update(slug + ".html" for slug in slugs)
    generated.update(slug + "-og-image.webp" for slug in slugs)
    return tuple(sorted(generated))
