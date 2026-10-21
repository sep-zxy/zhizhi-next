"""Opt-in operational acceptance identity, distinct from the synthetic test policy.

Only the explicitly approved Windows portable directory. Never activate in CI,
import a personal identity, read external secrets, or initialize on service start.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .auth import Actor
from .local_protection import LocalProtectionError, protect_current_user
from .owner_browser import OWNER_BROWSER_SCOPES
from .portable import portable_root, validate_portable_runtime

OWNER = "portable-operational-acceptance"
ACTOR = "portable-acceptance-person"
FORMAT = "zhizhi-owner-acceptance-dpapi-v1"
ENTROPY = b"zhizhi/portable/owner-operational-acceptance/v1"
APPROVED_ROOT = r"C:\Code\zhizhi-e2e-20261020"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{64}")


class OwnerIdentityError(ValueError):
    """Fixed codes only, no native diagnostic or identity values."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _safe(root: Path, path: Path) -> Path:
    if not path.is_absolute():
        raise OwnerIdentityError("OWNER_PATH_INVALID")
    for ancestor in (path, *path.parents):
        try:
            attributes = ancestor.lstat()
        except FileNotFoundError:
            attributes = None
        # Reject the attribute, including unknown tags. Inspection errors other
        # than a not-yet-created descendant must fail closed, not skip a parent.
        if attributes is not None and getattr(
            attributes, "st_file_attributes", 0
        ) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OwnerIdentityError("OWNER_PATH_REDIRECTED")
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise OwnerIdentityError("OWNER_PATH_REDIRECTED")
    resolved = path.resolve()
    lexical = os.path.abspath(path)  # noqa: PTH100 -- compare without following redirects
    if not resolved.is_relative_to(root) or os.path.normcase(lexical) != os.path.normcase(
        str(resolved)
    ):
        raise OwnerIdentityError("OWNER_PATH_INVALID")
    return path


def _acceptance_root() -> Path:
    if sys.platform != "win32":
        raise OwnerIdentityError("WINDOWS_REQUIRED")
    root = portable_root()
    approved = Path(APPROVED_ROOT)
    if root is None:
        raise OwnerIdentityError("PORTABLE_REQUIRED")
    try:
        parts = root.relative_to(approved).parts
    except ValueError:
        raise OwnerIdentityError("OWNER_ROOT_NOT_APPROVED") from None
    if parts and (
        len(parts) != 3
        or parts[:2] != ("git-delivery", "releases")
        or not re.fullmatch(r"[0-9a-f]{12}-[0-9]+-[0-9]+", parts[2])
    ):
        raise OwnerIdentityError("OWNER_ROOT_NOT_APPROVED")
    _safe(approved, root)
    validate_portable_runtime(root / "data", write_paths=(root / "data/owner-acceptance",))
    return root


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise OwnerIdentityError("OWNER_IDENTITY_UNAVAILABLE")
    with path.open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise OwnerIdentityError("OWNER_IDENTITY_INVALID")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise OwnerIdentityError("OWNER_IDENTITY_INVALID")
    return value


@dataclass(frozen=True)
class ProtectedOwnerIdentity:
    token: str = field(repr=False)
    device_id: str = field(repr=False)
    port: int

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def actor(self) -> Actor:
        # A user identity proves user-authored outcomes; worker/reviewer/writer
        # remain separate WorkflowSettings principals, not this browser token.
        return Actor(OWNER, ACTOR, "user", OWNER_BROWSER_SCOPES)

    def configuration(self) -> dict[str, Any]:
        return {
            "owner_browser": {"owner_id": OWNER, "actor_id": ACTOR, "origin": self.origin},
            "identities": [
                {
                    "owner_id": OWNER,
                    "actor_id": ACTOR,
                    "kind": "user",
                    "scopes": sorted(OWNER_BROWSER_SCOPES),
                    "allowed_classifications": ["public", "personal"],
                    "token": self.token,
                }
            ],
        }


def _launch(port: int) -> dict[str, Any]:
    return {
        "mode": "owner-acceptance",
        "owner_dpapi_identity": "data/owner-acceptance/identity.json",
        "platform_port": port,
    }


