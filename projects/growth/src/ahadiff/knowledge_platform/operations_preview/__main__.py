"""Standalone local operator checks, deliberately absent from model tool registries."""
from __future__ import annotations

import argparse
import json
import sys

from .checks import PreviewError, inspect_backup, restore_plan, verify_publication_manifest


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
    args = parser.parse_args(argv)
    try:
        if args.command == "backup-inspect":
            result = inspect_backup(args.snapshot)
        elif args.command == "restore-plan":
            result = restore_plan(args.snapshot, dry_run=args.dry_run)
        else:
            result = verify_publication_manifest(
                args.manifest, repository=args.repository, database=args.database,
                owner_id=args.owner_id, policy_path=args.policy,
                domain_policy_version=args.domain_policy_version,
            )
    except Exception as exc:
        # Do not print raw exceptions: paths, YAML, DB rows and Git diagnostics
        # may contain private text. Machine-readable fixed codes only.
        code = str(exc) if isinstance(exc, PreviewError) else "VERIFICATION_FAILED"
        print(json.dumps({"status": "blocked", "code": code, "effect": "no_live_change"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
