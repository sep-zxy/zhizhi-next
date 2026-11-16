"""Generate staging only; Quartz build/deployment remain separate approvals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .projection import project_git_revision
from .revalidate import bind_domain_publication


def main() -> None:
    """Run a fixed-revision deterministic export with an explicit rights policy."""
    parser = argparse.ArgumentParser(
        description="Create a model-free public Quartz staging projection"
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--policy-version", default="1")
    parser.add_argument("--template-version", default="1")
    parser.add_argument("--internal-manifest", type=Path)
    parser.add_argument("--domain-database", type=Path)
    parser.add_argument("--owner-id")
    parser.add_argument("--domain-policy-version", default="1")
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if args.internal_manifest and args.internal_manifest.resolve().is_relative_to(
        args.staging.resolve()
    ):
        parser.error("Internal manifest must be outside public staging")
    if bool(args.domain_database) != bool(args.owner_id):
        parser.error("Trusted domain database and owner must be supplied together")
    domain, resolver = (
        bind_domain_publication(
            args.domain_database,
            args.owner_id,
            args.repository,
            domain_policy_version=args.domain_policy_version,
        )
        if args.domain_database
        else (None, None)
    )
    try:
        result = project_git_revision(
            args.repository,
            args.revision,
            args.staging,
            policy=policy,
            policy_version=args.policy_version,
            template_version=args.template_version,
            publication_resolver=resolver,
        )
    finally:
        if domain is not None:
            domain.close()
    if args.internal_manifest:
        args.internal_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.internal_manifest.write_text(
            json.dumps(result["internal_manifest"], ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "build_hash": result["public_manifest"]["build_hash"],
                "counts": result["public_manifest"]["counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
