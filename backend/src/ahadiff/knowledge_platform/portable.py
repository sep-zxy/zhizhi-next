"""Fail-closed portable startup checks; not an operating-system sandbox.

No directories, credentials or state are created by this module. The launcher
must set the environment before starting Python, including disabling bytecode.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

_REQUIRED_PATHS = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
)
_OPTIONAL_PATHS = (
    "PYTHONPYCACHEPREFIX",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TORCH_HOME",
    "MPLCONFIGDIR",
    "NUMBA_CACHE_DIR",
    "PIP_CACHE_DIR",
    "UV_CACHE_DIR",
)


def portable_root() -> Path | None:
    raw = os.environ.get("ZHIZHI_PORTABLE_ROOT")
    if raw is None:
        if "ZHIZHI_PORTABLE_STATE" in os.environ:
            raise ValueError("Portable state requires a portable root")
        return None
    root = Path(raw)
    if not raw or not root.is_absolute() or not root.is_dir():
        raise ValueError("Portable root must be an existing absolute directory")
    if str(root).startswith(("\\\\", "//")):
        raise ValueError("Portable root must not be a network path")
    resolved = root.resolve()
    # Reject a redirected package anchor, including a junction in its parents.
    # normcase is case-insensitive on Windows, unlike string prefix checks.
    lexical = os.path.abspath(root)  # noqa: PTH100 -- must not follow links for this comparison
    if os.path.normcase(lexical) != os.path.normcase(str(resolved)):
        raise ValueError("Portable root must not be redirected through links or junctions")
    return resolved


def _inside(path: str | Path, root: Path, label: str) -> Path:
    candidate = Path(path)
    if not str(path) or not candidate.is_absolute():
        raise ValueError(f"{label} must be an absolute portable path")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} escapes the portable directory")
    return resolved


def require_portable_path(path: str | Path, *, state: bool = False) -> Path:
    """Validate before opening any domain state; resolve links/junctions first."""
    root = portable_root()
    if root is None:
        return Path(path)
    if state:
        raw = os.environ.get("ZHIZHI_PORTABLE_STATE", "")
        root = _inside(raw, root, "ZHIZHI_PORTABLE_STATE")
    return _inside(path, root, "Application path")


def validate_portable_runtime(
    state_directory: str | Path,
    *,
    write_paths: tuple[str | Path, ...] = (),
) -> dict[str, object]:
    """Check launch-time paths and bundled Git without writing or starting work."""
    root = portable_root()
    if root is None:
        return {"portable": False, "status": "not_enabled"}
    require_portable_path(state_directory, state=True)
    _inside(Path.cwd(), root, "Working directory")
    _inside(sys.executable, root, "Python executable")
    for entry in sys.path:
        _inside(Path(entry or ".").resolve(), root, "Python import path")
    for name in _REQUIRED_PATHS:
        _inside(os.environ.get(name, ""), root, name)
    for name in _OPTIONAL_PATHS:
        if name in os.environ:
            _inside(os.environ[name], root, name)
    for path in write_paths:
        _inside(path, root, "Application write path")
    if not sys.dont_write_bytecode:
        raise ValueError("Portable Python must start with -B (bytecode disabled)")
    if os.environ.get("PYTHONNOUSERSITE") != "1" or not sys.flags.no_user_site:
        raise ValueError("Portable Python requires disabled user site (-s / PYTHONNOUSERSITE=1)")
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("Bundled Git is required")
    _inside(executable, root, "Git executable")
    # tempfile may already have cached a location; checking only TEMP is insufficient.
    import tempfile

    if tempfile.tempdir is not None:
        _inside(os.fsdecode(tempfile.tempdir), root, "Cached temporary directory")
    temporary = Path(os.environ["TEMP"])
    if not temporary.is_dir() or not os.access(temporary, os.W_OK):
        raise ValueError("Portable TEMP must be an existing writable directory")
    if any(Path(os.environ[name]).resolve() != temporary.resolve() for name in ("TMP", "TMPDIR")):
        raise ValueError("Portable TEMP, TMP and TMPDIR must use the same directory")
    # Bind Python tempfile explicitly: never fall back to a system directory if
    # the package's TEMP later becomes unavailable. No file is created here.
    tempfile.tempdir = str(temporary.resolve())
    return {
        "portable": True,
        "status": "ok",
        "bytecode_disabled": True,
        "git_bundled": True,
        "scope": "application_paths_not_os_sandbox",
    }


def portable_child_environment() -> dict[str, str]:
    """Pass only validated application paths, never ambient secrets or proxies."""
    root = portable_root()
    if root is None:
        return {}
    state = require_portable_path(os.environ.get("ZHIZHI_PORTABLE_STATE", ""))
    result = {"ZHIZHI_PORTABLE_ROOT": str(root), "ZHIZHI_PORTABLE_STATE": str(state)}
    for name in _REQUIRED_PATHS:
        result[name] = str(_inside(os.environ.get(name, ""), root, name))
    for name in _OPTIONAL_PATHS:
        if name in os.environ:
            result[name] = str(_inside(os.environ[name], root, name))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only portable runtime preflight")
    parser.add_argument("--state-directory", required=True)
    parser.add_argument("--write-path", action="append", default=[])
    args = parser.parse_args()
    try:
        result = validate_portable_runtime(
            args.state_directory,
            write_paths=tuple(args.write_path),
        )
        if not result["portable"]:
            raise ValueError("Portable mode is not enabled")
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        raise SystemExit(2) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
