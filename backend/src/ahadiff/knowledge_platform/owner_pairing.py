"""Explicit local approval using existing protected configuration; never mint secrets."""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .auth import Actor, TokenAuthenticator
from .config_io import load_config_object, string_list
from .errors import DomainError
from .owner_browser import OwnerBrowserSessions, configured_owner_policy


def approve_existing_owner(
    path: Path,
    pairing_code: str,
    *,
    protected_current_user: bool = False,
) -> str:
    """One bounded request. No URL argument, redirects, proxy, output token or retries."""
    if not re.fullmatch(r"[A-F0-9]{20}", pairing_code):
        raise ValueError("Invalid pairing code")
    from .portable import portable_root, require_portable_path

    if portable_root() is not None:
        require_portable_path(path)
    if protected_current_user:
        from .owner_identity import load_owner_identity

        config = load_owner_identity(path).configuration()
    else:
        config = load_config_object(path, secret=True)
    return _approve(config, pairing_code, protected_current_user=protected_current_user)


def _approve(config: dict[str, Any], pairing_code: str, *, protected_current_user: bool) -> str:
    policy = configured_owner_policy(config, protected_current_user=protected_current_user)
    records = config.get("identities")
    if not isinstance(records, list):
        raise ValueError("Identity mapping unavailable")
    matches = [
        item
        for item in records
        if isinstance(item, dict)
        and item.get("owner_id") == policy["owner_id"]
        and item.get("actor_id") == policy["actor_id"]
    ]
    if len(matches) != 1:
        raise ValueError("Identity mapping is absent or ambiguous")
    item = matches[0]
    token = item.get("token")
    kind = item.get("kind")
    if not isinstance(token, str) or not isinstance(kind, str):
        raise ValueError("Credential unavailable")
    actor = Actor(
        policy["owner_id"],
        policy["actor_id"],
        kind,
        frozenset(string_list(item.get("scopes", []), "Identity scopes")),
        frozenset(
            string_list(
                item.get("allowed_classifications", ["public", "personal"]),
                "Identity classifications",
            )
        ),
    )
    authenticator = TokenAuthenticator({token: actor})
    sessions = OwnerBrowserSessions(
        policy["origin"], authenticator, owner_id=policy["owner_id"], actor_id=policy["actor_id"]
    )
    sessions.validate_identities([actor])
    url = urlsplit(policy["origin"])
    connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=5)
    try:
        connection.request(
            "POST",
            sessions.auth_prefix + "/approve",
            body=json.dumps({"pairing_code": pairing_code}),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read(4097)
        if response.status != 200 or len(raw) > 4096:
            raise ValueError("Approval not confirmed")
        value = json.loads(raw)
        if value != {
            "status": "pairing_approved",
            "fixture": False,
            "authentication_mode": "owner_loopback",
        }:
            raise ValueError("Approval response is invalid")
    finally:
        connection.close()
    return "pairing_approved"


def main() -> None:
    parser = argparse.ArgumentParser(description="批准已配置 owner 的一次性本机浏览器配对")
    identities = parser.add_mutually_exclusive_group(required=True)
    identities.add_argument("--identities-file", type=Path)
    identities.add_argument("--owner-dpapi-identity", type=Path)
    args = parser.parse_args()
    try:
        # No argv/history or URL code. Bounded input, no echo or raw diagnostics.
        line = sys.stdin.readline(23)
        code = line.rstrip("\r\n")
        if len(line) > 22:
            raise ValueError("Pairing input is too long")
        status = approve_existing_owner(
            args.owner_dpapi_identity or args.identities_file,
            code,
            protected_current_user=args.owner_dpapi_identity is not None,
        )
    except (OSError, ValueError, TypeError, DomainError, http.client.HTTPException):
        print("OWNER_PAIRING_NOT_CONFIRMED", file=sys.stderr)
        raise SystemExit(2) from None
    print(status)


if __name__ == "__main__":
    main()
