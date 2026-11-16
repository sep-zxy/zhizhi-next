"""Independent AI review and user-only, single-use, exact-diff approval challenges."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from .auth import Actor, authenticated
from .contracts import MAJOR_RISKS, fingerprint, new_id, now, validate_payload
from .errors import DomainError, invalid
from .publication import reject_publication_proofs


class ReviewMethods:
    """Reviews cannot derive user authority from an AI response or request field."""

    def create_decision_challenge(self, change_id: str, *, actor: Actor) -> dict[str, Any]:
        """Issue a short-lived user-only nonce bound to the exact current proposal."""
        authenticated(actor, "knowledge:review")
        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN", "Only the authenticated user may request approval", status_code=403
            )
        with self.store.transaction() as connection:
            change = self.store.get(connection, "change", change_id, actor.owner_id)
            if change["state"] not in {"needs_user", "needs_ai_review", "approved"}:
                raise DomainError(
                    "INVALID_STATE", "This proposal cannot be approved", status_code=409
                )
            if change["policy_version"] != self.policy_version:
                raise DomainError(
                    "REVIEW_REQUIRED", "Policy changed; generate a fresh proposal", status_code=409
                )
            if self.writer.head() != change["base_git_revision"]:
                raise DomainError("BASE_STALE", "The proposal base changed", status_code=409)
            nonce = secrets.token_urlsafe(32)
            record = {
                "owner_id": actor.owner_id,
                "actor_id": actor.actor_id,
                "change_id": change_id,
                "patch_hash": change["patch_hash"],
                "base_revision": change["base_revision"],
                "base_git_revision": change["base_git_revision"],
                "evidence_hash": change["evidence_hash"],
                "policy_version": self.policy_version,
                "used": False,
                "revision": 1,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
            self.store.put(connection, "decision_nonce", fingerprint(nonce), record)
            return {
                key: record[key]
                for key in (
                    "change_id",
                    "patch_hash",
                    "base_revision",
                    "policy_version",
                    "expires_at",
                )
            } | {"decision_nonce": nonce}

    def record_review(
        self,
        change_id: str,
        payload: dict[str, Any],
        *,
        actor: Actor,
        idempotency_key: str,
        trace_id: str | None = None,
        reviewed_fields: tuple[str, ...] = (),
        reviewed_relations_hash: str | None = None,
        publication_review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record bound review; AI may escalate but can never approve a major change."""
        authenticated(actor, "knowledge:review")
        payload = validate_payload(payload)
        reject_publication_proofs(payload)
        if publication_review is not None:
            publication_review = validate_payload(publication_review)
        if {"reviewed_fields", "reviewed_relations_hash"}.intersection(payload):
            raise DomainError(
                "FORBIDDEN", "Review coverage comes from trusted reviewer code", status_code=403
            )
        if not isinstance(reviewed_fields, tuple) or any(
            not isinstance(field, str) or not field or len(field) > 100 for field in reviewed_fields
        ):
            raise invalid("reviewed_fields must be a tuple of bounded field names")
        decision = payload.get("decision")
        if decision not in {"approve", "reject", "needs_user", "needs_evidence"}:
            raise invalid("Unknown review decision")
        if not isinstance(payload.get("reason", ""), str):
            raise invalid("Review reason must be text")
        if not isinstance(payload.get("risk_flags", []), list):
            raise invalid("risk_flags must be a list")

        def review(connection: Any) -> dict[str, Any]:
            change = self.store.get(connection, "change", change_id, actor.owner_id)
            if reviewed_relations_hash is not None:
                relations = change.get("metadata", {}).get("relations")
                if (
                    decision != "approve"
                    or "metadata" not in reviewed_fields
                    or not isinstance(relations, list)
                    or not relations
                    or reviewed_relations_hash != fingerprint(relations)
                ):
                    raise invalid("Relation review coverage must bind the exact approved metadata")
            if change["state"] in {
                "committed",
                "prepared",
                "rejected",
                "stale",
                "no_change",
                "reconcile_required",
            }:
                raise DomainError("INVALID_STATE", "Proposal is not reviewable", status_code=409)
            if (
                payload.get("expected_patch_hash", payload.get("patch_hash"))
                != change["patch_hash"]
            ):
                raise DomainError(
                    "REVIEW_REQUIRED", "Review must bind the exact patch hash", status_code=409
                )
            if change["policy_version"] != self.policy_version:
                raise DomainError("REVIEW_REQUIRED", "Review policy has changed", status_code=409)
            if self.writer.head() != change["base_git_revision"]:
                raise DomainError("BASE_STALE", "Proposal base has changed", status_code=409)
            if actor.kind != "user" and actor.actor_id == change["proposer_id"]:
                raise DomainError(
                    "FORBIDDEN", "A proposal needs an independent reviewer", status_code=403
                )
            risks = set(change["risk_flags"]) | set(payload.get("risk_flags", []))
            if not all(isinstance(item, str) for item in risks):
                raise invalid("Risk names must be strings")
            if decision == "needs_user" and not risks.intersection(MAJOR_RISKS):
                raise invalid("User escalation must identify a major risk")
            if decision == "approve" and risks.intersection(MAJOR_RISKS) and actor.kind != "user":
                raise DomainError(
                    "REVIEW_REQUIRED", "Major changes require user approval", status_code=409
                )
            if actor.kind == "user":
                nonce = payload.get("decision_nonce")
                if not isinstance(nonce, str) or not nonce:
                    raise DomainError(
                        "REVIEW_REQUIRED",
                        "An exact-diff user challenge is required",
                        status_code=409,
                    )
                try:
                    challenge = self.store.get(
                        connection, "decision_nonce", fingerprint(nonce), actor.owner_id
                    )
                except DomainError as exc:
                    raise DomainError(
                        "REVIEW_REQUIRED", "Approval challenge is invalid", status_code=409
                    ) from exc
                bound = all(
                    challenge[key] == change[key]
                    for key in (
                        "change_id",
                        "patch_hash",
                        "base_revision",
                        "base_git_revision",
                        "evidence_hash",
                        "policy_version",
                    )
                )
                if (
                    challenge["used"]
                    or challenge["expires_at"] <= now()
                    or not bound
                    or challenge["actor_id"] != actor.actor_id
                    or payload.get("base_revision") != change["base_revision"]
                ):
                    raise DomainError(
                        "REVIEW_REQUIRED",
                        "Approval challenge expired, was used or no longer matches",
                        status_code=409,
                    )
                challenge.update(used=True, revision=challenge["revision"] + 1)
                self.store.put(connection, "decision_nonce", fingerprint(nonce), challenge)
            result = {
                "review_id": new_id("rev"),
                "change_id": change_id,
                "owner_id": actor.owner_id,
                "actor_id": actor.actor_id,
                "actor_kind": actor.kind,
                "decision": decision,
                "reason": payload.get("reason", ""),
                "risk_flags": sorted(risks),
                "patch_hash": change["patch_hash"],
                "base_revision": change["base_revision"],
                "base_git_revision": change["base_git_revision"],
                "evidence_hash": change["evidence_hash"],
                "policy_version": self.policy_version,
                "revision": 1,
                "reviewer_model": payload.get("reviewer_model"),
                "reviewed_fields": sorted(set(reviewed_fields)),
                "reviewed_relations_hash": reviewed_relations_hash,
                "trace_id": trace_id,
                "created_at": now(),
            }
            change["risk_flags"] = sorted(risks)
            if publication_review is not None:
                certificate = self._issue_publication_certificate(
                    connection, change, result, publication_review, actor
                )
                change["publication_certificate_id"] = certificate["certificate_id"]
                result["publication_certificate_id"] = certificate["certificate_id"]
            change["review_level"] = "user" if risks & MAJOR_RISKS else change["review_level"]
            change["state"] = {
                "approve": "approved",
                "reject": "rejected",
                "needs_user": "needs_user",
                "needs_evidence": "needs_evidence",
            }[decision]
            change.update(revision=change["revision"] + 1, updated_at=now())
            self.store.put(connection, "review", result["review_id"], result)
            self.store.put(connection, "change", change_id, change)
            self.store.event(
                connection,
                "change.reviewed.v1",
                change,
                change_id,
                {
                    "review_id": result["review_id"],
                    "state": change["state"],
                    "patch_hash": change["patch_hash"],
                    "reviewed_fields": result["reviewed_fields"],
                    "reviewed_relations_hash": reviewed_relations_hash,
                },
                trace_id,
            )
            if actor.kind == "user" and decision == "approve":
                # Approval schedules the service writer; no user/agent receives Git authority.
                existing_task = self._unique(
                    connection, actor, "approved_apply", change_id, change["patch_hash"]
                )
                if existing_task:
                    result["task_id"] = existing_task
                    result["change_state"] = change["state"]
                    self.store.put(connection, "review", result["review_id"], result)
                    return result
                timestamp = now()
                task = {
                    "task_id": new_id("tsk"),
                    "owner_id": actor.owner_id,
                    "kind": "knowledge.apply",
                    "state": "queued",
                    "payload": {"change_id": change_id},
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
                self._save_unique(
                    connection,
                    actor,
                    "approved_apply",
                    change_id,
                    change["patch_hash"],
                    task["task_id"],
                )
                self.store.event(connection, "task.queued.v1", task, task["task_id"])
                result["task_id"] = task["task_id"]
                self.store.put(connection, "review", result["review_id"], result)
            result["change_state"] = change["state"]
            return result

        binding = payload
        if reviewed_fields or reviewed_relations_hash is not None:
            binding = {
                **payload,
                "reviewed_fields": list(reviewed_fields),
                "reviewed_relations_hash": reviewed_relations_hash,
            }
        if publication_review is not None:
            binding = {**binding, "publication_review": publication_review}
        return self._mutate(actor, f"record_review:{change_id}", idempotency_key, binding, review)

    def list_reviews(
        self, *, actor: Actor, state: str | None = "needs_user", limit: int = 100
    ) -> list[dict[str, Any]]:
        """List owner-scoped proposal decisions, not a misleading generic AI error queue."""
        authenticated(actor, "knowledge:read")
        self._validate_page(limit, 0)
        with self.store.reading() as connection:
            rows = connection.execute(
                "SELECT data FROM kp_records WHERE kind='change' AND owner_id=? ORDER BY rowid",
                (actor.owner_id,),
            ).fetchall()
            changes = [json.loads(row["data"]) for row in rows]
            return self._visible_records(
                connection,
                [change for change in changes if state is None or change["state"] == state],
                actor,
            )[:limit]
