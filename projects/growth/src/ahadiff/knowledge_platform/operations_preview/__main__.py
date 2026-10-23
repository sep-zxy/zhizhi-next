"""Standalone local operator checks, deliberately absent from model tool registries."""
from __future__ import annotations

import argparse
import json
import sys

from .checks import PreviewError, inspect_backup, restore_plan, verify_publication_manifest
from .publication import eligibility, publish_preview, publish_build_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("backup-inspect", help="Verify an existing local snapshot")
    inspect.add_argument("snapshot")
    restore = commands.add_parser("restore-plan", help="Only a dry-run recovery plan")
    restore.add_argument("snapshot")
    restore.add_argument("--dry-run", action="store_true", required=True)
    publish = commands.add_parser("publish-verify", help="Revalidate an existing public manifest")
    publish.add_argument("--manifest", required=True)
    publish.add_argument("--repository", required=True)
    publish.add_argument("--database", required=True)
    publish.add_argument("--owner-id", required=True)
    publish.add_argument("--policy", required=True)
    publish.add_argument("--domain-policy-version", default="1")
    for name in ("eligibility", "publish-preview", "publish-build-plan"):
        command = commands.add_parser(name)
        command.add_argument("--repository", required=True)
        command.add_argument("--database", required=True)
        command.add_argument("--owner-id", required=True)
        command.add_argument("--domain-policy-version", default="1")
        if name == "eligibility":
            command.add_argument("entry_id")
        else:
            command.add_argument("--staging", required=True)
            command.add_argument("--policy", required=True)
        if name == "publish-preview":
            command.add_argument("--revision", required=True)
            command.add_argument("--policy-version", default="1")
            command.add_argument("--template-version", default="1")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup-inspect":
            result = inspect_backup(args.snapshot)
        elif args.command == "restore-plan":
            result = restore_plan(args.snapshot, dry_run=args.dry_run)
        elif args.command == "publish-verify":
            result = verify_publication_manifest(
                args.manifest, repository=args.repository, database=args.database,
                owner_id=args.owner_id, policy_path=args.policy,
                domain_policy_version=args.domain_policy_version,
            )
        else:
            common = dict(repository=args.repository, database=args.database,
                          owner_id=args.owner_id, domain_policy_version=args.domain_policy_version)
            if args.command == "eligibility":
                result = eligibility(args.entry_id, **common)
            elif args.command == "publish-preview":
                result = publish_preview(**common, staging=args.staging, revision=args.revision,
                                         policy_path=args.policy, policy_version=args.policy_version,
                                         template_version=args.template_version)
            else:
                result = publish_build_plan(**common, staging=args.staging, policy_path=args.policy)
    except Exception as exc:
        # Do not print raw exceptions: paths, YAML, DB rows and Git diagnostics
        # may contain private text. Machine-readable fixed codes only.
        code = str(exc) if isinstance(exc, PreviewError) else "VERIFICATION_FAILED"
        print(json.dumps({"status": "blocked", "code": code, "effect": "operation_not_completed"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
