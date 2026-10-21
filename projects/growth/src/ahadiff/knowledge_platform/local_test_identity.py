"""Windows-only, explicitly approved portable TEST identity initialization.

Never import a personal credential, initialize in CI, or use this for production.
The service bearer is encrypted with current-user DPAPI and never printed.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from .browser_sessions import AUTH_PREFIX, TEST_ACTOR, TEST_OWNER, TEST_SCOPES
from .portable import portable_root, validate_portable_runtime

_APPROVED_ROOT = r"C:\Code\zhizhi-e2e-20261020"
_RELEASE_NAME = re.compile(r"[0-9a-f]{12}-[0-9]+-[0-9]+")
_FORMAT = "zhizhi-local-test-dpapi-v1"
_ENTROPY = b"zhizhi/portable/approved-local-test/v1"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{64}")
_CODE = re.compile(r"[A-F0-9]{20}")


class LocalTestIdentityError(ValueError):
    """Fixed diagnostic code only; never include paths, secrets or OS messages."""


@dataclass(frozen=True)
class LocalTestIdentity:
    token: str = field(repr=False)
    device_id: str = field(repr=False)
    port: int

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _safe_path(root: Path, path: Path) -> Path:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise LocalTestIdentityError("REDIRECTED_TEST_PATH")
    resolved = path.resolve()
    lexical = os.path.abspath(path)  # noqa: PTH100 -- compare without following redirections
    if os.path.normcase(lexical) != os.path.normcase(str(resolved)):
        raise LocalTestIdentityError("REDIRECTED_TEST_PATH")
    if not path.is_absolute() or not resolved.is_relative_to(root):
        raise LocalTestIdentityError("TEST_PATH_OUTSIDE_PACKAGE")
    return path


def _test_root() -> Path:
    if sys.platform != "win32":
        raise LocalTestIdentityError("WINDOWS_REQUIRED")
    root = portable_root()
    approved = Path(_APPROVED_ROOT)
    if root is None:
        raise LocalTestIdentityError("PORTABLE_RUNTIME_REQUIRED")
    try:
        parts = root.relative_to(approved).parts
    except ValueError:
        raise LocalTestIdentityError("UNAPPROVED_TEST_ROOT") from None
    if parts and (
        len(parts) != 3 or parts[:2] != ("git-delivery", "releases")
        or not _RELEASE_NAME.fullmatch(parts[2])
    ):
        raise LocalTestIdentityError("UNAPPROVED_TEST_ROOT")
    _safe_path(approved, root)
    validate_portable_runtime(root / "data", write_paths=(root / "data/local-test",))
    return root


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    """Current-user DPAPI, UI forbidden, no machine-wide protection."""
    if sys.platform != "win32":
        raise LocalTestIdentityError("WINDOWS_REQUIRED")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(
        len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output = Blob()
    # LOAD_LIBRARY_SEARCH_SYSTEM32: do not load a same-named DLL from the package.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True, winmode=0x800)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True, winmode=0x800)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
        ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    try:
        if not operation(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None,
            0x1, ctypes.byref(output),  # CRYPTPROTECT_UI_FORBIDDEN, not LOCAL_MACHINE.
        ):
            raise LocalTestIdentityError("LOCAL_PROTECTION_FAILED")
        if not output.data or not 0 < output.size <= 32768:
            raise LocalTestIdentityError("LOCAL_PROTECTION_INVALID")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_buffer, 0, len(data))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _launch(port: int) -> dict[str, Any]:
    return {
        "mode": "local-test", "local_test_identity": "data/local-test/identity.json",
        "platform_port": port,
    }


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(32769)
    if len(data) > 32768:
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY") from None
    if not isinstance(value, dict):
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    return cast("dict[str, Any]", value)


def _load(root: Path) -> LocalTestIdentity:
    directory = _safe_path(root, root / "data/local-test")
    document = _read_json(_safe_path(root, directory / "identity.json"))
    if set(document) != {"format", "protected"} or document["format"] != _FORMAT:
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    encoded = document["protected"]
    if not isinstance(encoded, str) or len(encoded) > 24000:
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    try:
        plaintext = _dpapi(base64.b64decode(encoded, validate=True), decrypt=True)
        protected = json.loads(plaintext)
    except (ValueError, UnicodeError):
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY") from None
    if not isinstance(protected, dict):
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    protected = cast("dict[str, Any]", protected)
    if set(protected) != {"payload", "sha256"}:
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    payload = protected["payload"]
    digest = protected["sha256"]
    if (
        not isinstance(payload, dict) or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not hmac.compare_digest(hashlib.sha256(_canonical(payload)).hexdigest(), digest)
    ):
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    payload = cast("dict[str, Any]", payload)
    expected = {
        "version": 1, "fixture": True, "package_root": os.path.normcase(str(root)),
        "owner_id": TEST_OWNER, "actor_id": TEST_ACTOR, "kind": "user",
        "scopes": sorted(TEST_SCOPES), "allowed_classifications": ["public"],
    }
    if (
        set(payload) != set(expected) | {"token", "device_id", "port"}
        or any(payload.get(key) != value for key, value in expected.items())
        or type(payload["version"]) is not int or payload["fixture"] is not True
        or not isinstance(payload["token"], str) or not _TOKEN.fullmatch(payload["token"])
        or not isinstance(payload["device_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", payload["device_id"])
        or type(payload["port"]) is not int or not 1024 <= payload["port"] <= 65535
    ):
        raise LocalTestIdentityError("INVALID_TEST_IDENTITY")
    identity = LocalTestIdentity(payload["token"], payload["device_id"], payload["port"])
    if _read_json(_safe_path(root, directory / "launch.json")) != _launch(identity.port):
        raise LocalTestIdentityError("TEST_CONFIGURATION_MISMATCH")
    if not _safe_path(root, directory / "platform").is_dir():
        raise LocalTestIdentityError("TEST_STATE_MISSING")
    return identity


def load_local_test_identity(
    path: Path, *, state_directory: str | Path, host: str, port: int
) -> LocalTestIdentity:
    """Load only the newly approved test file, never a caller-selected credential."""
    root = _test_root()
    expected = root / "data/local-test"
    if (
        _safe_path(root, path) != expected / "identity.json"
        or _safe_path(root, Path(state_directory)) != expected / "platform"
        or host != "127.0.0.1"
    ):
        raise LocalTestIdentityError("TEST_CONFIGURATION_MISMATCH")
    identity = _load(root)
    if identity.port != port:
        raise LocalTestIdentityError("TEST_CONFIGURATION_MISMATCH")
    return identity


def initialize_local_test_identity(port: int) -> str:
    """Explicit CLI action only; refuse adopting, overwriting or merging test state."""
    root = _test_root()
    if type(port) is not int or not 1024 <= port <= 65535:
        raise LocalTestIdentityError("INVALID_TEST_PORT")
    directory = _safe_path(root, root / "data/local-test")
    if directory.exists():
        identity = _load(root)
        if identity.port != port:
            raise LocalTestIdentityError("TEST_CONFIGURATION_MISMATCH")
        return "already_initialized"
    payload = {
        "version": 1, "fixture": True, "package_root": os.path.normcase(str(root)),
        "owner_id": TEST_OWNER, "actor_id": TEST_ACTOR, "kind": "user",
        "scopes": sorted(TEST_SCOPES), "allowed_classifications": ["public"],
        "token": secrets.token_urlsafe(48), "device_id": secrets.token_hex(16), "port": port,
    }
    plaintext = _canonical(
        {"payload": payload, "sha256": hashlib.sha256(_canonical(payload)).hexdigest()}
    )
    document = {
        "format": _FORMAT, "protected": base64.b64encode(_dpapi(plaintext)).decode("ascii")
    }
    # No replace/overwrite, even during concurrent initialization. A partial write
    # is refused on restart rather than silently minting a different identity.
    directory.mkdir(mode=0o700)
    with _safe_path(root, directory / "identity.json").open("xb") as stream:
        stream.write(_canonical(document) + b"\n")
    with _safe_path(root, directory / "launch.json").open("xb") as stream:
        stream.write(_canonical(_launch(port)) + b"\n")
    _safe_path(root, directory / "platform").mkdir()
    return "initialized"


def pair_local_test_identity(code: str) -> str:
    if not _CODE.fullmatch(code):
        raise LocalTestIdentityError("INVALID_PAIRING_CODE")
    root = _test_root()
    identity = _load(root)
    # No proxy, DNS lookup, redirects or ambient cookie/auth configuration.
    connection = http.client.HTTPConnection("127.0.0.1", identity.port, timeout=5)
    try:
        connection.request(
            "POST", AUTH_PREFIX + "/approve", body=_canonical({"pairing_code": code}),
            headers={
                "Authorization": "Bearer " + identity.token,
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        body = response.read(4097)
        if (
            response.status != 200 or len(body) > 4096
            or json.loads(body) != {"status": "pairing_approved", "fixture": True}
        ):
            raise LocalTestIdentityError("PAIRING_NOT_APPROVED")
    finally:
        connection.close()
    return "pairing_approved"


def main() -> None:
    parser = argparse.ArgumentParser(description="Approved isolated Windows test identity only")
    subcommands = parser.add_subparsers(dest="action", required=True)
    initialize = subcommands.add_parser("initialize")
    initialize.add_argument("--approve-local-test-identity", action="store_true", required=True)
    initialize.add_argument("--port", type=int, default=8766)
    pair = subcommands.add_parser("pair")
    pair.add_argument("--pairing-code", required=True)
    args = parser.parse_args()
    try:
        status = (
            initialize_local_test_identity(args.port)
            if args.action == "initialize" else pair_local_test_identity(args.pairing_code)
        )
    except LocalTestIdentityError as exc:
        print(json.dumps({"status": "failed", "code": exc.args[0], "fixture": True}))
        raise SystemExit(2) from None
    except (ValueError, OSError, http.client.HTTPException):
        print(json.dumps({"status": "failed", "code": "LOCAL_TEST_OPERATION_FAILED",
                          "fixture": True}))
        raise SystemExit(2) from None
    print(json.dumps({"status": status, "fixture": True, "live_e2e": "not_verified"}))


if __name__ == "__main__":
    main()
