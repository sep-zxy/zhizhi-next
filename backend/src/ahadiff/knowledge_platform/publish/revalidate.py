"""Revalidate a fixed public projection against its trusted domain immediately before release."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .projection import ProjectionError, PublicationResolver, project_git_revision


def bind_domain_publication(
    database: Path, owner_id: str, repository: Path, *, domain_policy_version: str = "1"
):
    """Open the trusted local ledger without initialization, migrations or recovery."""
    from ..auth import Actor
    from ..domain import DomainService

    if not database.is_file() or database.is_symlink() or not owner_id.strip():
        raise ProjectionError("An existing trusted domain database and owner are required")
    domain = DomainService.open_readonly(
        database, vault_path=repository, policy_version=domain_policy_version
    )
    if domain.writer.repository.resolve() != repository.resolve():
        domain.close()
        raise ProjectionError("Trusted domain ledger and publication Git repository differ")
    actor = Actor(owner_id, "deterministic-publisher", "service", frozenset({"knowledge:read"}))
    return domain, lambda entry: domain.verify_publication_entry(entry, actor=actor)


def revalidate_public_projection(
    public_manifest: dict[str, Any],
    *,
    repository: str | Path,
    policy: dict[str, Any],
    publication_resolver: PublicationResolver,
) -> None:
    """No RAW is exported: exact Git input is rechecked by the trusted certificate resolver."""
    with tempfile.TemporaryDirectory(prefix="knowledge-public-revalidation-") as temporary:
        result = project_git_revision(
            repository,
            public_manifest["revision"],
            Path(temporary) / "staging",
            policy=policy,
            policy_version=public_manifest["policy_version"],
            template_version=public_manifest["template_version"],
            publication_resolver=publication_resolver,
        )
    if result["public_manifest"] != public_manifest:
        raise ProjectionError("Publication eligibility, policy or immutable projection changed")
