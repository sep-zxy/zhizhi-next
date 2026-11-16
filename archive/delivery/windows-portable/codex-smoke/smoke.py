"""Prewritten Windows run-only smoke of the shipped backend adapter.

No login, credential reads, services, configuration edits or API fallback.
Only the official CLI may use/refresh the explicitly selected existing login.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
import time
import uuid

ROOT = Path(r"C:\Code\zhizhi-e2e-20261020")
ADAPTER_COMMIT = "95eab3d97f42ebc51ba45acdaadd679ea7eeac4b"
DISTRIBUTION_SHA256 = "91d453996e9dbc7c8f96ea2e716c6f6a25ae24ab7676a15eb0e111f932905ece"
PATCH_SHA256 = "6263a182c2805098ea700482c4214058aecb4bb680b01504a622de338a0bbd99"
MODEL = "gpt-6.1-sol"
CLI_VERSION = "0.163.0-alpha.10.2"


class SmokeBlocked(Exception):
    pass


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inside(root, relative):
    value = PurePosixPath(relative)
    if value.is_absolute() or ".." in value.parts or "\\" in relative or ":" in relative:
        raise SmokeBlocked("UNSAFE_REPORT_OR_MANIFEST_PATH")
    path = root.joinpath(*value.parts)
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (
            hasattr(ancestor, "is_junction") and ancestor.is_junction()
        ):
            raise SmokeBlocked("LINK_OR_JUNCTION_FORBIDDEN")
    if not path.resolve().is_relative_to(root.resolve()):
        raise SmokeBlocked("PATH_OUTSIDE_APPROVED_ROOT")
    return path


def verify_package(root, expected_digest=DISTRIBUTION_SHA256):
    manifest = inside(root, "provenance/distribution.json")
    if sha(manifest) != expected_digest:
        raise SmokeBlocked("EXACT_CODEX_PATCH_REQUIRED")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    for relative, digest in record["files"].items():
        if sha(inside(root, relative)) != digest:
            raise SmokeBlocked("IMMUTABLE_FILE_MISMATCH")
    for prefix in ("app", "runtime"):
        for path in inside(root, prefix).rglob("*"):
            relative = path.relative_to(root).as_posix()
            inside(root, relative)
            if path.is_file() and relative not in record["files"]:
                raise SmokeBlocked("UNLISTED_IMMUTABLE_FILE")
    for prefix in ("app/backend", "app/pi", "app/wiki"):
        component = inside(root, prefix + "/component.json")
        contents = json.loads(component.read_text(encoding="utf-8"))
        for relative, digest in contents["files"].items():
            if sha(inside(root, prefix + "/" + relative)) != digest:
                raise SmokeBlocked("COMPONENT_FILE_MISMATCH")
        for path in component.parent.rglob("*"):
            relative = path.relative_to(component.parent).as_posix()
            if path.is_file() and relative != "component.json" and relative not in contents["files"]:
                raise SmokeBlocked("UNLISTED_COMPONENT_FILE")
    return len(record["files"])


def acquire_launcher_lock(root):
    # Use the existing lock without creating or changing any state-file contents.
    path = inside(root, "data/launcher.lock")
    if not path.is_file():
        raise SmokeBlocked("LAUNCHER_LOCK_MISSING")
    import msvcrt

    lock = path.open("r+b")
    try:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except BaseException:
        lock.close()
        raise
    return lock


def isolated_environment(root, run_directory):
    """Only this process and its descendants change environment; no persistent edit."""
    keep = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    locations = {
        "HOME": "home", "USERPROFILE": "home", "APPDATA": "appdata",
        "LOCALAPPDATA": "localappdata", "TEMP": "temp", "TMP": "temp", "TMPDIR": "temp",
        "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "shared",
    }
    for key, relative in locations.items():
        path = inside(run_directory, relative)
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    system_root = next(
        (value for key, value in env.items() if key.upper() == "SYSTEMROOT"), r"C:\Windows"
    )
    env.update(
        PATH=os.pathsep.join([
            str(root / "runtime/node"), str(root / "runtime/git/cmd"),
            str(root / "runtime/python"), str(Path(system_root) / "System32"),
        ]),
        ZHIZHI_PORTABLE_ROOT=str(root),
        ZHIZHI_PORTABLE_STATE=str(run_directory),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        DO_NOT_TRACK="1",
    )
    os.environ.clear()
    os.environ.update(env)
    tempfile.tempdir = str(run_directory / "temp")


def fixed_config(executable, codex_home):
    return {
        "enabled": True,
        "adapter": "codex_cli",
        "reuse_existing_login": True,
        "executable": [str(executable)],
        "expected_version": CLI_VERSION,
        "codex_home": str(codex_home),
        "routes": [{
            "provider_id": "openai", "model_id": MODEL, "account_qualified": True,
            "allowed_classifications": ["public"],
        }],
        "timeout_seconds": 120,
        "max_input_bytes": 16384,
        "max_output_bytes": 1_048_576,
    }


def fixed_task_fields():
    return {
        "task_id": "codex-cli-public-arithmetic-smoke",
        "purpose": "answer",
        "input_refs": ("public:fixed-arithmetic-2-plus-2",),
        "input_classification": "public",
        "allowed_provider_ids": ("openai",),
        "schema_name": "codex-cli-arithmetic-smoke-v1",
        "output_schema": {
            "type": "object",
            "properties": {"answer": {"type": "integer", "enum": [4]}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        "payload": {"question": "What is 2 + 2?"},
        "dedupe_key": "codex-cli-public-arithmetic-smoke-v1",
        "max_output_tokens": 4096,
        "instructions": "Calculate 2 + 2. Return only the required JSON answer. Do not use tools.",
    }


def summarize(result):
    # No raw model output, stderr, paths, account identifiers or provider text in reports.
    from ahadiff.knowledge_platform.model.validation import sanitized_usage

    passed = (
        result.status == "succeeded"
        and result.output == {"answer": 4}
        and result.fixture is False
        and result.provider_id == "openai"
        and result.requested_model_id == MODEL
        and result.model_id is None
        and result.runtime_version == "codex-cli " + CLI_VERSION
    )
    return {
        "status": "adapter_smoke_passed" if passed else "adapter_smoke_not_passed",
        "adapter_status": result.status,
        "error_code": result.error_code,
        "fixture": result.fixture,
        "answer_matches_fixed_schema": result.output == {"answer": 4},
        "requested_model_id": MODEL,
        "actual_model_id": None,
        "actual_model_evidence": "unknown_in_exec_jsonl",
        "runtime_version": "codex-cli " + CLI_VERSION,
        "usage": sanitized_usage(result.usage),
        "tool_event_rejected": result.error_code == "UNEXPECTED_MODEL_TOOL",
        "tool_detection_is_not_rollback": True,
        "platform_e2e": "not_run",
    }


def execute_adapter(executable, codex_home):
    from ahadiff.knowledge_platform.model.config import build_runner_from_config
    from ahadiff.knowledge_platform.model.contracts import ModelTask

    runner = build_runner_from_config(fixed_config(executable, codex_home))
    result = asyncio.run(runner.run(ModelTask(**fixed_task_fields())))
    return summarize(result)


def main():
    parser = argparse.ArgumentParser(description="One fixed public request through the final adapter")
    parser.add_argument("--codex-exe", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--approve-existing-chatgpt-login", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    run_directory = None
    lock = None
    report = {
        "status": "blocked",
        "adapter_source_commit": ADAPTER_COMMIT,
        "patch_sha256": PATCH_SHA256,
        "platform_e2e": "not_run",
    }
    try:
        if sys.platform != "win32":
            raise SmokeBlocked("WINDOWS_ONLY_NO_CLOUD_AUTH_CALLS")
        if not args.approve_existing_chatgpt_login:
            raise SmokeBlocked("EXISTING_LOGIN_APPROVAL_REQUIRED")
        if not sys.dont_write_bytecode or not sys.flags.isolated:
            raise SmokeBlocked("REQUIRES_ISOLATED_PYTHON_AND_NO_BYTECODE")
        if Path(sys.executable).resolve() != inside(ROOT, "runtime/python/python.exe").resolve():
            raise SmokeBlocked("BUNDLED_PYTHON_REQUIRED")
        if (
            not args.codex_exe.is_absolute() or not args.codex_exe.is_file()
            or args.codex_exe.name.lower() != "codex.exe"
            or not args.codex_home.is_absolute()
        ):
            raise SmokeBlocked("EXISTING_ABSOLUTE_CODEX_PATHS_REQUIRED")
        # Metadata-only path checks; no traversal/read of the existing Codex home.
        report_root = inside(ROOT, "reports/codex-adapter-smoke")
        report_root.mkdir(parents=True, exist_ok=True)
        label = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        run_directory = inside(report_root, label)
        run_directory.mkdir()
        os.chdir(ROOT)
        lock = acquire_launcher_lock(ROOT)
        report["immutable_files_verified"] = verify_package(ROOT)
        isolated_environment(ROOT, run_directory)
        sys.path.insert(0, str(inside(ROOT, "app/backend")))
        for entry in sys.path:
            if not Path(entry).resolve().is_relative_to(ROOT.resolve()):
                raise SmokeBlocked("NON_PORTABLE_PYTHON_IMPORT_PATH")
        report.update(execute_adapter(args.codex_exe, args.codex_home))
    except SmokeBlocked as exc:
        report["error_code"] = str(exc)
    except Exception:
        report["error_code"] = "SMOKE_HOST_ERROR_REDACTED"
    finally:
        if lock is not None:
            lock.close()
    report["finished_utc"] = datetime.now(UTC).isoformat()
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if run_directory is not None:
        path = inside(run_directory, "report.json")
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print("Report: reports/codex-adapter-smoke/" + run_directory.name + "/report.json")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "adapter_smoke_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
