"""Verify final Quartz output without deployment or model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import validate_public_artifacts
from .projection import ProjectionError
from .quartz import quartz_generated_files
from .revalidate import bind_domain_publication, revalidate_public_projection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--template-assets", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--domain-database", type=Path)
    parser.add_argument("--owner-id")
    parser.add_argument("--domain-policy-version", default="1")
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8-sig"))
    template = json.loads(args.template_assets.read_text(encoding="utf-8-sig"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    supplied = [bool(args.repository), bool(args.domain_database), bool(args.owner_id)]
    if any(supplied) and not all(supplied):
        parser.error("Repository, trusted domain database and owner are required together")
    if manifest.get("requires_domain_revalidation") and not all(supplied):
        raise ProjectionError(
            "Canonical public artifact requires final trusted domain revalidation"
        )
    if all(supplied):
        domain, resolver = bind_domain_publication(
            args.domain_database,
            args.owner_id,
            args.repository,
            domain_policy_version=args.domain_policy_version,
        )
        try:
            revalidate_public_projection(
                manifest, repository=args.repository, policy=policy, publication_resolver=resolver
            )
        finally:
            domain.close()
    if (
        args.staging
        and json.loads((args.staging / "publish-manifest.json").read_text(encoding="utf-8-sig"))
        != manifest
    ):
        raise ProjectionError("Staging manifest differs from the approved manifest")
    report = validate_public_artifacts(
        args.output,
        manifest,
        policy=policy,
        forbidden_fingerprints=policy.get("forbidden_fingerprints", []),
        trusted_template_assets=template["assets"],
        generated_files=quartz_generated_files(args.staging)
        if args.staging
        else tuple(
            template.get(
                "generated_files",
                ["index.html", "static/contentIndex.json", "sitemap.xml", "robots.txt"],
            )
        ),
    )
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.report:
        if args.report.resolve().is_relative_to(args.output.resolve()):
            parser.error("The internal verification report must be outside public output")
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
