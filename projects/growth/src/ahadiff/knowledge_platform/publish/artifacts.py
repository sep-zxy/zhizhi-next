"""Fail-closed post-build scan of HTML, search indexes and emitted assets."""

from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .projection import _PRIVATE_PATH, _SENSITIVE, ProjectionError, _json, _path, _public_url


class _References(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if not value:
                continue
            if name in {"href", "src", "poster", "action"}:
                self.targets.append(value)
            elif name == "srcset":
                self.targets.extend(
                    item.strip().split()[0] for item in value.split(",") if item.strip()
                )


def validate_public_artifacts(
    output: str | Path,
    public_manifest: dict[str, Any],
    *,
    forbidden_fingerprints: list[str],
    trusted_template_assets: dict[str, str] | None = None,
    generated_files: tuple[str, ...] = (
        "index.html",
        "static/contentIndex.json",
        "sitemap.xml",
        "robots.txt",
    ),
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check the final directory, not just Markdown staging.

    Templates must supply exact asset digests from their trusted build inputs.
    This scanner never constructs an allowlist from the directory being checked.
    A failure blocks release; the existing website is not touched.
    """
    root = Path(output).resolve()
    if not root.is_dir():
        raise ProjectionError("Final artifact directory does not exist")
    if policy is not None:
        if (
            public_manifest.get("format_version") != 2
            or public_manifest.get("policy_hash") != hashlib.sha256(_json(policy)).hexdigest()
        ):
            raise ProjectionError("Public manifest does not bind the exact publication policy")
        if not re.fullmatch(r"[0-9a-f]{64}", str(public_manifest.get("input_snapshot_hash", ""))):
            raise ProjectionError("Public manifest has no immutable input snapshot binding")
    asset_hashes = dict(trusted_template_assets or {})
    asset_hashes.update({item["path"]: item["hash"] for item in public_manifest["assets"]})
    allowed = {_path(name) for name in generated_files}
    for page in public_manifest["pages"]:
        slug = _path(page["slug"])
        allowed.add(slug + ".html")
        allowed.add(slug + "/index.html")
    allowed.update(_path(name) for name in asset_hashes)
    allowed.add("publish-manifest.json")
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ProjectionError("Final output contains a symlink")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            if name not in allowed or _PRIVATE_PATH.search(name):
                raise ProjectionError("Final output is outside the public allowlist")
            data = path.read_bytes()
            if name in asset_hashes and hashlib.sha256(data).hexdigest() != asset_hashes[name]:
                raise ProjectionError("Final asset differs from the approved digest")
            files[name] = data
    if "index.html" not in files:
        raise ProjectionError("Final output has no home page")
    if public_manifest.get("format_version") == 2:
        try:
            emitted_manifest = json.loads(files["publish-manifest.json"])
        except (KeyError, ValueError) as error:
            raise ProjectionError("Final public manifest is missing or invalid") from error
        if emitted_manifest != public_manifest:
            raise ProjectionError("Final public manifest differs from the approved input")
    for page in public_manifest["pages"]:
        if not {page["slug"] + ".html", page["slug"] + "/index.html"}.intersection(files):
            raise ProjectionError("A public manifest page is missing from final output")

    for name, data in files.items():
        text = data.decode("utf-8", errors="replace")
        normalized = unquote(html.unescape(text))
        for fingerprint in forbidden_fingerprints:
            variants = {fingerprint, json.dumps(fingerprint, ensure_ascii=True)[1:-1]}
            if any(value and (value in normalized or value.encode() in data) for value in variants):
                raise ProjectionError("Private fingerprint leaked into final output")
        # Random compressed image bytes are not text (e.g. WebP may contain I:/).
        # All binary bytes still get exact fingerprint checks above and must be
        # digest-approved assets or explicitly derived public OG paths.
        textual = Path(name).suffix.lower() in {
            ".html",
            ".css",
            ".js",
            ".json",
            ".xml",
            ".txt",
            ".svg",
        }
        if textual and (_PRIVATE_PATH.search(normalized) or _SENSITIVE.search(normalized)):
            raise ProjectionError("Private path or credential pattern in final output")
        targets: list[str] = []
        if name.endswith(".html"):
            parser = _References()
            parser.feed(text)
            targets.extend(parser.targets)
        if name.endswith(".css"):
            targets.extend(
                match[1] for match in re.findall(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", text)
            )
        for target in targets:
            if target.startswith("#") or target.startswith("data:image/"):
                continue
            parsed = urlsplit(target)
            if parsed.scheme in {"mailto", "tel"}:
                continue
            if parsed.scheme or parsed.netloc:
                _public_url(target)
                continue
            raw = unquote(parsed.path)
            resolved = posixpath.normpath(
                raw.lstrip("/")
                if raw.startswith("/")
                else posixpath.join(posixpath.dirname(name), raw)
            )
            if resolved == ".":
                resolved = "index.html"
            _path(resolved)
            variants = {resolved, resolved + ".html", resolved.rstrip("/") + "/index.html"}
            if not variants.intersection(files):
                raise ProjectionError("Final HTML/CSS contains an unresolved or private reference")
    return {
        "files_checked": len(files),
        "public_manifest_hash": hashlib.sha256(_json(public_manifest)).hexdigest(),
        "input_snapshot_hash": public_manifest.get("input_snapshot_hash"),
        "policy_hash": public_manifest.get("policy_hash"),
        "revision": public_manifest.get("revision"),
        "artifact_hash": hashlib.sha256(
            json.dumps(
                {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }
