"""Private expiring intake blobs, separate from retained domain objects."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..intake.contracts import IntakeError

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}")
_DIGEST = re.compile(r"[a-f0-9]{64}")


class IntakeCache:
    """Content-addressed cache restricted to domain-provided intake identifiers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        if root.is_symlink():
            raise ValueError("Intake cache cannot be a symbolic link")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve()

    def _directory(self, intake_id: str) -> Path:
        if not _IDENTIFIER.fullmatch(intake_id):
            raise IntakeError("INVALID_INTAKE_ID", "收件标识无效。")
        directory = self.root / intake_id
        if directory.is_symlink():
            raise IntakeError("UNSAFE_CACHE_PATH", "临时素材路径无效。", status_code=500)
        return directory

    def put(self, intake_id: str, document: dict[str, Any]) -> dict[str, Any]:
        """Atomically save a bounded JSON bundle; return an opaque digest, not a path."""
        body = json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > 24 * 1024 * 1024:
            raise IntakeError("CONTENT_TOO_LARGE", "临时素材包超过大小限制。")
        digest = hashlib.sha256(body).hexdigest()
        directory = self._directory(intake_id)
        # Reads and cleanup must not recreate directories another keep removed.
        directory.mkdir(exist_ok=True, mode=0o700)
        target = directory / (digest + ".json")
        if target.is_symlink():
            raise IntakeError("UNSAFE_CACHE_PATH", "临时素材路径无效。", status_code=500)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            Path(temporary).replace(target)
            if hasattr(os, "O_DIRECTORY"):
                dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"sha256": digest, "bytes": len(body), "content_type": "application/json"}

    def path(self, intake_id: str, digest: str) -> Path:
        """Resolve and verify a cached blob before passing it to the domain writer."""
        if not _DIGEST.fullmatch(digest):
            raise IntakeError("INVALID_CACHE_REFERENCE", "临时素材引用无效。")
        path = self._directory(intake_id) / (digest + ".json")
        if path.is_symlink() or not path.is_file():
            raise IntakeError(
                "CACHE_UNAVAILABLE", "临时素材已不可用，请重新提交。", status_code=409
            )
        if path.stat().st_size > 24 * 1024 * 1024:
            raise IntakeError("CONTENT_TOO_LARGE", "临时素材包超过大小限制。")
        # This cache contains only server-created files under the private service
        # account; request data cannot select a filesystem path.
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise IntakeError("CACHE_INTEGRITY", "临时素材校验失败，未收藏。", status_code=409)
        return path

    def read(self, intake_id: str, digest: str) -> dict[str, Any]:
        """Read only an integrity-checked server cache bundle for durable resume."""
        return json.loads(self.path(intake_id, digest).read_text(encoding="utf-8"))

    def discard(self, intake_id: str) -> None:
        """Delete only temporary blobs after a durable discarded/expired decision."""
        directory = self._directory(intake_id)
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            return
        for item in entries:
            if item.is_symlink() or not item.is_file():
                continue
            if _DIGEST.fullmatch(item.stem) and item.suffix == ".json":
                item.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            directory.rmdir()

    def intake_ids(self, *, limit: int = 1000) -> list[str]:
        """List bounded server-created identifiers, never follow directory symlinks."""
        if not 1 <= limit <= 10_000:
            raise ValueError("Cache sweep limit must be between 1 and 10000")
        identifiers: list[str] = []
        for path in self.root.iterdir():
            if not path.is_symlink() and path.is_dir() and _IDENTIFIER.fullmatch(path.name):
                identifiers.append(path.name)
            if len(identifiers) >= limit:
                break
        return identifiers
