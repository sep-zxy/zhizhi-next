"""Domain-only projection adapter; the general legacy Markdown exporter stays unchanged."""
from pathlib import Path
import tempfile

from ..publish.projection import build_projection, parse_markdown
from ..writer import GitWriter, ReadOnlyGitWriter
from .checks import _relative, _require, _revision


def project_domain_revision(repository, revision, staging, *, owner_id, policy,
                            policy_version="1", template_version="1", publication_resolver):
    """Read with the existing scrubbed, bounded Git facade; no inherited GIT_DIR.

    Every Markdown input must be a canonical entry in this owner's namespace.
    Legacy publish:true documents cannot become public via a domain operator.
    Existing resolver still decides current rights/certificate/revocation status.
    """
    _revision(revision)
    writer = ReadOnlyGitWriter(repository)
    commit = writer._git("rev-parse", "--verify", revision + "^{commit}").decode().strip()
    files = {}
    for item in writer._git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not item:
            continue
        header, filename = item.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        _require(mode in {"100644", "100755"} and kind == "blob", "NONREGULAR_GIT_INPUT")
        path = _relative(filename.decode("utf-8"))
        content = writer._git("cat-file", "blob", oid)
        if path.endswith(".md"):
            metadata, _ = parse_markdown(content.decode("utf-8"))
            _require(metadata.get("schema_version") == 6 and isinstance(metadata.get("entry_id"), str),
                     "NON_DOMAIN_MARKDOWN_REJECTED")
            expected = GitWriter.path_for(owner_id, metadata["entry_id"])
            # Other owners' canonical files are not this operator's inputs.
            if path != expected:
                continue
        files[path] = content
    return build_projection(files, staging, revision=commit, policy=policy,
                            policy_version=policy_version, template_version=template_version,
                            publication_resolver=publication_resolver)


def revalidate_domain_projection(manifest, *, repository, owner_id, policy, publication_resolver):
    with tempfile.TemporaryDirectory(prefix="knowledge-domain-revalidation-") as temp:
        result = project_domain_revision(repository, manifest["revision"], Path(temp) / "staging",
                                         owner_id=owner_id, policy=policy,
                                         policy_version=manifest["policy_version"],
                                         template_version=manifest["template_version"],
                                         publication_resolver=publication_resolver)
    _require(result["public_manifest"] == manifest, "DOMAIN_PROJECTION_CHANGED")
