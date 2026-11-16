"""Bounded cleanup of one invocation-owned workspace; never chmod or follow links."""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
import stat
from pathlib import Path
from typing import Any


class UnsafeCleanupPath(ValueError):
    """The owned workspace cannot be inspected without following unsafe paths."""


def cleanup_failure(exc: BaseException) -> dict[str, Any]:
    """Fixed error vocabulary only: never exception text, filenames or class names."""
    kind = (
        "unsafe_path" if isinstance(exc, UnsafeCleanupPath)
        else "timeout" if isinstance(exc, TimeoutError)
        else "os_error" if isinstance(exc, OSError)
        else "cancelled" if isinstance(exc, asyncio.CancelledError)
        else "host_error"
    )

    def number(value: Any) -> int | None:
        return value if type(value) is int and -(2**31) <= value < 2**32 else None

    return {
        "failure_type": kind,
        "errno": number(getattr(exc, "errno", None)),
        "winerror": number(getattr(exc, "winerror", None)),
    }


def _inspect_tree(root: Path) -> bool:
    """Reject every existing ancestor/child reparse point and special file.

    This is a fail-closed application check, not an OS sandbox against another
    process modifying the same tree between inspection and deletion.
    """
    if not root.is_absolute():
        raise UnsafeCleanupPath()
    for ancestor in reversed((root, *root.parents)):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            if ancestor == root:
                return False
            raise
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise UnsafeCleanupPath()
        if not stat.S_ISDIR(metadata.st_mode):
            raise UnsafeCleanupPath()
    pending = [root]
    seen = 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                seen += 1
                if seen > 10_000:
                    raise UnsafeCleanupPath()
                metadata = entry.stat(follow_symlinks=False)
                if (stat.S_ISLNK(metadata.st_mode)
                        or getattr(metadata, "st_file_attributes", 0)
                        & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise UnsafeCleanupPath()
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(Path(entry.path))
                elif not stat.S_ISREG(metadata.st_mode):
                    raise UnsafeCleanupPath()
    return True


async def cleanup_owned_workspace(root: Path) -> dict[str, Any]:
    """Only the caller's freshly created mkdtemp root is eligible for cleanup.

    One retry is permitted for a known sharing/busy/nonempty condition after
    owned processes have been stopped. Permission/unknown/reparse failures do
    not trigger retries, permission changes, forced deletion or another model.
    """
    report: dict[str, Any] = {
        "resource": "temporary_directory", "status": "pending", "attempts": 0,
        "errors": [], "recovery": "none",
    }
    for attempt in (1, 2):
        report["attempts"] = attempt
        try:
            if _inspect_tree(root):
                shutil.rmtree(root)  # No onexc/onerror, chmod, or ignore_errors.
            report["status"] = "completed"
            return report
        except Exception as exc:
            report["errors"].append(cleanup_failure(exc))
            transient = isinstance(exc, OSError) and (
                getattr(exc, "winerror", None) in {32, 33, 145}
                or exc.errno in {errno.EBUSY, errno.ENOTEMPTY}
            )
            if attempt == 1 and transient:
                await asyncio.sleep(0.1)
                continue
            report["status"] = "failed"
            report["recovery"] = "owned_workspace_retained_for_review"
            return report
    raise AssertionError("Unreachable bounded cleanup")