def load_owner_identity(
    path: Path,
    *,
    state_directory: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> ProtectedOwnerIdentity:
    root = _acceptance_root()
    directory = _safe(root, root / "data/owner-acceptance")
    if _safe(root, path) != directory / "identity.json" or host != "127.0.0.1":
        raise OwnerIdentityError("OWNER_CONFIGURATION_MISMATCH")
    if state_directory is not None and _safe(root, Path(state_directory)) != directory / "platform":
        raise OwnerIdentityError("OWNER_CONFIGURATION_MISMATCH")
    document = _read(path)
    if set(document) != {"format", "protected"} or document["format"] != FORMAT:
        raise OwnerIdentityError("OWNER_IDENTITY_INVALID")
    encoded = document["protected"]
    if not isinstance(encoded, str) or len(encoded) > 24000:
        raise OwnerIdentityError("OWNER_IDENTITY_INVALID")
    try:
        payload = json.loads(
            protect_current_user(
                base64.b64decode(encoded, validate=True), entropy_bytes=ENTROPY, decrypt=True
            )
        )
    except (LocalProtectionError, ValueError, UnicodeError):
        raise OwnerIdentityError("OWNER_PROTECTION_UNAVAILABLE") from None
    expected = {
        "version": 1,
        "acceptance_only": True,
        "fixture": False,
        "package_root": os.path.normcase(str(root)),
        "owner_id": OWNER,
        "actor_id": ACTOR,
        "scopes": sorted(OWNER_BROWSER_SCOPES),
        "kind": "user",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != set(expected) | {"token", "device_id", "port"}
        or any(payload.get(key) != value for key, value in expected.items())
        or type(payload["version"]) is not int
        or payload["fixture"] is not False
        or payload["acceptance_only"] is not True
        or not isinstance(payload["token"], str)
        or not _TOKEN.fullmatch(payload["token"])
        or not isinstance(payload["device_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", payload["device_id"])
        or type(payload["port"]) is not int
        or not 1024 <= payload["port"] <= 65535
    ):
        raise OwnerIdentityError("OWNER_IDENTITY_INVALID")
    identity = ProtectedOwnerIdentity(payload["token"], payload["device_id"], payload["port"])
    if port is not None and identity.port != port:
        raise OwnerIdentityError("OWNER_CONFIGURATION_MISMATCH")
    if _read(_safe(root, directory / "launch.json")) != _launch(identity.port):
        raise OwnerIdentityError("OWNER_CONFIGURATION_MISMATCH")
    for subdir in ("platform", "desktop"):
        if not _safe(root, directory / subdir).is_dir():
            raise OwnerIdentityError("OWNER_STATE_UNAVAILABLE")
    desktop = _read(_safe(root, directory / "desktop.json"))
    if (
        desktop.get("service_url") != identity.origin
        or desktop.get("identity_file") != "identity.json"
        or desktop.get("identity_format") != FORMAT
        or desktop.get("device_id") != identity.device_id
    ):
        raise OwnerIdentityError("OWNER_DESKTOP_BINDING_MISMATCH")
    return identity


def initialize_owner_identity(port: int) -> str:
    """Explicit operator action only. No caller-selected owner/path/scopes or imported state."""
    root = _acceptance_root()
    if type(port) is not int or not 1024 <= port <= 65535:
        raise OwnerIdentityError("OWNER_PORT_INVALID")
    directory = _safe(root, root / "data/owner-acceptance")
    if directory.exists():
        load_owner_identity(directory / "identity.json", port=port)
        return "already_initialized"
    payload = {
        "version": 1,
        "acceptance_only": True,
        "fixture": False,
        "package_root": os.path.normcase(str(root)),
        "owner_id": OWNER,
        "actor_id": ACTOR,
        "kind": "user",
        "scopes": sorted(OWNER_BROWSER_SCOPES),
        "token": secrets.token_urlsafe(48),
        "device_id": secrets.token_hex(16),
        "port": port,
    }
    document = {
        "format": FORMAT,
        "protected": base64.b64encode(
            protect_current_user(_canonical(payload), entropy_bytes=ENTROPY)
        ).decode("ascii"),
    }
    # Encrypt first; a crypto failure cannot create/adopt an identity directory.
    directory.mkdir(mode=0o700)
    with _safe(root, directory / "identity.json").open("xb") as stream:
        stream.write(_canonical(document) + b"\n")
    with _safe(root, directory / "launch.json").open("xb") as stream:
        stream.write(_canonical(_launch(port)) + b"\n")
    for name in ("platform", "desktop"):
        _safe(root, directory / name).mkdir()
    with _safe(root, directory / "desktop.json").open("xb") as stream:
        stream.write(
            _canonical(
                {
                    "service_url": f"http://127.0.0.1:{port}",
                    "identity_file": "identity.json",
                    "identity_format": FORMAT,
                    "device_id": payload["device_id"],
                    "input_classification": "personal",
                    "providers": [],
                    "model": {"enabled": False},
                }
            )
            + b"\n"
        )
    return "initialized"


def load_desktop_owner_identity(
    path: Path,
    *,
    state_directory: Path,
    service_url: str,
    device_id: str,
) -> ProtectedOwnerIdentity:
    """Only the same protected owner and isolated desktop may use this transport."""
    identity = load_owner_identity(path)
    root = _acceptance_root()
    if (
        _safe(root, state_directory) != root / "data/owner-acceptance/desktop/.ahadiff"
        or service_url != identity.origin
        or device_id != identity.device_id
    ):
        raise OwnerIdentityError("OWNER_DESKTOP_BINDING_MISMATCH")
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description="明确批准后的独立 Windows owner 验收身份初始化")
    parser.add_argument("--approve-owner-acceptance-identity", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    try:
        status = initialize_owner_identity(args.port)
    except (ValueError, OSError, TypeError):
        print("OWNER_IDENTITY_NOT_INITIALIZED", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps({"status": status, "acceptance_only": True, "live_e2e": "not_verified"}))


if __name__ == "__main__":
    main()
