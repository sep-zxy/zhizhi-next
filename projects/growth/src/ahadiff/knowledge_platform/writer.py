"""Isolated Git object writer with ref CAS and verifiable crash recovery.

No user's working tree or index is ever modified. A temporary private index builds
the candidate tree; only update-ref with the approved old SHA makes it authoritative.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .contracts import identifier
from .errors import DomainError
from .git_process import run_git
from .portable import portable_root, require_portable_path


def _git_command(*args: str) -> list[str]:
    # These must be argv overrides: launcher GIT_CONFIG_* is intentionally scrubbed.
    executable = "git"
    if portable_root() is not None:
        found = shutil.which("git")
        if found is None:
            raise ValueError("Bundled Git is required")
        executable = str(require_portable_path(found))
    return [
        executable,
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        f"core.attributesFile={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "tag.gpgsign=false",
        "-c",
        "gc.auto=0",
        "-c",
        "maintenance.auto=false",
        "-c",
        "credential.helper=",
        "-c",
        "protocol.allow=never",
        *args,
    ]


class ReadOnlyGitWriter:
    """A restricted facade: publication can inspect existing Git, never initialize it."""

    def __init__(self, repository: str | Path) -> None:
        self.repository = require_portable_path(repository, state=True).resolve()
        if not (self.repository / "HEAD").is_file():
            raise DomainError(
                "NOT_FOUND", "Existing knowledge repository is required", status_code=404
            )
        self.ref = "refs/heads/knowledge-main"

    def _git(self, *args: str, **kwargs: Any) -> bytes:
        if not args or args[0] not in {"show", "ls-tree", "rev-parse", "cat-file"}:
            raise DomainError("FORBIDDEN", "Publication Git access is read-only", status_code=403)
        return GitWriter._git(self, *args, **kwargs)

    def _environment(self) -> dict[str, str]:
        return GitWriter._environment(self)

    def read_entry(self, commit: str, path: str) -> dict[str, Any]:
        return GitWriter.read_entry(self, commit, path)

    def file_hash(self, commit: str | None, path: str) -> str | None:
        return GitWriter.file_hash(self, commit, path)

    def head(self) -> str | None:
        result = self._git("rev-parse", "--verify", self.ref, allow_failure=True).decode().strip()
        return result or None


class GitWriter:
    """Write a dedicated bare knowledge repository, never an arbitrary checkout."""

    def __init__(self, repository: str | Path, *, ref: str = "refs/heads/knowledge-main") -> None:
        self.repository = require_portable_path(repository, state=True).resolve()
        self.ref = ref
        if not ref.startswith("refs/heads/") or any(char in ref for char in (" ", "..", "\n", ":")):
            raise ValueError("Invalid authoritative ref")
        self._initialized = False

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("GIT_"):
                del env[key]
        env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_SYSTEM=os.devnull,
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_ATTR_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Knowledge Writer",
            GIT_AUTHOR_EMAIL="writer@localhost",
            GIT_COMMITTER_NAME="Knowledge Writer",
            GIT_COMMITTER_EMAIL="writer@localhost",
            GIT_TERMINAL_PROMPT="0",
        )
        return env

    def _git(
        self,
        *args: str,
        input_data: bytes | None = None,
        environment: dict[str, str] | None = None,
        allow_failure: bool = False,
    ) -> bytes:
        result = run_git(
            _git_command("--git-dir", str(self.repository), *args),
            input_data=input_data,
            env=environment or self._environment(),
            cwd=self.repository,
            timeout=30,
        )
        if result.returncode and not allow_failure:
            raise DomainError(
                "GIT_FAILURE",
                "The knowledge Git operation failed; no destructive retry was made",
                retryable=True,
                status_code=503,
            )
        return result.stdout if result.returncode == 0 else b""

    def initialize(self) -> None:
        """Initialize only an empty dedicated path; reject non-bare/user repositories."""
        if self._initialized:
            return
        if self.repository.exists() and any(self.repository.iterdir()):
            if self._git("rev-parse", "--is-bare-repository").strip() != b"true":
                raise DomainError(
                    "UNSAFE_REPOSITORY",
                    "Knowledge writer requires a dedicated bare repository",
                    status_code=409,
                )
        else:
            self.repository.mkdir(parents=True, exist_ok=True)
            result = run_git(
                _git_command("init", "--bare", "--template=", str(self.repository)),
                timeout=30,
                env=self._environment(),
                cwd=self.repository,
            )
            if result.returncode:
                raise DomainError(
                    "GIT_FAILURE", "Could not initialize the knowledge repository", status_code=503
                )
        self._initialized = True

    def head(self) -> str | None:
        """Read the authoritative ref; an empty dedicated repository has no HEAD yet."""
        self.initialize()
        result = self._git("rev-parse", "--verify", self.ref, allow_failure=True).decode().strip()
        return result or None

    def file_hash(self, commit: str | None, path: str) -> str | None:
        """Hash the exact formal file, distinguishing an absent path from an empty file."""
        if commit is None:
            return None
        if not self._git("ls-tree", "--full-tree", commit, "--", path).strip():
            return None
        return hashlib.sha256(self._git("show", f"{commit}:{path}")).hexdigest()

    @staticmethod
    def path_for(owner_id: str, entry_id: str) -> str:
        """Derive paths exclusively from a hashed owner namespace and stable entry ID."""
        identifier(entry_id, "entry_id")
        owner_namespace = hashlib.sha256(owner_id.encode()).hexdigest()[:24]
        return f"entries/{owner_namespace}/{entry_id}.md"

    @staticmethod
    def document(entry: dict[str, Any]) -> bytes:
        """Render canonical metadata and exact body as the sole formal Markdown source."""
        frontmatter = {
            key: value
            for key, value in entry.items()
            if key not in {"body", "commit_sha", "path", "owner_id"}
        }
        frontmatter["schema_version"] = 6
        rendered = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=True, width=120)
        return ("---\n" + rendered + "---\n" + entry["body"]).encode("utf-8")

    def prepare(self, change: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        """Create an immutable commit object without moving any ref."""
        self.initialize()
        base = change.get("base_git_revision")
        if self.head() != base:
            raise DomainError("BASE_STALE", "The approved Git base has changed", status_code=409)
        path = self.path_for(change["owner_id"], change["entry_id"])
        content = self.document(entry)
        blob = self._git("hash-object", "-w", "--stdin", input_data=content).decode().strip()
        with tempfile.TemporaryDirectory(
            prefix="knowledge-index-", dir=self.repository
        ) as temporary:
            environment = self._environment()
            environment["GIT_INDEX_FILE"] = str(Path(temporary) / "index")
            self._git("read-tree", base or "--empty", environment=environment)
            self._git(
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{path}",
                environment=environment,
            )
            tree = self._git("write-tree", environment=environment).decode().strip()
        args = ["commit-tree", tree]
        if base:
            args.extend(["-p", base])
        message = (
            f"知识变更 {change['change_id']}\n\nChange-Id: {change['change_id']}\n"
            f"Patch-Hash: {change['patch_hash']}\n"
        )
        # Stable dates make retries before journal persistence reproduce the same commit.
        environment = self._environment()
        environment["GIT_AUTHOR_DATE"] = entry["updated_at"]
        environment["GIT_COMMITTER_DATE"] = entry["updated_at"]
        commit = (
            self._git(*args, input_data=message.encode("utf-8"), environment=environment)
            .decode()
            .strip()
        )
        return {
            "change_id": change["change_id"],
            "owner_id": change["owner_id"],
            "base_commit": base,
            "commit_sha": commit,
            "path": path,
            "document_hash": hashlib.sha256(content).hexdigest(),
            "entry": entry,
            "trace_id": change.get("trace_id"),
            "patch_hash": change["patch_hash"],
        }

    def verify(self, journal: dict[str, Any]) -> None:
        """Prove commit identity, parent and resulting file before any reconciliation."""
        commit = journal["commit_sha"]
        message = self._git("show", "-s", "--format=%B", commit).decode("utf-8")
        if f"Change-Id: {journal['change_id']}\n" not in message:
            raise DomainError(
                "RECONCILE_REQUIRED", "Commit does not prove the expected change", status_code=409
            )
        parents = self._git("show", "-s", "--format=%P", commit).decode().strip().split()
        if parents != ([journal["base_commit"]] if journal["base_commit"] else []):
            raise DomainError(
                "RECONCILE_REQUIRED",
                "Commit base does not match the durable journal",
                status_code=409,
            )
        content = self._git("show", f"{commit}:{journal['path']}")
        if hashlib.sha256(content).hexdigest() != journal["document_hash"]:
            raise DomainError(
                "RECONCILE_REQUIRED", "Committed document failed verification", status_code=409
            )

    def contains(self, commit: str, head: str | None = None) -> bool:
        """Check ancestry, not HEAD equality, so recovery handles later commits safely."""
        head = head or self.head()
        if head is None:
            return False
        result = run_git(
            _git_command(
                "--git-dir", str(self.repository), "merge-base", "--is-ancestor", commit, head
            ),
            timeout=30,
            env=self._environment(),
            cwd=self.repository,
        )
        if result.returncode not in {0, 1}:
            raise DomainError(
                "RECONCILE_REQUIRED", "Could not establish commit ancestry", status_code=409
            )
        return result.returncode == 0

    def publish(self, journal: dict[str, Any]) -> None:
        """CAS a verified prepared commit, or recognize it as already applied once."""
        self.verify(journal)
        current = self.head()
        if current and self.contains(journal["commit_sha"], current):
            return
        if current != journal["base_commit"]:
            raise DomainError(
                "BASE_STALE",
                "Authoritative Git ref advanced; both versions are preserved",
                status_code=409,
            )
        self._git("update-ref", self.ref, journal["commit_sha"], current or "0" * 40)

    def read_entry(self, commit: str, path: str) -> dict[str, Any]:
        """Read a specific immutable Markdown revision, never a mutable DB body copy."""
        content = self._git("show", f"{commit}:{path}").decode("utf-8")
        if not content.startswith("---\n") or "\n---\n" not in content[4:]:
            raise DomainError(
                "CONTENT_CORRUPT", "Formal entry has invalid frontmatter", status_code=409
            )
        raw, body = content[4:].split("\n---\n", 1)
        metadata = yaml.safe_load(raw)
        if not isinstance(metadata, dict):
            raise DomainError(
                "CONTENT_CORRUPT", "Formal entry metadata is invalid", status_code=409
            )
        return {**metadata, "body": body, "path": path, "commit_sha": commit}
