"""Temporary intakes and immutable private retained source objects."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .auth import Actor, authenticated, classification_labels
from .contracts import canonical, fingerprint, new_id, now, validate_payload
from .errors import DomainError, invalid
from .publication import reject_publication_proofs

INTAKE_STATES = frozenset(
    {
        "received",
        "normalized",
        "fetching",
        "extracted",
        "partial",
        "preview_ready",
        "awaiting_keep",
        "retained",
        "kept",
        "knowledge_processing",
        "reviewed",
        "committed",
        "needs_auth",
        "rate_limited",
        "unsupported",
        "needs_input",
        "waiting_model",
        "discarded",
        "expired",
    }
)


def clean_url(value: str | None) -> str | None:
    """Remove common credential/account query parameters from durable source URLs."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid("URL must be text")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise invalid("Source URLs must be credential-free HTTP(S) URLs")
    private = {
        "token",
        "access_token",
        "auth",
        "authorization",
        "signature",
        "sig",
        "key",
        "api_key",
        "cookie",
        "session",
        "sessionid",
        "account",
        "user_id",
        "uid",
    }
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in private and not key.lower().startswith(("utm_", "x-amz-"))
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class SourceMethods:
    """Intake/source operations using the domain's shared transaction boundary."""

    def create_intake(
        self,
        payload: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Store a private temporary receipt; this never creates a retained source."""
        authenticated(actor, "knowledge:write")
        payload = validate_payload(payload)
        reject_publication_proofs(payload)
        if "url" in payload:
            payload["url"] = clean_url(payload["url"])
        if "raw_path" in payload or "object_path" in payload:
            raise invalid("Intake input cannot choose server filesystem paths")

        def create(connection: Any) -> dict[str, Any]:
            channel = payload.get("channel", "http")
            external = payload.get("external_message_id")
            semantic_key = canonical([channel, external]) if external is not None else None
            if semantic_key:
                existing = self._unique(
                    connection, actor, "channel_message", semantic_key, fingerprint(payload)
                )
                if existing:
                    return self.store.get(connection, "intake", existing, actor.owner_id)
            timestamp = now()
            intake = {
                "intake_id": new_id("in"),
                "owner_id": actor.owner_id,
                "state": "received",
                "payload": payload,
                "channel": channel,
                "external_message_id": external,
                "input_hash": fingerprint(payload),
                "url": payload.get("url"),
                "title": payload.get("title"),
                "metadata": payload.get("metadata", {}),
                "preview": None,
                "parse_status": None,
                "preview_revision": 0,
                "source_id": None,
                "task_id": None,
                "revision": 1,
                "trace_id": trace_id,
                "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self.store.put(connection, "intake", intake["intake_id"], intake)
            if semantic_key:
                self._save_unique(
                    connection,
                    actor,
                    "channel_message",
                    semantic_key,
                    fingerprint(payload),
                    intake["intake_id"],
                )
            self.store.event(connection, "intake.received.v1", intake, intake["intake_id"])
            return intake

        return self._mutate(actor, "create_intake", idempotency_key, payload, create)

    def read_intake(self, intake_id: str, *, actor: Actor) -> dict[str, Any]:
        """Read an owner-scoped temporary preview and its precise lifecycle state."""
        return self._read("intake", intake_id, actor)

    def list_intakes(
        self,
        *,
        actor: Actor,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List owned, readable intake snapshots without expiring or retaining anything."""
        authenticated(actor, "knowledge:read")
        if state is not None and (not isinstance(state, str) or state not in INTAKE_STATES):
            raise invalid("Unknown intake state")
        return self._list_owned_records(
            "intake", actor=actor, state=state, limit=limit, offset=offset
        )

    @staticmethod
    def _intake_lifecycle(record: dict[str, Any]) -> dict[str, Any]:
        """Minimal storage-maintenance receipt, never a preview or source-read capability."""
        return {
            key: record[key]
            for key in ("intake_id", "state", "source_id", "expires_at", "revision")
        } | {"data_classification": "public", "classification_labels": ["public"]}

    def read_intake_lifecycle(self, intake_id: str, *, actor: Actor) -> dict[str, Any]:
        """Let an owned maintenance worker clean expired/retained caches without RAW access."""
        authenticated(actor, "knowledge:read")
        if actor.kind == "agent":
            raise DomainError(
                "FORBIDDEN", "Lifecycle maintenance is not a model tool", status_code=403
            )
        if actor.kind == "service":
            actor.require("tasks:work")
        with self.store.reading() as connection:
            record = self.store.get(connection, "intake", intake_id, actor.owner_id)
            return self._intake_lifecycle(record)

    def _purge_intake(
        self, connection: Any, intake: dict[str, Any], state: str, trace_id: str | None = None
    ) -> dict[str, Any]:
        """Clear temporary payload and replay receipts in one domain transaction."""
        intake_id = intake["intake_id"]
        intake["preview"] = None
        intake["payload"] = {
            key: value
            for key, value in intake["payload"].items()
            if key in {"channel", "external_message_id", "url", "title"}
        }
        intake["metadata"] = {}
        if intake.get("task_id"):
            task = self.store.get(connection, "task", intake["task_id"], intake["owner_id"])
            if task["kind"] == "intake.preview" and task["state"] not in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                task.update(
                    state="cancelled",
                    result={"intake_id": intake_id, "state": state},
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    revision=task["revision"] + 1,
                    updated_at=now(),
                )
                self.store.put(connection, "task", task["task_id"], task)
                self.store.event(connection, "task.cancelled.v1", task, task["task_id"])
        intake.update(state=state, revision=intake["revision"] + 1, updated_at=now())
        self.store.put(connection, "intake", intake_id, intake)
        rows = connection.execute(
            "SELECT operation,key,response FROM kp_receipts WHERE owner_id=?",
            (intake["owner_id"],),
        ).fetchall()
        for row in rows:
            response = json.loads(row["response"])
            if response.get("intake_id") == intake_id and not response.get("source_id"):
                connection.execute(
                    "UPDATE kp_receipts SET response=? WHERE owner_id=? AND operation=? AND key=?",
                    (canonical(intake), intake["owner_id"], row["operation"], row["key"]),
                )
        self.store.event(
            connection,
            f"intake.{state}.v1",
            intake,
            intake_id,
            {"preview_revision": intake["preview_revision"]},
            trace_id,
        )
        return intake

    def update_intake(
        self,
        intake_id: str,
        patch: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a preview using revision CAS; only retain_source may cross keep."""
        authenticated(actor, "knowledge:write")
        patch = validate_payload(patch)
        reject_publication_proofs(patch)
        allowed = {
            "state",
            "preview",
            "parse_status",
            "metadata",
            "expires_at",
            "task_id",
            "preview_revision",
            "expected_preview_revision",
            "expected_revision",
            "title",
            "url",
        }
        if set(patch) - allowed:
            raise invalid("Unsupported intake patch fields")
        if actor.kind == "agent" and ("expires_at" in patch or patch.get("state") == "expired"):
            raise DomainError(
                "FORBIDDEN", "Models cannot change retention deadlines", status_code=403
            )

        def update(connection: Any) -> dict[str, Any]:
            intake = self.store.get(connection, "intake", intake_id, actor.owner_id)
            self._check_revision(intake, patch)
            if (
                "expected_preview_revision" in patch
                and patch["expected_preview_revision"] != intake["preview_revision"]
            ):
                raise DomainError("BASE_STALE", "Preview has changed", status_code=409)
            if intake["state"] in {"discarded", "expired"} or intake["source_id"]:
                raise DomainError("INVALID_STATE", "Intake is no longer mutable", status_code=409)
            state = patch.get("state", intake["state"])
            if state not in INTAKE_STATES or state in {
                "retained",
                "kept",
                "knowledge_processing",
                "reviewed",
                "committed",
            }:
                raise invalid("This intake transition requires the corresponding domain operation")
            if state == "discarded" and actor.kind != "user":
                raise DomainError(
                    "FORBIDDEN", "Only the user may discard a preview", status_code=403
                )
            if state == "expired" and intake["expires_at"] > now():
                raise DomainError("INVALID_STATE", "Intake has not expired", status_code=409)
            if "preview" in patch:
                expected = patch.get("expected_preview_revision")
                if intake["preview_revision"] and expected is None:
                    raise invalid("Updating an existing preview requires expected_preview_revision")
                intake["preview_revision"] += 1
                if (
                    "preview_revision" in patch
                    and patch["preview_revision"] != intake["preview_revision"]
                ):
                    raise DomainError(
                        "BASE_STALE", "Invalid next preview revision", status_code=409
                    )
            if "task_id" in patch and patch["task_id"] is not None:
                self.store.get(connection, "task", patch["task_id"], actor.owner_id)
            if "expires_at" in patch:
                try:
                    parsed = datetime.fromisoformat(patch["expires_at"])
                    if parsed.tzinfo is None:
                        raise ValueError("naive time")
                    patch["expires_at"] = parsed.astimezone(UTC).isoformat()
                except (ValueError, TypeError) as exc:
                    raise invalid("expires_at must include timezone") from exc
            for key in allowed - {
                "preview_revision",
                "expected_preview_revision",
                "expected_revision",
            }:
                if key in patch:
                    intake[key] = clean_url(patch[key]) if key == "url" else patch[key]
            if state in {"discarded", "expired"}:
                return self._purge_intake(connection, intake, state, trace_id)
            intake.update(state=state, revision=intake["revision"] + 1, updated_at=now())
            self.store.put(connection, "intake", intake_id, intake)
            self.store.event(
                connection,
                f"intake.{state}.v1",
                intake,
                intake_id,
                {"preview_revision": intake["preview_revision"]},
                trace_id,
            )
            return intake

        return self._mutate(actor, f"update_intake:{intake_id}", idempotency_key, patch, update)

    def _source_bytes(self, descriptor: dict[str, Any]) -> bytes:
        """Read only a regular file within the explicitly configured temporary cache."""
        if "raw_text" in descriptor:
            if not isinstance(descriptor["raw_text"], str):
                raise invalid("raw_text must be a string")
            data = descriptor["raw_text"].encode("utf-8")
        elif descriptor.get("raw_path"):
            path = Path(descriptor["raw_path"])
            root = self.cache_path.resolve()
            if not path.is_absolute():
                path = root / path
            if path.is_symlink() or not path.is_file():
                raise invalid("RAW path must be a regular cache file")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise DomainError(
                    "FORBIDDEN", "RAW path is outside the intake cache", status_code=403
                )
            relative = path.relative_to(root)
            if any(part in {"..", "."} for part in relative.parts):
                raise invalid("RAW path must not contain traversal")
            if os.name != "nt":
                # Walk directory descriptors so intermediate symlink swaps cannot escape.
                directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for part in relative.parts[:-1]:
                        child_fd = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                        )
                        os.close(directory_fd)
                        directory_fd = child_fd
                    descriptor_fd = os.open(
                        relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
                    )
                    with os.fdopen(descriptor_fd, "rb") as handle:
                        data = handle.read(self.max_object_bytes + 1)
                finally:
                    os.close(directory_fd)
            else:
                current = root
                for part in relative.parts:
                    current = current / part
                    if current.is_symlink():
                        raise DomainError(
                            "FORBIDDEN",
                            "Symlinks are not accepted as source objects",
                            status_code=403,
                        )
                with path.open("rb") as handle:
                    data = handle.read(self.max_object_bytes + 1)
        else:
            raise invalid("A retained source requires raw_text or a controlled-cache raw_path")
        if not data or len(data) > self.max_object_bytes:
            raise invalid("Source must be nonempty and within the object size limit")
        return data

    def _save_object(self, data: bytes) -> tuple[str, str]:
        """Durably create a content-addressed object before recording any reference."""
        digest = hashlib.sha256(data).hexdigest()
        target = self.object_path / "sha256" / digest[:2] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise DomainError(
                    "OBJECT_CORRUPT", "Existing source object failed verification", status_code=409
                )
            return digest, str(target.relative_to(self.object_path))
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".retain-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).replace(target)
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return digest, str(target.relative_to(self.object_path))

    def retain_source(
        self,
        intake_id: str,
        descriptor: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
        external_document_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Keep exactly the preview the user selected, and atomically enqueue processing."""
        authenticated(actor, "knowledge:write")
        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN", "Only an authenticated user may keep a source", status_code=403
            )
        descriptor = validate_payload(descriptor)
        reject_publication_proofs(descriptor)
        if external_document_manifest is not None:
            external_document_manifest = validate_payload(external_document_manifest)

        def retain(connection: Any) -> dict[str, Any]:
            intake = self.store.get(connection, "intake", intake_id, actor.owner_id)
            if intake["source_id"]:
                return self.store.get(connection, "source", intake["source_id"], actor.owner_id)
            if intake["state"] not in {"preview_ready", "awaiting_keep", "partial", "extracted"}:
                raise DomainError(
                    "INVALID_STATE", "A current preview is required before keep", status_code=409
                )
            if intake["expires_at"] <= now():
                raise DomainError("INVALID_STATE", "Preview has expired", status_code=409)
            if (
                not intake["preview_revision"]
                or descriptor.get("preview_revision") != intake["preview_revision"]
            ):
                raise DomainError(
                    "BASE_STALE", "Keep must select the current preview revision", status_code=409
                )
            data = self._source_bytes(descriptor)
            extraction = descriptor.get("metadata", {}).get("extraction", {})
            semantic_identity = None
            if descriptor.get("content_identity") and extraction:
                semantic = {
                    "platform": extraction.get("platform"),
                    "url": extraction.get("source_url"),
                    "body": extraction.get("body_text"),
                    "description": extraction.get("description"),
                }
                semantic_identity = fingerprint(semantic)
            elif descriptor.get("content_identity"):
                semantic_identity = fingerprint(
                    [descriptor["content_identity"], hashlib.sha256(data).hexdigest()]
                )
            if semantic_identity:
                # A protected envelope cannot reuse a clean-source generation task,
                # and a later personal capture cannot wash an earlier protected RAW.
                semantic_identity = fingerprint(
                    [
                        semantic_identity,
                        sorted(classification_labels(intake)),
                        sorted(classification_labels(descriptor, default="public")),
                        bool(external_document_manifest),
                        descriptor.get("publication_intent"),
                        descriptor.get("metadata", {}).get("publication_intent"),
                        intake.get("publication_intent"),
                        intake.get("metadata", {}).get("publication_intent"),
                        intake.get("payload", {}).get("publication_intent"),
                        intake.get("payload", {}).get("metadata", {}).get("publication_intent"),
                    ]
                )
                existing = self._unique(
                    connection, actor, "source_content", semantic_identity, semantic_identity
                )
                if existing:
                    source = self.store.get(connection, "source", existing, actor.owner_id)
                    intake.update(
                        state="retained",
                        source_id=source["source_id"],
                        task_id=source["task_id"],
                        revision=intake["revision"] + 1,
                        updated_at=now(),
                    )
                    self.store.put(connection, "intake", intake_id, intake)
                    self.store.event(
                        connection,
                        "intake.retained.v1",
                        intake,
                        intake_id,
                        {"source_id": source["source_id"], "reused": True},
                        trace_id,
                    )
                    return source
            digest, object_path = self._save_object(data)
            metadata = {
                key: value
                for key, value in descriptor.items()
                if key not in {"raw_text", "raw_path"}
            }
            for key in ("url", "canonical_url"):
                if key in metadata:
                    metadata[key] = clean_url(metadata[key])
            timestamp = now()
            source = {
                "source_id": new_id("src"),
                "owner_id": actor.owner_id,
                "intake_id": intake_id,
                "schema_version": 1,
                "revision": 1,
                "content_hash": digest,
                "content_sha256": digest,
                "object_path": object_path,
                "descriptor": metadata,
                "visibility": "private",
                "rights": metadata.get("rights", "unknown"),
                "created_at": timestamp,
                "trace_id": trace_id,
            }
            task = {
                "task_id": new_id("tsk"),
                "owner_id": actor.owner_id,
                "kind": "knowledge.generate",
                "state": "queued",
                "payload": {
                    "evidence_refs": [{"source_id": source["source_id"]}],
                    "intake_id": intake_id,
                },
                "result": None,
                "error": None,
                "attempt": 0,
                "max_attempts": 5,
                "lease_owner": None,
                "lease_token": None,
                "lease_until": None,
                "next_run_at": None,
                "revision": 1,
                "trace_id": trace_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            source["task_id"] = task["task_id"]
            if external_document_manifest is not None:
                document_id = self._register_external_document(
                    connection, source, intake, external_document_manifest, data
                )
                if document_id:
                    source["external_document_id"] = document_id
            self.store.put(connection, "source", source["source_id"], source)
            if semantic_identity:
                self._save_unique(
                    connection,
                    actor,
                    "source_content",
                    semantic_identity,
                    semantic_identity,
                    source["source_id"],
                )
            self.store.put(connection, "task", task["task_id"], task)
            intake.update(
                state="retained",
                source_id=source["source_id"],
                task_id=task["task_id"],
                revision=intake["revision"] + 1,
                updated_at=timestamp,
            )
            self.store.put(connection, "intake", intake_id, intake)
            self.store.event(
                connection,
                "source.retained.v1",
                source,
                source["source_id"],
                {"intake_id": intake_id},
                trace_id,
            )
            self.store.event(connection, "task.queued.v1", task, task["task_id"])
            return source

        binding = (
            descriptor
            if external_document_manifest is None
            else {
                "descriptor": descriptor,
                "external_document_manifest": external_document_manifest,
            }
        )
        return self._mutate(actor, f"retain_source:{intake_id}", idempotency_key, binding, retain)

    def get_source(self, source_id: str, *, actor: Actor) -> dict[str, Any]:
        """Read private source metadata without exposing its absolute filesystem path."""
        return self._read("source", source_id, actor)

    def read_evidence(self, evidence_ref: dict[str, Any], *, actor: Actor) -> dict[str, Any]:
        """Read immutable evidence, enforcing owner and hash/version bindings."""
        authenticated(actor, "knowledge:read")
        with self.store.reading() as connection:
            return self._evidence(connection, evidence_ref, actor)

    def _evidence(self, connection: Any, ref: dict[str, Any], actor: Actor) -> dict[str, Any]:
        if ref.get("external_document_id"):
            result = self._external_document(connection, ref["external_document_id"], actor)
            digest = result["content_hash"]
        elif ref.get("source_id"):
            record = self._protect_read(
                connection,
                self.store.get(connection, "source", ref["source_id"], actor.owner_id),
                actor,
            )
            if ref.get("source_revision", record["revision"]) != record["revision"]:
                raise DomainError("BASE_STALE", "Source revision does not match", status_code=409)
            data = (self.object_path / record["object_path"]).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != record["content_hash"]:
                raise DomainError(
                    "OBJECT_CORRUPT",
                    "Evidence object failed integrity verification",
                    status_code=409,
                )
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            extraction = record["descriptor"].get("metadata", {}).get("extraction", {})
            if extraction:
                # A retained JSON bundle may contain raw media/base64 and signed URLs.
                # Models receive the extracted text, never the entire untrusted bundle.
                text = extraction.get("body_text") or extraction.get("description") or None
            result = {**record, "text": text}
        elif ref.get("learning_outcome_id"):
            result = self.store.get(
                connection, "learning", ref["learning_outcome_id"], actor.owner_id
            )
            result = self._protect_read(connection, result, actor)
            digest = result["content_hash"]
            result = {**result, "text": result["payload"].get("user_text", "")}
        else:
            raise invalid("Evidence must reference a retained source or learning outcome")
        if ref.get("content_hash", digest) != digest:
            raise DomainError("BASE_STALE", "Evidence fingerprint does not match", status_code=409)
        locator = ref.get("locator") or {}
        if not isinstance(locator, dict | str):
            raise invalid("Evidence locator must be an object or string")
        result["locator"] = locator
        return result

    def get_channel_state(self, channel: str, *, actor: Actor) -> dict[str, Any]:
        """Read an owned durable ingress cursor; missing channels start empty."""
        authenticated(actor, "knowledge:read")
        try:
            return self._read("channel", self._channel_id(channel, actor), actor)["state"]
        except DomainError as exc:
            if exc.code != "NOT_FOUND":
                raise
            return {}

    def set_channel_state(
        self,
        channel: str,
        state: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a channel cursor only after its receipt has durably succeeded."""
        authenticated(actor, "knowledge:write")
        state = validate_payload(state)
        record_id = self._channel_id(channel, actor)

        def update(connection: Any) -> dict[str, Any]:
            record = {
                "owner_id": actor.owner_id,
                "channel": channel,
                "state": state,
                "revision": 1,
                "trace_id": trace_id,
                "updated_at": now(),
            }
            try:
                previous = self.store.get(connection, "channel", record_id, actor.owner_id)
                record["revision"] = previous["revision"] + 1
                for key in ("offset", "update_id"):
                    if (
                        key in state
                        and key in previous["state"]
                        and state[key] < previous["state"][key]
                    ):
                        raise DomainError(
                            "BASE_STALE", "Channel cursor cannot move backwards", status_code=409
                        )
            except DomainError as exc:
                if exc.code != "NOT_FOUND":
                    raise
            self.store.put(connection, "channel", record_id, record)
            return state

        return self._mutate(actor, f"set_channel_state:{channel}", idempotency_key, state, update)

    @staticmethod
    def _channel_id(channel: str, actor: Actor) -> str:
        return fingerprint([actor.owner_id, channel])

    def list_expired_intakes(self, *, actor: Actor, limit: int = 100) -> list[dict[str, Any]]:
        """Enumerate owned expired, unretained receipts even if no cache was ever created."""
        authenticated(actor, "knowledge:read")
        self._validate_page(limit, 0)
        with self.store.reading() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind='intake' AND owner_id=? ORDER BY rowid",
                (actor.owner_id,),
            ).fetchall()
            records = [json.loads(row["data"]) for row in rows]
            return self._visible_records(
                connection,
                [
                    record
                    for record in records
                    if not record.get("source_id")
                    and record["state"] not in {"discarded", "expired"}
                    and record["expires_at"] <= now()
                ],
                actor,
            )[:limit]

    def expire_intakes(self, *, actor: Actor, limit: int = 100) -> list[dict[str, Any]]:
        """Expire owned temporary receipts without exposing restricted RAW to maintenance."""
        authenticated(actor, "knowledge:write")
        if actor.kind == "agent":
            raise DomainError("FORBIDDEN", "Models cannot expire intakes", status_code=403)
        if actor.kind == "service":
            actor.require("tasks:work")
        self._validate_page(limit, 0)
        results = []
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind='intake' AND owner_id=? ORDER BY rowid",
                (actor.owner_id,),
            ).fetchall()
            for row in rows:
                if len(results) >= limit:
                    break
                record = json.loads(row["data"])
                if (
                    record.get("source_id")
                    or record["state"] in {"expired", "discarded"}
                    or record["expires_at"] > now()
                ):
                    continue
                self._purge_intake(connection, record, "expired")
                results.append(record if actor.kind == "user" else self._intake_lifecycle(record))
        return results
