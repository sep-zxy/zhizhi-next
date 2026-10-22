"""Storage adapters for the desktop client, not a second knowledge authority.

An outbox contains immutable requests awaiting acknowledgement.  It never stores
an alternative official Wiki body or claims that an offline request committed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

import httpx
import portalocker

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def canonical_json(value: Any) -> str:
    """Produce the stable representation used for immutable request identities."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class BackendError(Exception):
    """A transport or domain error that retains retry and conflict semantics."""

    code: str
    detail: str
    retryable: bool = False
    status_code: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class KnowledgeBackend(Protocol):
    """Desktop boundary for learning facts and reviewed Wiki proposals."""

    async def submit_learning_outcome(self, outcome: Mapping[str, Any]) -> dict[str, Any]:
        """Submit one immutable outcome revision; never infer mastery."""
        ...

    async def propose_wiki_change(
        self, proposal: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        """Propose a change through the common review route, not a file write."""
        ...

    async def read_entry(self, entry_id: str) -> dict[str, Any]:
        """Read the canonical entry visible to the authenticated principal."""
        ...


def outcome_key(outcome: Mapping[str, Any]) -> str:
    """Scope replay to a device, concept, learning session and outcome revision."""
    fields = ("device_id", "session_id", "version")
    values: list[str] = []
    for field in fields:
        value = outcome.get(field)
        if value is None or not str(value).strip():
            raise BackendError("INVALID_INPUT", f"Missing {field}")
        values.append(str(value))
    return "learning:" + hashlib.sha256(canonical_json(values).encode()).hexdigest()


class DurableOutbox:
    """Atomic, disk-backed client outbox with payload conflict detection.

    Persist before attempting network I/O. A timeout leaves the same request and
    key available for replay. Terminal rejection is retained for user inspection,
    including both local and remote conflict details, instead of overwritten.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(canonical_json(record))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def enqueue(self, key: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a request or return its existing state without changing it."""
        with portalocker.Lock(str(self.directory / ".outbox.lock"), timeout=10):
            return self._enqueue(key, path, payload)

    def _enqueue(self, key: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        destination = self._path(key)
        body = json.loads(canonical_json(dict(payload)))
        fingerprint = hashlib.sha256(canonical_json([path, body]).encode()).hexdigest()
        if destination.exists():
            record = json.loads(destination.read_text(encoding="utf-8"))
            if record["fingerprint"] != fingerprint:
                raise BackendError(
                    "IDEMPOTENCY_CONFLICT",
                    "This revision already has different content",
                    False,
                    409,
                )
            return record
        record = {
            "idempotency_key": key,
            "path": path,
            "payload": body,
            "fingerprint": fingerprint,
            "state": "pending",
            "attempts": 0,
        }
        self._write(destination, record)
        return record

    def records(self, *, pending_only: bool = False) -> list[dict[str, Any]]:
        """Read recoverable delivery state, without silently discarding failures."""
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.directory.glob("*.json"))
        ]
        return [record for record in records if not pending_only or record["state"] == "pending"]

    def get(self, key: str) -> dict[str, Any] | None:
        """Look up a prior immutable request without allocating a new one."""
        path = self._path(key)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def update(self, record: dict[str, Any], **changes: Any) -> dict[str, Any]:
        """Atomically record a delivery result while preserving the input."""
        with portalocker.Lock(str(self.directory / ".outbox.lock"), timeout=10):
            path = self._path(record["idempotency_key"])
            current = json.loads(path.read_text(encoding="utf-8"))
            if current["state"] == "acknowledged":
                return current
            updated = {**current, **changes}
            self._write(path, updated)
            return updated

    def enqueue_facts(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        """Allocate outcome revisions atomically across desktop processes."""
        with portalocker.Lock(str(self.directory / ".outbox.lock"), timeout=10):
            body = dict(facts)
            body.pop("version", None)
            records = [
                record
                for record in self.records()
                if record["path"] == "/api/knowledge/v1/learning/outcomes"
                and record["payload"].get("device_id") == body.get("device_id")
                and record["payload"].get("session_id") == body.get("session_id")
            ]
            for previous in reversed(records):
                comparison = dict(previous["payload"])
                comparison.pop("version", None)
                if comparison == body:
                    return previous
            body["version"] = 1 + max(
                (int(record["payload"]["version"]) for record in records), default=0
            )
            return self._enqueue(outcome_key(body), "/api/knowledge/v1/learning/outcomes", body)


class RemoteKnowledgeBackend:
    """Authenticated HTTP client with persistent, idempotent offline replay.

    Credentials are supplied by the desktop's secure configuration and are not
    written to the outbox. Insecure remote HTTP and redirects are rejected.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token_provider: Callable[[], str],
        outbox: DurableOutbox,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("A credential-free HTTP(S) service URL is required")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote knowledge service requires HTTPS")
        if parsed.query or parsed.fragment:
            raise ValueError("Service URL cannot contain query or fragment")
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.outbox = outbox
        self.client = client
        self._delivery_lock = asyncio.Lock()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        key: str | None = None,
        payload: Any = None,
        collection: bool = False,
    ) -> dict[str, Any]:
        token = self.token_provider()
        if not token:
            raise BackendError(
                "WAITING_AUTH", "Knowledge service authentication is required", True, 401
            )
        headers = {"Authorization": f"Bearer {token}"}
        if key:
            headers["Idempotency-Key"] = key
        try:
            if self.client is None:
                async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                    response = await client.request(
                        method, self.base_url + path, json=payload, headers=headers
                    )
            else:
                response = await self.client.request(
                    method,
                    self.base_url + path,
                    json=payload,
                    headers=headers,
                    follow_redirects=False,
                )
        except httpx.TransportError as error:
            raise BackendError(
                "NETWORK_UNAVAILABLE", "Knowledge service could not be reached", True
            ) from error
        if 300 <= response.status_code < 400:
            raise BackendError(
                "UNSAFE_REDIRECT",
                "Knowledge service redirects are not followed",
                False,
                response.status_code,
            )
        try:
            result = response.json()
        except ValueError as error:
            raise BackendError(
                "INVALID_RESPONSE",
                "Knowledge service returned invalid JSON",
                response.status_code >= 500 or response.is_success,
                response.status_code,
            ) from error
        if response.is_error:
            failure = result.get("error", {}) if isinstance(result, dict) else {}
            raise BackendError(
                str(failure.get("code", "REMOTE_ERROR")),
                str(failure.get("detail", "Knowledge service rejected this request")),
                response.status_code in {401, 429}
                or bool(failure.get("retryable", response.status_code >= 500)),
                response.status_code,
            )
        if not isinstance(result, dict) or not isinstance(
            result.get("data"), list if collection else dict
        ):
            raise BackendError(
                "INVALID_RESPONSE", "Knowledge service response is missing data", True
            )
        return result

    async def _deliver(self, record: dict[str, Any]) -> dict[str, Any]:
        if record["state"] == "acknowledged":
            return record["response"]
        if record["state"] == "rejected":
            failure = record["error"]
            raise BackendError(
                failure["code"], failure["detail"], False, failure.get("status_code")
            )
        attempts = record["attempts"] + 1
        try:
            response = await self._request(
                "POST", record["path"], key=record["idempotency_key"], payload=record["payload"]
            )
        except BackendError as error:
            self.outbox.update(
                record,
                attempts=attempts,
                state="pending" if error.retryable else "rejected",
                error={
                    "code": error.code,
                    "detail": error.detail,
                    "status_code": error.status_code,
                },
            )
            raise
        self.outbox.update(record, attempts=attempts, state="acknowledged", response=response)
        return response

    async def submit_learning_outcome(self, outcome: Mapping[str, Any]) -> dict[str, Any]:
        """Deliver a learning fact; retrying does not create a new revision."""
        async with self._delivery_lock:
            record = self.outbox.enqueue(
                outcome_key(outcome), "/api/knowledge/v1/learning/outcomes", outcome
            )
            return await self._deliver(record)

    async def submit_learning_facts(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        """Assign a persistent version when local facts materially change."""
        async with self._delivery_lock:
            return await self._deliver(self.outbox.enqueue_facts(facts))

    async def propose_wiki_change(
        self, proposal: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        """Route desktop drafts to the same change/review/CAS path as Pi."""
        if not idempotency_key:
            raise BackendError("INVALID_INPUT", "A change idempotency key is required")
        async with self._delivery_lock:
            return await self._deliver(
                self.outbox.enqueue(idempotency_key, "/api/knowledge/v1/changes", proposal)
            )

    async def read_entry(self, entry_id: str) -> dict[str, Any]:
        """Read one canonical entry, rejecting path-shaped identifiers."""
        if not entry_id or any(char in entry_id for char in "/\\?#") or entry_id in {".", ".."}:
            raise BackendError("INVALID_INPUT", "Invalid entry identifier")
        return await self._request("GET", "/api/knowledge/v1/entries/" + entry_id)

    async def read_relations(
        self, entry_id: str, *, kinds: list[str] | None = None,
        direction: str = "both", visibility: str = "all", limit: int = 100,
        traverse: bool = False, max_depth: int = 2, max_nodes: int = 100,
    ) -> dict[str, Any]:
        """Read a bounded authoritative projection without persisting or inferring edges."""
        if (
            not isinstance(entry_id, str) or not entry_id or len(entry_id) > 128
            or any(char in entry_id for char in "/\\?#%") or entry_id in {".", ".."}
        ):
            raise BackendError("INVALID_INPUT", "Invalid entry identifier", False, 422)
        if (
            kinds is not None and (not isinstance(kinds, list) or any(
                not isinstance(kind, str) or kind not in {"navigation", "confirmed", "candidate"}
                for kind in kinds
            ))
            or direction not in {"in", "out", "both"}
            or visibility not in {"all", "public"}
            or type(traverse) is not bool
        ):
            raise BackendError("INVALID_INPUT", "Invalid relation filters", False, 422)
        bounds = {"max_depth": (max_depth, 3), "max_nodes": (max_nodes, 200)} if traverse else {
            "limit": (limit, 1000)
        }
        if any(type(value) is not int or not 1 <= value <= maximum
               for value, maximum in bounds.values()):
            raise BackendError("INVALID_INPUT", "Invalid relation bounds", False, 422)
        query = [("direction", direction), ("visibility", visibility)]
        query.extend(("kind", kind) for kind in kinds or [])
        query.extend((name, str(value)) for name, (value, _) in bounds.items())
        path = "/api/knowledge/v1/relations/" + quote(entry_id, safe="")
        if traverse:
            path += "/traverse"
        return await self._request("GET", path + "?" + urlencode(query))

    async def flush(self) -> list[dict[str, Any]]:
        """Replay pending records; continue past independent terminal conflicts."""
        results: list[dict[str, Any]] = []
        async with self._delivery_lock:
            for record in self.outbox.records(pending_only=True):
                try:
                    results.append(await self._deliver(record))
                except BackendError as error:
                    results.append(
                        {
                            "idempotency_key": record["idempotency_key"],
                            "error": {"code": error.code, "retryable": error.retryable},
                        }
                    )
                    if (
                        error.code in {"WAITING_AUTH", "NETWORK_UNAVAILABLE"}
                        or error.status_code == 401
                    ):
                        break
        return results

    async def list_entries(self) -> list[dict[str, Any]]:
        """Read the authorized catalog, never silently substitute local articles."""
        entries: list[dict[str, Any]] = []
        while True:
            response = await self._request(
                "GET", f"/api/knowledge/v1/entries?limit=100&offset={len(entries)}", collection=True
            )
            batch = response["data"]
            if not all(isinstance(entry, dict) for entry in batch):
                raise BackendError("INVALID_RESPONSE", "Entry catalog is malformed", True)
            entries.extend(batch)
            if len(batch) < 100:
                return entries

    async def read_change(self, change_id: str) -> dict[str, Any]:
        """Read the common review/apply state; acceptance alone is not publication."""
        if not change_id or any(char in change_id for char in "/\\?#") or change_id in {".", ".."}:
            raise BackendError("INVALID_INPUT", "Invalid change identifier")
        return await self._request("GET", "/api/knowledge/v1/changes/" + change_id)

    async def read_task(self, task_id: str) -> dict[str, Any]:
        """Read the domain's safe task projection, never its worker lease or inputs."""
        if not task_id or any(char in task_id for char in "/\\?#") or task_id in {".", ".."}:
            raise BackendError("INVALID_INPUT", "Invalid task identifier")
        return await self._request("GET", "/api/knowledge/v1/tasks/" + task_id)

    async def read_entry_revision(self, entry_id: str, revision: int) -> dict[str, Any]:
        """Read an exact formal base for a diff; the current entry is not a fallback."""
        if not entry_id or any(char in entry_id for char in "/\\?#") or entry_id in {".", ".."}:
            raise BackendError("INVALID_INPUT", "Invalid entry identifier")
        if type(revision) is not int or revision < 1:
            raise BackendError("INVALID_INPUT", "Invalid entry revision")
        return await self._request(
            "GET", f"/api/knowledge/v1/entries/{entry_id}?revision={revision}"
        )

    @staticmethod
    def _radar_path(suggestion_id: str) -> str:
        if (
            not suggestion_id
            or any(char in suggestion_id for char in "/\\?#")
            or suggestion_id in {".", ".."}
        ):
            raise BackendError("INVALID_INPUT", "Invalid suggestion identifier")
        return "/api/knowledge/v1/radar/" + suggestion_id

    async def run_radar(self, *, limit: int = 3) -> dict[str, Any]:
        """Request evidence-bounded candidates, not automatic enrollment."""
        return await self._request(
            "POST",
            "/api/knowledge/v1/radar/runs",
            key="radar-run:" + str(uuid4()),
            payload={"limit": limit},
        )

    async def read_radar(self, suggestion_id: str) -> dict[str, Any]:
        """Read the accepted exercise and its authoritative completion status."""
        return await self._request("GET", self._radar_path(suggestion_id))

    async def decide_radar(self, suggestion_id: str, decision: str) -> dict[str, Any]:
        """Persist an explicit user decision for safe retry after a disconnect."""
        if decision not in {"accept", "reject", "snooze"}:
            raise BackendError("INVALID_INPUT", "Invalid radar decision")
        async with self._delivery_lock:
            return await self._deliver(
                self.outbox.enqueue(
                    "radar-decision:" + suggestion_id,
                    self._radar_path(suggestion_id) + "/decision",
                    {"decision": decision},
                )
            )


class LocalVaultBackend:
    """Compatibility storage adapter; this does not enable metered model calls."""

    def __init__(
        self,
        *,
        submit: Callable[..., dict[str, Any]],
        propose: Callable[..., dict[str, Any]],
        read: Callable[..., dict[str, Any]],
    ) -> None:
        self._submit, self._propose, self._read = submit, propose, read

    async def submit_learning_outcome(self, outcome: Mapping[str, Any]) -> dict[str, Any]:
        """Delegate to the existing local learning ledger."""
        return await asyncio.to_thread(self._submit, dict(outcome))

    async def propose_wiki_change(
        self, proposal: Mapping[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        """Delegate to the existing guarded local Wiki workflow."""
        return await asyncio.to_thread(
            self._propose, dict(proposal), idempotency_key=idempotency_key
        )

    async def read_entry(self, entry_id: str) -> dict[str, Any]:
        """Read local data without altering its storage format."""
        return await asyncio.to_thread(self._read, entry_id)
