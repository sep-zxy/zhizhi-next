"""Deterministic publication adapter; no model, deployment or credential access.

Unknown source/asset rights quarantine the referring page. Public manifests never
include exclusion reasons, private filenames, raw paths or internal source IDs.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import yaml
from markdown_it import MarkdownIt

from ..errors import DomainError

PublicationResolver = Callable[[dict[str, Any]], dict[str, Any]]


class ProjectionError(ValueError):
    """An unsafe or ambiguous candidate that must not reach public staging."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: yaml.SafeLoader, node: Any, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise ProjectionError("Duplicate or non-string YAML key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)
_MARKDOWN = MarkdownIt("commonmark")
_SENSITIVE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{16,}|\bAKIA[A-Z0-9]{16}\b|(?:password|api[_-]?key|secret|token)\s*[:=]\s*[^\s,;]{8,})"
)
_PRIVATE_PATH = re.compile(r"(?i)(?:\b(?:raw|daily|output|private)/|\b[a-z]:[\\/]|file://)")


def parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Parse real YAML including quoted dates, nested structures and Chinese tags."""
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized
    match = re.search(r"^---\s*$", normalized[4:], flags=re.MULTILINE)
    if match is None:
        raise ProjectionError("Unterminated YAML frontmatter")
    boundary = 4 + match.start()
    metadata = yaml.load(normalized[4:boundary], Loader=_UniqueLoader)
    if not isinstance(metadata, dict):
        raise ProjectionError("YAML frontmatter must be a mapping")
    return metadata, normalized[4 + match.end() :].lstrip("\n")


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            try:
                return _date(datetime.fromisoformat(text.replace("Z", "+00:00")))
            except ValueError as error:
                raise ProjectionError("Invalid publication date") from error
    raise ProjectionError("Missing publication date")


def _path(value: str) -> str:
    if "\\" in value or "\x00" in value:
        raise ProjectionError("Invalid publication path")
    decoded = value
    for _ in range(3):
        replacement = unquote(decoded)
        if replacement == decoded:
            break
        decoded = replacement
    path = PurePosixPath(decoded)
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in decoded:
        raise ProjectionError("Publication path escapes its root")
    return path.as_posix()


def _public_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ProjectionError("Unsafe public URL")
    host = parsed.hostname.casefold()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ProjectionError("Internal host cannot be public")
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ProjectionError("Private address cannot be public")
    decoded_path = unquote(unquote(parsed.path)).casefold()
    if (
        _PRIVATE_PATH.search(decoded_path.lstrip("/"))
        or re.search(r"/(?:api/knowledge|api/growth|identity|account)(?:/|$)", decoded_path)
        or (host in {"t.me", "telegram.me"} and re.match(r"/(?:c/|\+|joinchat/)", decoded_path))
        or (host.endswith("slack.com") and decoded_path.startswith("/archives/"))
        or (host in {"discord.com", "discordapp.com"} and decoded_path.startswith("/channels/"))
    ):
        raise ProjectionError("Internal, private-chat or identity route cannot be public")
    if re.search(
        r"(?i)(?:token|key|secret|auth|signature|credential|chat_id|user_id|owner_id|device_id)=",
        unquote(parsed.query),
    ):
        raise ProjectionError("Credential-bearing public URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _source_key(source: Any) -> str:
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        for key in ("source_id", "learning_outcome_id", "url", "path"):
            if source.get(key):
                return str(source[key])
    raise ProjectionError("Source has no stable identity")


def _link_targets(body: str) -> list[tuple[str, bool]]:
    targets: list[tuple[str, bool]] = []
    for token in _MARKDOWN.parse(body):
        if token.type in {"html_block", "html_inline"}:
            raise ProjectionError("Raw HTML requires a separately reviewed projection")
        for child in token.children or []:
            if child.type == "html_inline":
                raise ProjectionError("Raw HTML requires a separately reviewed projection")
            if child.type in {"link_open", "image"}:
                target = child.attrGet("href" if child.type == "link_open" else "src")
                if target:
                    targets.append((target, child.type == "image"))
    targets.extend(
        (match.group(2).split("|", 1)[0], bool(match.group(1)))
        for match in re.finditer(r"(!?)\[\[([^\]]+)\]\]", body)
    )
    return targets


def _rewrite_link(body: str, target: str, replacement: str) -> str:
    """Rewrite only syntactic destinations, never arbitrary prose substrings."""
    escaped = re.escape(target)
    body = re.sub(
        r"(\]\(\s*<?)" + escaped + r"(?=>?[\s)])", lambda match: match.group(1) + replacement, body
    )
    body = re.sub(
        r"(^\s*\[[^\]]+\]:\s*<?)" + escaped + r"(?=>?(?:\s|$))",
        lambda match: match.group(1) + replacement,
        body,
        flags=re.MULTILINE,
    )
    return re.sub(
        r"(\[\[)" + escaped + r"(?=[|\]])",
        lambda match: match.group(1) + replacement.lstrip("/"),
        body,
    )


def _json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def build_projection(
    files: dict[str, bytes],
    staging: Path | str,
    *,
    revision: str,
    policy_version: str,
    template_version: str,
    policy: dict[str, Any],
    publication_resolver: PublicationResolver | None = None,
) -> dict[str, Any]:
    """Project an immutable snapshot into an empty staging directory.

    policy.sources maps source identity to an approved public {url,title} or
    {path,title}. policy.assets is a source-path-to-SHA256 allowlist. The caller
    retains internal_manifest; only public_manifest is placed in staging.
    """
    destination = Path(staging)
    if destination.exists() and any(destination.iterdir()):
        raise ProjectionError("Staging must be empty; existing site data is never deleted")
    public_sources = policy.get("sources", {})
    allowed_assets = policy.get("assets", {})
    forbidden = tuple(str(value) for value in policy.get("forbidden_fingerprints", []) if value)
    candidates: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, str]] = []
    for filename, content in sorted(files.items()):
        path = _path(filename)
        if not path.endswith(".md"):
            continue
        try:
            source_text = content.decode("utf-8")
            metadata, body = parse_markdown(source_text)
            intent = metadata.get("publish") is True or metadata.get("visibility") == "public"
            if (
                not intent
                or metadata.get("publish") is False
                or metadata.get("visibility") == "private"
            ):
                continue
            canonical = (
                "schema_version" in metadata
                or "publication_certificate" in metadata
                or ("owner_id" in metadata and "entry_id" in metadata)
                or (publication_resolver is not None and path.startswith("entries/"))
            )
            if (
                metadata.get("deleted") is True
                or metadata.get("publication_eligible") is False
                or (canonical and metadata.get("publication_eligible") is not True)
                or (canonical and metadata.get("schema_version") != 6)
            ):
                raise ProjectionError("Canonical entry is deleted or ineligible for publication")
            artifact = None
            publication_binding = None
            if canonical or metadata.get("publication_certificate"):
                if publication_resolver is None:
                    raise ProjectionError(
                        "Canonical publication requires trusted domain revalidation"
                    )
                # Writer's exact body is certificate-bound, including leading newlines.
                if not source_text.startswith("---\n") or "\n---\n" not in source_text[4:]:
                    raise ProjectionError("Canonical Writer document is not intact")
                exact_body = source_text[4:].split("\n---\n", 1)[1]
                artifact = publication_resolver({**metadata, "body": exact_body})
                if not isinstance(artifact, dict) or not isinstance(artifact.get("body"), str):
                    raise ProjectionError("Domain did not return a verified public artifact")
                certificate = metadata.get("publication_certificate")
                if isinstance(certificate, dict):
                    publication_binding = {
                        key: certificate[key]
                        for key in (
                            "candidate_hash",
                            "source_manifest_hash",
                            "input_manifest_hash",
                            "patch_hash",
                        )
                        if isinstance(certificate.get(key), str)
                        and re.fullmatch(r"[0-9a-f]{64}", certificate[key])
                    }
                    publication_binding["certificate_hash"] = hashlib.sha256(
                        _json(certificate)
                    ).hexdigest()
                body = artifact["body"]
                metadata = {
                    **metadata,
                    "title": artifact.get("title"),
                    "entry_type": artifact.get("entry_type", "knowledge"),
                    "metadata": artifact.get("metadata", {}),
                }
                # Never retain top-level aliases, summary or tags from the internal entry.
                for field in ("summary", "tags", "type", "sources"):
                    metadata.pop(field, None)
            if _PRIVATE_PATH.search(path):
                raise ProjectionError("Private source directory")
            title = metadata.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ProjectionError("A public title is required")
            if (
                _SENSITIVE.search(body)
                or _PRIVATE_PATH.search(body)
                or any(value in body or value in title for value in forbidden)
            ):
                raise ProjectionError("Private or sensitive content fingerprint")
            # Canonical storage paths contain an owner namespace. Public URLs use
            # only the entry identity, never the private tenant-routing directory.
            slug = _path(
                "knowledge/" + str(metadata["entry_id"])
                if artifact is not None
                else path.removesuffix(".md")
            )
            if slug.endswith("/index") and PurePosixPath(path).name != "index.md":
                raise ProjectionError("Ordinary pages cannot become folder indexes")
            nested_metadata = metadata.get("metadata", {})
            if not isinstance(nested_metadata, dict):
                raise ProjectionError("Entry metadata must be a mapping")
            tags = metadata.get("tags", nested_metadata.get("tags", []))
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ProjectionError("Tags must be a YAML string list")
            sources = (
                artifact.get("citations", [])
                if artifact is not None
                else (metadata.get("sources", metadata.get("evidence_refs", [])) or [])
            )
            if not isinstance(sources, list):
                raise ProjectionError("Sources must be a YAML list")
            projected_sources = []
            for source in sources:
                projection = (
                    source if artifact is not None else public_sources.get(_source_key(source))
                )
                if not isinstance(projection, dict):
                    raise ProjectionError("Source has no approved public projection")
                source_title = str(projection.get("title", "来源"))
                href = (
                    _public_url(projection["url"])
                    if projection.get("url")
                    else "/" + _path(projection["path"])
                )
                if _SENSITIVE.search(source_title) or any(
                    value in source_title or value in href for value in forbidden
                ):
                    raise ProjectionError("Unsafe public source projection")
                projected_sources.append({"title": source_title, "url": href})
            created = _date(metadata.get("created", metadata.get("created_at")))
            updated = _date(metadata.get("updated", metadata.get("updated_at", created)))
            public_metadata = {
                "title": title,
                "tags": tags,
                "created": created,
                "updated": updated,
                "type": str(metadata.get("type", metadata.get("entry_type", "knowledge"))),
                "sources": projected_sources,
            }
            summary = (
                None
                if artifact is not None
                else metadata.get("summary", nested_metadata.get("summary"))
            )
            if isinstance(summary, str):
                public_metadata["description"] = summary
            if projected_sources:
                body = (
                    body.rstrip()
                    + "\n\n## 公开来源\n\n"
                    + "\n".join(f"- [{item['title']}]({item['url']})" for item in projected_sources)
                    + "\n"
                )
            candidates[path] = {
                "entry_id": str(
                    metadata.get("entry_id")
                    or metadata.get("id")
                    or hashlib.sha256(path.encode()).hexdigest()
                ),
                "slug": slug,
                "body": body,
                "metadata": public_metadata,
                "sources": projected_sources,
                "created": created,
                "updated": updated,
                "public_artifact_hash": hashlib.sha256(_json(artifact)).hexdigest()
                if artifact is not None
                else None,
                "publication_binding": publication_binding,
            }
        except (
            ProjectionError,
            DomainError,
            UnicodeError,
            yaml.YAMLError,
            KeyError,
            TypeError,
        ) as error:
            excluded.append({"path": path, "reason": str(error)})

    # Repeatedly quarantine referring pages until no link can reveal an excluded
    # private resource, including references removed in an earlier iteration.
    used_assets: set[str] = set()
    while True:
        removed = []
        public_slugs = {}
        for source_path, item in candidates.items():
            for name in (
                source_path,
                source_path.removesuffix(".md"),
                item["slug"],
                item["slug"] + ".md",
            ):
                public_slugs[name] = item["slug"]
        allowed_pages = set(candidates) | {path.removesuffix(".md") for path in candidates}
        allowed_pages |= {item["slug"] for item in candidates.values()} | {
            item["slug"] + ".md" for item in candidates.values()
        }
        for path, candidate in candidates.items():
            try:
                for target, is_asset in _link_targets(candidate["body"]):
                    if target.startswith("#"):
                        continue
                    parsed = urlsplit(target)
                    if parsed.scheme or parsed.netloc:
                        if is_asset:
                            raise ProjectionError(
                                "Remote embedded resources are not in the asset allowlist"
                            )
                        _public_url(target)
                        continue
                    raw_target = unquote(parsed.path).lstrip("/")
                    relative = posixpath.normpath(
                        posixpath.join(posixpath.dirname(path), raw_target)
                    )
                    options = {raw_target, relative}
                    page = next(
                        (value for value in (relative, raw_target) if value in allowed_pages), None
                    )
                    if page is not None and not is_asset:
                        replacement = (
                            "/"
                            + public_slugs[page]
                            + (("#" + parsed.fragment) if parsed.fragment else "")
                        )
                        candidate["body"] = _rewrite_link(candidate["body"], target, replacement)
                        continue
                    asset = next((value for value in options if value in allowed_assets), None)
                    if asset is None or asset not in files:
                        raise ProjectionError("Reference is outside the public manifest allowlist")
                    _path(asset)
                    if _PRIVATE_PATH.search(asset) or any(
                        value in asset or value.encode() in files[asset] for value in forbidden
                    ):
                        raise ProjectionError("Asset contains a private fingerprint")
                    if hashlib.sha256(files[asset]).hexdigest() != allowed_assets[asset]:
                        raise ProjectionError("Asset hash differs from its approved version")
                    if PurePosixPath(asset).suffix.lower() not in {
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".webp",
                        ".avif",
                        ".pdf",
                    }:
                        raise ProjectionError("Active or unsupported public asset")
                    used_assets.add(asset)
                    candidate["body"] = _rewrite_link(candidate["body"], target, "/" + asset)
            except ProjectionError as error:
                removed.append(path)
                excluded.append({"path": path, "reason": str(error)})
        if not removed:
            break
        for path in removed:
            del candidates[path]
        used_assets.clear()

    output: dict[str, bytes] = {}
    pages = []
    source_map: dict[str, list[dict[str, str]]] = {}
    slugs: set[str] = set()
    for _path_key, candidate in sorted(candidates.items()):
        slug = candidate["slug"]
        if slug in slugs:
            raise ProjectionError("Duplicate public slug")
        slugs.add(slug)
        body = candidate["body"]
        # Keep normal paths normal. Directory indexes are solely explicit index.md.
        content = (
            "---\n"
            + yaml.safe_dump(candidate["metadata"], allow_unicode=True, sort_keys=True)
            + "---\n\n"
            + body.rstrip()
            + "\n"
        ).encode()
        if (
            any(value.encode() in content for value in forbidden)
            or _SENSITIVE.search(content.decode())
            or _PRIVATE_PATH.search(content.decode())
        ):
            raise ProjectionError("Public metadata contains a forbidden fingerprint")
        output[slug + ".md"] = content
        pages.append(
            {
                "entry_id": candidate["entry_id"],
                "slug": slug,
                "content_hash": hashlib.sha256(content).hexdigest(),
                "created": candidate["created"],
                "updated": candidate["updated"],
                **(
                    {"public_artifact_hash": candidate["public_artifact_hash"]}
                    if candidate["public_artifact_hash"]
                    else {}
                ),
                **(
                    {"publication_binding": candidate["publication_binding"]}
                    if candidate["publication_binding"]
                    else {}
                ),
            }
        )
        source_map[slug] = candidate["sources"]
    assets = []
    for path in sorted(used_assets):
        output[path] = files[path]
        assets.append({"path": path, "hash": hashlib.sha256(files[path]).hexdigest()})
    recent = sorted(
        pages, key=lambda item: (item["updated"], item["created"], item["slug"]), reverse=True
    )
    if "index.md" not in output:
        titles = {
            item["slug"]: re.sub(r"[\[\]<>]", "", item["metadata"]["title"])
            for item in candidates.values()
        }
        groups: dict[str, list[str]] = {}
        for item in candidates.values():
            for tag in item["metadata"]["tags"] or [item["metadata"]["type"]]:
                groups.setdefault(tag, []).append(item["slug"])
        sections = []
        for tag, members in sorted(groups.items()):
            label = re.sub(r"[\[\]<>#\r\n]", "", tag)
            sections.append(
                f"## {label}（{len(members)}）\n\n"
                + "\n".join(f"- [{titles[slug]}](/{slug})" for slug in sorted(members))
            )
        first_created = min((item["created"] for item in pages), default="1970-01-01T00:00:00Z")
        last_updated = max((item["updated"] for item in pages), default="1970-01-01T00:00:00Z")
        output["index.md"] = (
            "---\ntitle: 知识分类\ntype: index\n"
            + f"created: '{first_created}'\n"
            + f"modified: '{last_updated}'\n"
            + "---\n\n# 知识分类\n\n"
            + "\n\n".join(sections)
            + "\n\n## 最近更新\n\n"
            + "\n".join(f"- [{titles[item['slug']]}](/{item['slug']})" for item in recent[:20])
            + "\n"
        ).encode()
    hashes = {path: hashlib.sha256(content).hexdigest() for path, content in sorted(output.items())}
    manifest = {
        "format_version": 2,
        "revision": revision,
        "input_snapshot_hash": hashlib.sha256(
            _json({path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())})
        ).hexdigest(),
        "policy_hash": hashlib.sha256(_json(policy)).hexdigest(),
        "requires_domain_revalidation": any(item.get("public_artifact_hash") for item in pages),
        "policy_version": policy_version,
        "template_version": template_version,
        "pages": pages,
        "assets": assets,
        "source_projection_map": source_map,
        "counts": {
            "source_files": len(files),
            "content_pages": sum(
                not page["slug"].endswith("/index") and page["slug"] != "index" for page in pages
            ),
            "source_index_pages": sum(
                page["slug"].endswith("/index") or page["slug"] == "index" for page in pages
            ),
            "generated_files": len(output),
        },
        "build_hash": hashlib.sha256(_json(hashes)).hexdigest(),
    }
    output["publish-manifest.json"] = _json(manifest)
    destination.mkdir(parents=True, exist_ok=True)
    for path, content in sorted(output.items()):
        target = destination / _path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return {"public_manifest": manifest, "internal_manifest": {**manifest, "excluded": excluded}}


def project_git_revision(
    repository: Path | str,
    revision: str,
    staging: Path | str,
    *,
    policy: dict[str, Any],
    policy_version: str = "1",
    template_version: str = "1",
    publication_resolver: PublicationResolver | None = None,
) -> dict[str, Any]:
    """Read exact committed blobs, never untracked files, symlinks or submodules."""
    repo = str(Path(repository).resolve())

    def git(*args: str) -> bytes:
        completed = subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True)
        return completed.stdout

    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise ProjectionError("An exact Git commit hash is required")
    commit = git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
    files = {}
    for item in git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not item:
            continue
        header, filename = item.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        if mode not in {"100644", "100755"} or kind != "blob":
            continue
        path = _path(filename.decode("utf-8"))
        files[path] = git("cat-file", "blob", oid)
    if publication_resolver is None:
        for path, data in files.items():
            if path.endswith(".md"):
                metadata, _ = parse_markdown(data.decode("utf-8"))
                if (
                    (
                        "schema_version" in metadata
                        or "publication_certificate" in metadata
                        or ("owner_id" in metadata and "entry_id" in metadata)
                    )
                    and metadata.get("visibility") == "public"
                    and metadata.get("publication_eligible") is True
                ):
                    raise ProjectionError(
                        "Canonical Git export requires an existing trusted domain ledger"
                    )
    return build_projection(
        files,
        staging,
        revision=commit,
        policy_version=policy_version,
        template_version=template_version,
        policy=policy,
        publication_resolver=publication_resolver,
    )
