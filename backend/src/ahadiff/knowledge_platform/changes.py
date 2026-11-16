"""Evidence-bound proposals, conservative hard gates and recoverable application."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .auth import Actor, authenticated, classification_labels
from .contracts import (
    MAJOR_RISKS,
    canonical,
    fingerprint,
    identifier,
    new_id,
    now,
    required_text,
    text_hash,
    validate_payload,
)
from .errors import DomainError, invalid
from .publication import reject_publication_proofs


def content_lines(body: str) -> list[str]:
    """Normalize presentation-only whitespace without erasing words or their order."""
    return [re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip()]


def classify_risks(
    payload: dict[str, Any], previous: dict[str, Any] | None
) -> tuple[list[str], str]:
    """Union declared risks with conservative program-inferred major boundaries.

    An arbitrary semantic rewrite cannot be proven non-core by string similarity.
    Removing/changing an existing substantive line therefore requires user review.
    Pure additions may be automatic; pure rearrangement receives independent review.
    """
    flags = set(payload.get("risk_flags", []))
    if not all(isinstance(flag, str) for flag in flags):
        raise invalid("risk_flags must contain strings")
    aliases = {
        "core_conclusion_flip": "core_conclusion",
        "privacy_change": "privacy_exposure",
        "important_delete": "important_merge_delete",
        "merge": "important_merge_delete",
        "personal_stance": "personal_position",
    }
    flags.update(aliases[flag] for flag in list(flags) if flag in aliases)
    operation = payload["operation"]
    if operation in {"delete", "merge"}:
        flags.add("important_merge_delete")
    body = payload["body"]
    if re.search(
        r"我的理解|我的观点|我的立场|我认为|I believe|my (?:view|understanding|position)",
        body,
        re.I,
    ) and (previous is None or body != previous.get("body")):
        flags.add("personal_position")
    if previous:
        if previous.get("deleted"):
            flags.add("important_merge_delete")
        if previous.get("visibility") != "public" and payload["visibility"] == "public":
            flags.add("privacy_exposure")
        if (
            previous.get("publication_intent", previous.get("visibility")) != "public"
            and payload.get("publication_intent") == "public"
        ):
            flags.add("privacy_exposure")
        old_lines = content_lines(previous["body"])
        new_lines = content_lines(body)
        if Counter(old_lines) - Counter(new_lines):
            flags.add("core_conclusion")
        elif old_lines != new_lines and not body.startswith(previous["body"]):
            flags.add("requires_ai_review")
        additions = [line for line in new_lines if line not in old_lines]
        if any(
            re.search(
                r"核心结论|更正|推翻|结论.*(?:错误|不再|不成立)|contradict|correction|conclusion.*wrong",
                line,
                re.I,
            )
            for line in additions
        ):
            flags.add("core_conclusion")
        if payload["title"] != previous["title"]:
            flags.add("requires_ai_review")
    level = (
        "user" if flags & MAJOR_RISKS else "ai" if "requires_ai_review" in flags else "automatic"
    )
    return sorted(flags), level


class ChangeMethods:
    """Proposals and the only final-write boundary; adapters never write Git directly."""

    def propose_change(
        self,
        payload: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
        workflow_task_id: str | None = None,
        derivative_input_manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate owned evidence and create an immutable, revision-bound proposal."""
        authenticated(actor, "knowledge:write")
        payload = validate_payload(payload)
        reject_publication_proofs(payload)
        if "workflow_task_id" in payload:
            raise DomainError(
                "FORBIDDEN",
                "Workflow association is not a request-body capability",
                status_code=403,
            )
        supplied = json.loads(canonical(payload))
        if workflow_task_id is not None:
            supplied = {"payload": supplied, "workflow_task_id": workflow_task_id}
        if derivative_input_manifest is not None:
            derivative_input_manifest = validate_payload(derivative_input_manifest)
            supplied = {"request": supplied, "derivative_input_manifest": derivative_input_manifest}
        required_text(payload, "title", limit=500)
        if not isinstance(payload.get("body"), str) or len(payload["body"]) > 1_000_000:
            raise invalid("body must be bounded Markdown text")
        if (
            not isinstance(payload.get("base_revision", 0), int)
            or payload.get("base_revision", 0) < 0
        ):
            raise invalid("base_revision must be a nonnegative integer")
        if not isinstance(payload.get("risk_flags", []), list):
            raise invalid("risk_flags must be an array")
        if not isinstance(payload.get("metadata", {}), dict):
            raise invalid("metadata must be an object")
        refs = payload.get("evidence_refs", [])
        if not isinstance(refs, list) or not refs:
            raise DomainError(
                "EVIDENCE_REQUIRED",
                "A proposal requires retained source or learning evidence",
                status_code=422,
            )
        # External references without trusted input/review proofs confer no public
        # eligibility. They remain usable for private corrections or withdrawal.

        def propose(connection: Any) -> dict[str, Any]:
            if workflow_task_id is not None:
                workflow = self.store.get(connection, "task", workflow_task_id, actor.owner_id)
                if workflow["kind"] != "knowledge.generate" or workflow["state"] != "running":
                    raise DomainError(
                        "INVALID_STATE",
                        "Proposal association requires a running generation task",
                        status_code=409,
                    )
            entry_id = identifier(payload.get("entry_id") or new_id("ent"), "entry_id")
            collision = connection.execute(
                "SELECT owner_id FROM kp_records WHERE kind='entry' AND id=?", (entry_id,)
            ).fetchone()
            if collision is not None and collision["owner_id"] != actor.owner_id:
                raise DomainError(
                    "NOT_FOUND", "The requested record was not found", status_code=404
                )
            previous = None
            try:
                previous = self._entry(connection, entry_id, actor.owner_id)
            except DomainError as exc:
                if exc.code != "NOT_FOUND":
                    raise
            base = payload.get("base_revision", 0)
            if base != (previous["revision"] if previous else 0):
                raise DomainError("BASE_STALE", "Entry revision has changed", status_code=409)
            base_git_revision = self.writer.head()
            path = self.writer.path_for(actor.owner_id, entry_id)
            catalog_hash = self.writer.file_hash(previous["commit_sha"], path) if previous else None
            if self.writer.file_hash(base_git_revision, path) != catalog_hash:
                raise DomainError(
                    "BASE_STALE",
                    "Formal file changed outside the catalog; both versions are preserved",
                    status_code=409,
                )
            payload["entry_id"] = entry_id
            payload["base_revision"] = base
            payload["operation"] = payload.get("operation", "update" if previous else "create")
            if payload["operation"] not in {"create", "update", "delete"}:
                raise invalid("operation must be create, update or delete")
            if (payload["operation"] == "create") == bool(previous):
                raise invalid("Operation does not match entry existence")
            payload["visibility"] = payload.get(
                "visibility", previous["visibility"] if previous else "public"
            )
            if payload["visibility"] not in {"private", "public", "legacy_unclassified"}:
                raise invalid("Unsupported visibility")
            payload["entry_type"] = payload.get(
                "entry_type", previous["entry_type"] if previous else "concept"
            )
            payload["metadata"] = {
                **(previous.get("metadata", {}) if previous else {}),
                **payload.get("metadata", {}),
            }
            payload["publication_intent"] = payload.get(
                "publication_intent",
                payload["metadata"].get("publication_intent", payload["visibility"]),
            )
            if payload["publication_intent"] not in {"public", "private", "legacy_unclassified"}:
                raise invalid("Unsupported publication intent")
            evidence = []
            normalized_refs = []
            has_personal_statement = False
            evidence_texts = []
            eligible = (
                payload["visibility"] == "public" and payload["publication_intent"] == "public"
            )
            for ref in refs:
                if not isinstance(ref, dict):
                    raise invalid("Evidence references must be objects")
                item = self._evidence(connection, ref, actor)
                if item.get("text"):
                    evidence_texts.append(item["text"])
                if ref.get("quote") and (not item.get("text") or ref["quote"] not in item["text"]):
                    raise DomainError(
                        "EVIDENCE_REQUIRED",
                        "Quoted evidence is not present in the source",
                        status_code=422,
                    )
                normalized_refs.append({**ref, "content_hash": item["content_hash"]})
                evidence.append(
                    {
                        "id": ref.get("source_id")
                        or ref.get("learning_outcome_id")
                        or ref.get("external_document_id"),
                        "content_hash": item["content_hash"],
                    }
                )
                if ref.get("learning_outcome_id") and item.get("text", "").strip():
                    has_personal_statement = True
                if ref.get("source_id"):
                    descriptor = item["descriptor"]
                    source_metadata = descriptor.get("metadata", {})
                    source_classification = descriptor.get(
                        "input_classification",
                        descriptor.get(
                            "classification",
                            source_metadata.get(
                                "evidence_classification",
                                source_metadata.get("input_classification", "public"),
                            ),
                        ),
                    )
                    if source_classification in {
                        "personal",
                        "third_party_private",
                        "employer",
                        "secret",
                    }:
                        eligible = False
                    if classification_labels(item, default="personal") - {"public"}:
                        eligible = False
                    complete = descriptor.get("completeness", "complete")
                    if complete in {"metadata_only", "unsupported"} or item.get("text") is None:
                        raise DomainError(
                            "SOURCE_INCOMPLETE",
                            "Evidence has no usable source text",
                            status_code=422,
                        )
                    eligible = eligible and item.get("rights") in {
                        "public",
                        "public_domain",
                        "licensed",
                        "summary_allowed",
                    }
                else:
                    eligible = False  # Raw learning statements never become public by default.
            payload["evidence_refs"] = normalized_refs
            if derivative_input_manifest is not None:
                payload["derivative_input_manifest"] = derivative_input_manifest
                payload["risk_flags"] = sorted(
                    set(payload.get("risk_flags", [])) | {"requires_ai_review"}
                )
                self._derivative_documents(connection, payload, actor)
            # A source ID alone does not prove an arbitrary paraphrase is supported.
            # Only source-verbatim additions qualify for programmatic L1 approval.
            previous_lines = set(content_lines(previous["body"])) if previous else set()
            new_claims = [
                line
                for line in content_lines(payload["body"])
                if line not in previous_lines and not line.startswith("#")
            ]
            if any(not any(line in text for text in evidence_texts) for line in new_claims):
                payload["risk_flags"] = sorted(
                    set(payload.get("risk_flags", [])) | {"requires_ai_review"}
                )
            flags, level = classify_risks(payload, previous)
            if "personal_position" in flags and not has_personal_statement:
                raise DomainError(
                    "PERSONAL_EVIDENCE_REQUIRED",
                    "Personal understanding requires authenticated user words",
                    status_code=422,
                )
            timestamp = now()
            change = {
                **payload,
                "change_id": new_id("chg"),
                "owner_id": actor.owner_id,
                "proposer_id": actor.actor_id,
                "proposer_kind": actor.kind,
                "base_git_revision": base_git_revision,
                "risk_flags": flags,
                "review_level": level,
                "policy_version": self.policy_version,
                "evidence_hash": fingerprint(evidence),
                "trace_id": trace_id,
                "publication_intent": payload["publication_intent"],
                "publication_eligible": eligible,
                "revision": 1,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            patch = {
                key: change[key]
                for key in (
                    "entry_id",
                    "base_revision",
                    "operation",
                    "title",
                    "body",
                    "entry_type",
                    "visibility",
                    "evidence_refs",
                    "metadata",
                    "publication_intent",
                    "publication_eligible",
                )
            }
            change["patch_hash"] = fingerprint(patch)
            change["state"] = {
                "user": "needs_user",
                "ai": "needs_ai_review",
                "automatic": "approved",
            }[level]
            change["automatic_approval"] = (
                {
                    "policy_version": self.policy_version,
                    "patch_hash": change["patch_hash"],
                    "evidence_hash": change["evidence_hash"],
                }
                if level == "automatic"
                else None
            )
            if (
                previous
                and not previous.get("deleted")
                and change["operation"] == "update"
                and content_lines(change["body"]) == content_lines(previous["body"])
                and all(
                    change[key] == previous.get(key)
                    for key in (
                        "title",
                        "visibility",
                        "evidence_refs",
                        "metadata",
                        "entry_type",
                        "publication_intent",
                        "publication_eligible",
                    )
                )
            ):
                change["state"] = "no_change"
            self.store.put(connection, "change", change["change_id"], change)
            if change["state"] != "no_change":
                if workflow_task_id is not None:
                    change["task_id"] = workflow_task_id
                else:
                    task = {
                        "task_id": new_id("tsk"),
                        "owner_id": actor.owner_id,
                        "kind": "knowledge.review",
                        "state": "queued",
                        "payload": {"change_id": change["change_id"]},
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
                    self.store.put(connection, "task", task["task_id"], task)
                    self.store.event(connection, "task.queued.v1", task, task["task_id"])
                    change["task_id"] = task["task_id"]
                self.store.put(connection, "change", change["change_id"], change)
            self.store.event(
                connection,
                "change.proposed.v1",
                change,
                change["change_id"],
                {"state": change["state"], "entry_id": entry_id, "risk_flags": flags},
            )
            return change

        return self._mutate(actor, "propose_change", idempotency_key, supplied, propose)

    def get_change(self, change_id: str, *, actor: Actor) -> dict[str, Any]:
        """Read a proposal and its exact review bindings."""
        return self._read("change", change_id, actor)

    def _validate_change(self, connection: Any, change: dict[str, Any], actor: Actor) -> None:
        self._protect_read(connection, change, actor)
        if change["policy_version"] != self.policy_version:
            raise DomainError(
                "REVIEW_REQUIRED",
                "Review policy changed; a new proposal is required",
                status_code=409,
            )
        patch = {
            key: change[key]
            for key in (
                "entry_id",
                "base_revision",
                "operation",
                "title",
                "body",
                "entry_type",
                "visibility",
                "evidence_refs",
                "metadata",
                "publication_intent",
                "publication_eligible",
            )
        }
        if fingerprint(patch) != change["patch_hash"]:
            raise DomainError(
                "REVIEW_REQUIRED", "Proposed patch changed after review", status_code=409
            )
        previous = None
        try:
            previous = self._entry(connection, change["entry_id"], actor.owner_id)
        except DomainError as exc:
            if exc.code != "NOT_FOUND":
                raise
        if (previous["revision"] if previous else 0) != change["base_revision"]:
            raise DomainError("BASE_STALE", "Entry changed after proposal", status_code=409)
        if self.writer.head() != change["base_git_revision"]:
            raise DomainError("BASE_STALE", "Git base changed after proposal", status_code=409)
        path = self.writer.path_for(actor.owner_id, change["entry_id"])
        catalog_hash = self.writer.file_hash(previous["commit_sha"], path) if previous else None
        if self.writer.file_hash(change["base_git_revision"], path) != catalog_hash:
            raise DomainError("BASE_STALE", "Formal file is ahead of the catalog", status_code=409)
        evidence = []
        for ref in change["evidence_refs"]:
            item = self._evidence(connection, ref, actor)
            evidence.append(
                {
                    "id": ref.get("source_id")
                    or ref.get("learning_outcome_id")
                    or ref.get("external_document_id"),
                    "content_hash": item["content_hash"],
                }
            )
        if fingerprint(evidence) != change["evidence_hash"]:
            raise DomainError("REVIEW_REQUIRED", "Evidence changed after review", status_code=409)
        flags, level = classify_risks(change, previous)
        if set(flags) - set(change["risk_flags"]):
            raise DomainError(
                "REVIEW_REQUIRED", "Additional program risk requires fresh review", status_code=409
            )
        if change["state"] != "approved":
            raise DomainError("REVIEW_REQUIRED", "This proposal is not approved", status_code=409)
        if level == "automatic":
            approval = change.get("automatic_approval")
            if approval and approval == {
                "policy_version": self.policy_version,
                "patch_hash": change["patch_hash"],
                "evidence_hash": change["evidence_hash"],
            }:
                return
        rows = connection.execute(
            "SELECT data FROM kp_records WHERE kind='review' AND owner_id=?", (actor.owner_id,)
        ).fetchall()
        for row in rows:
            review = json.loads(row["data"])
            if (
                review["change_id"] == change["change_id"]
                and review["decision"] == "approve"
                and review["patch_hash"] == change["patch_hash"]
                and review["base_revision"] == change["base_revision"]
                and review["base_git_revision"] == change["base_git_revision"]
                and review["policy_version"] == self.policy_version
                and review["evidence_hash"] == change["evidence_hash"]
                and (level != "user" or review["actor_kind"] == "user")
            ):
                return
        raise DomainError(
            "REVIEW_REQUIRED", "No current independent approval exists", status_code=409
        )

    def _finalize_journal(self, connection: Any, journal: dict[str, Any]) -> dict[str, Any]:
        change = self.store.get(connection, "change", journal["change_id"], journal["owner_id"])
        if change["state"] == "committed":
            return self._entry(
                connection, change["entry_id"], journal["owner_id"], change["committed_revision"]
            )
        entry = {**journal["entry"], "commit_sha": journal["commit_sha"], "path": journal["path"]}
        catalog = {key: value for key, value in entry.items() if key != "body"}
        self.store.put(connection, "entry", entry["entry_id"], catalog)
        connection.execute(
            "INSERT OR IGNORE INTO kp_entry_revisions"
            "(entry_id,revision,owner_id,commit_sha,path,content_hash,metadata) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                entry["entry_id"],
                entry["revision"],
                entry["owner_id"],
                entry["commit_sha"],
                entry["path"],
                entry["content_hash"],
                canonical(catalog),
            ),
        )
        change.update(
            state="committed",
            commit_sha=entry["commit_sha"],
            committed_revision=entry["revision"],
            revision=change["revision"] + 1,
            updated_at=now(),
        )
        self.store.put(connection, "change", change["change_id"], change)
        connection.execute(
            "UPDATE kp_writer_journal SET state='committed' WHERE change_id=?",
            (change["change_id"],),
        )
        event_type = (
            "entry_created"
            if entry["revision"] == 1
            else "entry_deleted"
            if entry["deleted"]
            else "substantive_updated"
        )
        self.store.event(
            connection,
            "knowledge.committed.v1",
            entry,
            entry["entry_id"],
            {
                "entry_id": entry["entry_id"],
                "revision": entry["revision"],
                "change_id": change["change_id"],
                "commit_sha": entry["commit_sha"],
                "growth_type": event_type,
                "public_projection_allowed": entry["publication_eligible"],
            },
            journal.get("trace_id"),
        )
        return entry

    def _recover_writer(
        self, owner_id: str | None = None, actor: Actor | None = None
    ) -> list[dict[str, Any]]:
        results = []
        with self.store.transaction() as connection:
            query = "SELECT data FROM kp_writer_journal WHERE state='prepared'"
            params: tuple[Any, ...] = ()
            if owner_id:
                query += " AND owner_id=?"
                params = (owner_id,)
            for row in connection.execute(query, params).fetchall():
                journal = json.loads(row["data"])
                try:
                    # Already-visible commits must be recorded even after a policy change.
                    # An invisible prepared commit must pass the CURRENT policy before CAS.
                    if not self.writer.contains(journal["commit_sha"]):
                        change = self.store.get(
                            connection, "change", journal["change_id"], journal["owner_id"]
                        )
                        verifier = Actor(
                            journal["owner_id"],
                            "writer-recovery",
                            "service",
                            frozenset({"*"}),
                            actor.allowed_classifications
                            if actor
                            else frozenset({"public", "personal"}),
                        )
                        self._validate_change(connection, {**change, "state": "approved"}, verifier)
                        self._publication_for_change(connection, change, verifier)
                    self.writer.publish(journal)
                    entry = self._finalize_journal(connection, journal)
                    results.append(
                        {
                            "change_id": journal["change_id"],
                            "state": "committed",
                            "entry_id": entry["entry_id"],
                        }
                    )
                except DomainError as exc:
                    if exc.code == "CLASSIFICATION_FORBIDDEN":
                        # A less-privileged restarted worker is not evidence of corruption.
                        # Keep the journal prepared for an appropriately configured writer.
                        results.append(
                            {
                                "change_id": journal["change_id"],
                                "state": "blocked",
                                "error_code": exc.code,
                            }
                        )
                        continue
                    state = "stale" if exc.code == "BASE_STALE" else "reconcile_required"
                    change = self.store.get(
                        connection, "change", journal["change_id"], journal["owner_id"]
                    )
                    change.update(state=state, revision=change["revision"] + 1, updated_at=now())
                    self.store.put(connection, "change", change["change_id"], change)
                    connection.execute(
                        "UPDATE kp_writer_journal SET state=? WHERE change_id=?",
                        (state, change["change_id"]),
                    )
                    results.append(
                        {"change_id": change["change_id"], "state": state, "error_code": exc.code}
                    )
        return results

    def recover_writer(self, *, actor: Actor) -> list[dict[str, Any]]:
        """Reconcile prepared commits using Git evidence, never reset or reapply blindly."""
        authenticated(actor, "knowledge:apply")
        if actor.kind == "agent":
            raise DomainError("FORBIDDEN", "Agents cannot control the writer", status_code=403)
        with self._apply_lock:
            return self._recover_writer(actor.owner_id, actor)

    def apply_change(
        self, change_id: str, *, actor: Actor, idempotency_key: str, trace_id: str | None = None
    ) -> dict[str, Any]:
        """Apply an approved immutable proposal once across SQLite/Git crash boundaries."""
        authenticated(actor, "knowledge:apply")
        if actor.kind == "agent":
            raise DomainError(
                "FORBIDDEN", "Agents may propose but cannot apply knowledge", status_code=403
            )
        self._validate_key(idempotency_key)
        with self._apply_lock:
            self._recover_writer(actor.owner_id, actor)
            operation = f"apply_change:{change_id}"
            input_hash = fingerprint({"change_id": change_id})
            with self.store.transaction() as connection:
                cached = self._receipt(connection, actor, operation, idempotency_key, input_hash)
                if cached is not None:
                    return self._protect_read(connection, cached, actor)
                change = self.store.get(connection, "change", change_id, actor.owner_id)
                self._protect_read(connection, change, actor)
                if change["state"] == "committed":
                    entry = self._entry(
                        connection, change["entry_id"], actor.owner_id, change["committed_revision"]
                    )
                    self._save_receipt(
                        connection, actor, operation, idempotency_key, input_hash, entry
                    )
                    return entry
                self._validate_change(connection, change, actor)
                previous = None
                if change["base_revision"]:
                    previous = self._entry(connection, change["entry_id"], actor.owner_id)
                entry = {
                    key: change[key]
                    for key in (
                        "entry_id",
                        "owner_id",
                        "title",
                        "body",
                        "entry_type",
                        "visibility",
                        "evidence_refs",
                        "metadata",
                        "publication_intent",
                        "publication_eligible",
                        "data_classification",
                        "classification_labels",
                    )
                }
                entry.update(
                    revision=change["base_revision"] + 1,
                    deleted=change["operation"] == "delete",
                    content_hash=text_hash(change["body"]),
                    created_at=previous["created_at"] if previous else change["created_at"],
                    updated_at=change["created_at"],
                    trace_id=trace_id or change.get("trace_id"),
                )
                certificate = self._publication_for_change(connection, change, actor)
                if certificate is not None:
                    entry["publication_eligible"] = True
                    entry["public_artifact"] = certificate["artifact"]
                    entry["publication_certificate"] = {
                        key: certificate[key]
                        for key in (
                            "certificate_id",
                            "policy_id",
                            "policy_version",
                            "candidate_hash",
                            "source_manifest_hash",
                            "input_manifest_hash",
                            "patch_hash",
                            "review_id",
                        )
                    }
                journal = self.writer.prepare(change, entry)
                connection.execute(
                    "INSERT INTO kp_writer_journal(change_id,owner_id,state,data) "
                    "VALUES(?,?,'prepared',?)",
                    (change_id, actor.owner_id, canonical(journal)),
                )
                change.update(state="prepared", revision=change["revision"] + 1, updated_at=now())
                self.store.put(connection, "change", change_id, change)
            self._kill_point("after_prepare", journal)
            # The durable journal precedes ref CAS. A crash after CAS is repaired by ancestry.
            with self.store.transaction() as connection:
                self.writer.publish(journal)
                self._kill_point("after_ref_update", journal)
                entry = self._finalize_journal(connection, journal)
                self._save_receipt(connection, actor, operation, idempotency_key, input_hash, entry)
            self._kill_point("after_commit", journal)
            return entry

    def get_entry(
        self, entry_id: str, *, actor: Actor, revision: int | None = None
    ) -> dict[str, Any]:
        """Read the immutable formal Git body selected by the owned catalog."""
        authenticated(actor, "knowledge:read")
        with self.store.reading() as connection:
            return self._protect_read(
                connection, self._entry(connection, entry_id, actor.owner_id, revision), actor
            )

    def list_entries(
        self, *, actor: Actor, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List owned entries from formal Git, with catalog/hash corruption surfaced."""
        authenticated(actor, "knowledge:read")
        self._validate_page(limit, offset)
        with self.store.reading() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind='entry' AND owner_id=? ORDER BY id",
                (actor.owner_id,),
            )
            visible = self._visible_records(
                connection, [json.loads(row["data"]) for row in rows.fetchall()], actor
            )[offset : offset + limit]
            return self._visible_records(
                connection,
                [self._entry(connection, item["entry_id"], actor.owner_id) for item in visible],
                actor,
            )
