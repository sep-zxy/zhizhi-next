"""Read-only OpenBao KV v2 boundary for trusted service startup, never an AI tool.

Protocol: https://openbao.org/docs/secrets/kv/kv-v2/
Only administrator-configured references can be resolved. This module does not
initialize/unseal a server, create tokens, write secrets or grant permissions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from .config_io import load_config_object

if TYPE_CHECKING:
    from pathlib import Path


class SecretUnavailable(RuntimeError):
    """A deliberately generic error, safe for task status and logs."""


@dataclass(frozen=True)
class SecretReference:
    mount: str
    path: str
    key: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.mount):
            raise ValueError("Invalid OpenBao mount")
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", self.path):
            raise ValueError("Invalid OpenBao secret reference")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.key):
            raise ValueError("Invalid OpenBao field")


class OpenBaoReader:
    """Exact-path allowlist plus the server's own least-privilege ACL.

    The bootstrap identity file is an approved protected mount, not a token
    obtained from a model, source document or incoming channel message.
    """

    def __init__(
        self,
        address: str,
        identity_file: Path,
        references: dict[str, SecretReference],
        *,
        client: httpx.Client | None = None,
    ) -> None:
        url = urlsplit(address)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("OpenBao requires a credential-free HTTPS origin")
        self.address = address.rstrip("/")
        self.identity_file = identity_file
        self.references = dict(references)
        self.client = client

    def resolve(self, name: str) -> str:
        if name not in self.references:
            raise SecretUnavailable("SECRET_REFERENCE_NOT_ALLOWED")
        ref = self.references[name]
        try:
            identity = load_config_object(self.identity_file, secret=True)
            token = identity.get("token")
            if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
                raise ValueError("Missing identity")
            url = f"{self.address}/v1/{ref.mount}/data/{ref.path}"
            headers = {"X-Vault-Token": token}
            if self.client is None:
                with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                    response = client.get(url, headers=headers)
            else:
                response = self.client.get(url, headers=headers, follow_redirects=False)
            if response.status_code != 200 or len(response.content) > 128 * 1024:
                raise ValueError("Unavailable")
            value = response.json()["data"]["data"][ref.key]
            if not isinstance(value, str) or not value or len(value) > 65536:
                raise ValueError("Invalid secret value")
            return value
        except Exception:
            # Neither HTTP bodies nor exception chains may expose token contents.
            raise SecretUnavailable("SECRET_UNAVAILABLE") from None
