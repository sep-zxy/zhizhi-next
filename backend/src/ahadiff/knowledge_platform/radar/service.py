"""Evidence-bounded radar recommendations, persisted in the domain ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast


def _open_questions(entry: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None]]:
    metadata: dict[str, Any] = entry.get("metadata") or {}
    legacy = metadata.get("open_questions", [])
    questions = cast("list[Any]", legacy) if isinstance(legacy, list) else []
    records = metadata.get("open_question_records")
    provenance: dict[str, dict[str, Any]] = {}
    closed: set[str] = set()
    if isinstance(records, list):
        for item in cast("list[Any]", records):
            if not isinstance(item, dict):
                continue
            record = cast("dict[str, Any]", item)
            text = record.get("text")
            if not isinstance(text, str):
                continue
            if record.get("state") == "open":
                provenance[text] = record
            elif record.get("state") in {"resolved", "dismissed"}:
                closed.add(text)
        questions = list(dict.fromkeys([
            *(question for question in questions if isinstance(question, str)), *provenance,
        ]))
    return [
        (question, provenance.get(question))
        for question in questions
        if isinstance(question, str) and question.strip() and question not in closed
    ]


class RadarService:
    """Recommend explicit unresolved questions, not speculative ability scores."""

    def __init__(self, domain: Any) -> None:
        self.domain = domain

    def read(self, suggestion_id: str, *, actor: Any) -> dict[str, Any]:
        """Restore a real accepted exercise, without inventing questions or scores."""
        from ahadiff.knowledge_platform.auth import classification_labels, classify_record
        from ahadiff.knowledge_platform.errors import DomainError

        suggestion = self.domain.read_task(suggestion_id, actor=actor)
        if suggestion["kind"] != "radar_suggestion":
            raise DomainError("INVALID_INPUT", "Task is not a radar suggestion", status_code=422)
        payload = suggestion["payload"]
        state = self.domain.get_channel_state("radar:" + suggestion_id, actor=actor)
        reference = payload.get("source_reference")
        entry = self.domain.get_entry(
            payload["target_entry_ids"][0], actor=actor,
            **({"revision": reference["revision"]} if reference else {}),
        )
        if reference and (entry["entry_id"] != reference["entry_id"] or
                          entry["content_hash"] != reference["expected_content_hash"]):
            raise DomainError("CONTENT_CONFLICT", "Pinned practice source changed", status_code=409)
        if entry.get("deleted"):
            raise DomainError("INVALID_STATE", "Exercise target was deleted", status_code=409)
        result = {
            "suggestion_id": suggestion_id,
            "suggestion_state": "proposed",
            **state,
            "classification": classify_record({"suggestion": payload, "entry": entry}),
            "classification_labels": sorted(
                classification_labels({"suggestion": payload, "entry": entry})
            ),
            "user_text": "",
            "practice": {
                "entry_id": entry["entry_id"],
                "title": entry["title"],
                "body": entry["body"],
                "question": payload["proposed_exercise"],
                **({"source_reference": reference} if reference else {}),
            },
        }
        if state.get("learning_outcome_id"):
            outcome = self.domain.get_learning_outcome(state["learning_outcome_id"], actor=actor)
            result["user_text"] = outcome["payload"]["user_text"]
            result["classification"] = classify_record({"practice": result, "outcome": outcome})
            result["classification_labels"] = sorted(
                classification_labels({"practice": result, "outcome": outcome})
            )
        return result

    def validate_submission(self, payload: dict[str, Any], *, actor: Any) -> dict[str, Any]:
        """Bind user facts to an accepted suggestion before any outcome is persisted."""
        from ahadiff.knowledge_platform.auth import classification_labels, classify_record
        from ahadiff.knowledge_platform.contracts import required_text, validate_payload
        from ahadiff.knowledge_platform.errors import DomainError

        payload = validate_payload(payload)
        required_text(payload, "device_id", limit=256)
        required_text(payload, "session_id", limit=256)
        if (
            not isinstance(payload.get("metadata"), dict)
            or not isinstance(payload.get("result", {}), dict)
            or isinstance(payload.get("version"), bool)
            or not isinstance(payload.get("version"), int)
        ):
            raise DomainError("INVALID_INPUT", "Invalid learning facts", status_code=422)
        if (
            actor.kind != "user"
            or not isinstance(payload.get("user_text"), str)
            or not payload["user_text"].strip()
            or len(payload["user_text"]) > 20000
        ):
            raise DomainError(
                "INVALID_INPUT", "A user-authored response is required", status_code=422
            )
        metadata = payload.get("metadata") or {}
        practice = self.read(metadata["radar_suggestion_id"], actor=actor)
        if practice["suggestion_state"] not in {"accepted", "completed"}:
            raise DomainError("INVALID_STATE", "Suggestion has not been accepted", status_code=409)
        if payload.get("entry_id") != practice["practice"]["entry_id"]:
            raise DomainError(
                "INVALID_INPUT", "Learning outcome does not target this suggestion", status_code=422
            )
        if (
            payload.get("session_id") != "radar:" + metadata["radar_suggestion_id"]
            or payload.get("version") != 1
        ):
            raise DomainError(
                "INVALID_INPUT", "Radar completion requires its immutable session", status_code=422
            )
        if practice.get("learning_outcome_id"):
            recorded = self.domain.get_learning_outcome(
                practice["learning_outcome_id"], actor=actor
            )["payload"]
            if any(
                payload.get(key) != recorded.get(key)
                for key in ("device_id", "session_id", "version", "user_text", "entry_id")
            ):
                raise DomainError(
                    "INVALID_STATE",
                    "This exercise already has a submitted response",
                    status_code=409,
                )
        classifications = {
            "submitted": payload,
            "practice": practice,
            "user_expression_classification": {"classification": "personal"},
        }
        response_identity = {
            key: payload.get(key)
            for key in ("device_id", "session_id", "version", "user_text", "entry_id")
        }
        # Atomically reserve this immutable completion before writing its outcome.
        # Different devices cannot both turn one accepted exercise into two facts.
        self.domain.create_task(
            "radar_submission_claim",
            {
                "suggestion_id": metadata["radar_suggestion_id"],
                "response_hash": hashlib.sha256(
                    json.dumps(
                        response_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "classification_labels": sorted(classification_labels(classifications)),
            },
            actor=actor,
            idempotency_key="radar-submission-claim:" + metadata["radar_suggestion_id"],
        )
        return {
            **payload,
            "result": {"practice_kind": "open_response", "graded": False},
            "metadata": {
                **metadata,
                "evidence_classification": classify_record(classifications),
                **({"source_reference": practice["practice"]["source_reference"]}
                   if "source_reference" in practice["practice"] else {}),
                "classification_labels": sorted(classification_labels(classifications)),
            },
        }

    def run(self, *, actor: Any, limit: int = 3) -> list[dict[str, Any]]:
        """Persist up to three evidence-versioned candidates without enrolling."""
        from ahadiff.knowledge_platform.auth import (
            classification_labels,
            classify_record,
            enforce_read_classification,
        )
        from ahadiff.knowledge_platform.search.index import SearchService

        suggestions = []
        for entry in SearchService(self.domain).entries(actor):
            enforce_read_classification(actor, entry)
            if not entry.get("evidence_refs"):
                continue
            for question, provenance in _open_questions(entry):
                identity_payload = {
                    "entry_id": entry["entry_id"],
                    "question": question,
                    "evidence_refs": entry["evidence_refs"],
                }
                if provenance is not None:
                    identity_payload["question_provenance"] = provenance
                identity = hashlib.sha256(
                    json.dumps(
                        identity_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                payload = {
                    "target_entry_ids": [entry["entry_id"]],
                    "reason": "条目明确记录了一个有来源的待验证问题。",
                    "evidence_refs": entry["evidence_refs"],
                    "relation_hypothesis": None,
                    "proposed_exercise": question,
                    "expected_artifact": "用户解释或可复核的实践证据",
                    "suggestion_state": "proposed",
                    "classification": classify_record(entry),
                    "classification_labels": sorted(classification_labels(entry)),
                }
                if provenance is not None:
                    payload["question_provenance"] = provenance
                task = self.domain.create_task(
                    "radar_suggestion",
                    payload,
                    actor=actor,
                    idempotency_key="radar-candidate:" + identity,
                )
                state = self.domain.get_channel_state("radar:" + task["task_id"], actor=actor).get(
                    "suggestion_state", "proposed"
                )
                if state != "proposed":
                    continue
                suggestions.append(
                    {
                        "suggestion_id": task["task_id"],
                        **payload,
                        "state": state,
                        "created_at": task["created_at"],
                    }
                )
                if len(suggestions) >= min(max(limit, 1), 3):
                    return suggestions
        return suggestions

    def _require_snoozed_target(self, payload: dict[str, Any], *, actor: Any) -> None:
        from ahadiff.knowledge_platform.errors import DomainError

        entry: dict[str, Any] = self.domain.get_entry(payload["target_entry_ids"][0], actor=actor)
        if (
            entry.get("deleted")
            or not entry.get("evidence_refs")
            or not any(question == payload["proposed_exercise"] for question, _ in _open_questions(entry))
        ):
            raise DomainError(
                "SUGGESTION_UNAVAILABLE", "The saved question is no longer open", status_code=409
            )

    def list_snoozed(self, *, actor: Any, before: str | None = None) -> dict[str, Any]:
        """List saved suggestions without generating candidates or enrolling the user."""
        from ahadiff.knowledge_platform.auth import classification_labels, classify_record
        from ahadiff.knowledge_platform.errors import DomainError, invalid

        if before is not None and (not before.startswith("tsk_") or len(before) > 100):
            raise invalid("Invalid snoozed suggestion cursor")
        page_size = 20
        tasks: list[dict[str, Any]] = self.domain.list_snoozed_radar_tasks(
            actor=actor, before=before, limit=page_size + 1
        )
        suggestions: list[dict[str, Any]] = []
        for task in tasks[:page_size]:
            payload = task["payload"]
            try:
                self._require_snoozed_target(payload, actor=actor)
            except DomainError as error:
                if error.code in {"NOT_FOUND", "CLASSIFICATION_FORBIDDEN", "SUGGESTION_UNAVAILABLE"}:
                    continue
                raise
            suggestions.append({
                "suggestion_id": task["task_id"],
                "suggestion_state": "snoozed",
                "proposed_exercise": payload["proposed_exercise"],
                "reason": payload["reason"],
                "expected_artifact": payload.get("expected_artifact"),
                "classification": classify_record(task),
                "classification_labels": sorted(classification_labels(task)),
            })
        return {
            "suggestions": suggestions,
            "next_cursor": tasks[page_size - 1]["task_id"] if len(tasks) > page_size else None,
            **({"data_classification": "public"} if not suggestions else {}),
        }

    def decide(self, suggestion_id: str, decision: str, *, actor: Any) -> dict[str, Any]:
        """Accept once, preserving rejection until the evidence changes."""
        from ahadiff.knowledge_platform.errors import DomainError

        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN", "Only the user can accept a learning suggestion", status_code=403
            )
        if decision not in {"accept", "reject", "snooze"}:
            raise DomainError("INVALID_INPUT", "Unknown radar decision", status_code=422)
        suggestion = self.domain.read_task(suggestion_id, actor=actor)
        if suggestion["kind"] != "radar_suggestion":
            raise DomainError("INVALID_INPUT", "Task is not a radar suggestion", status_code=422)
        previous = self.domain.get_channel_state("radar:" + suggestion_id, actor=actor)
        state = previous.get("suggestion_state", "proposed")
        target_state = {"accept": "accepted", "reject": "rejected", "snooze": "snoozed"}[decision]
        ordinary = "plan_hash" not in suggestion["payload"]
        resuming = ordinary and state == "snoozed" and decision == "accept"
        if state == target_state:
            return previous
        if state != "proposed" and not resuming:
            raise DomainError(
                "INVALID_STATE", "Suggestion already has a different decision", status_code=409
            )
        if resuming:
            self._require_snoozed_target(suggestion["payload"], actor=actor)
        from ahadiff.knowledge_platform.auth import classification_labels, classify_record

        result: dict[str, Any] = {
            "suggestion_id": suggestion_id,
            "suggestion_state": target_state,
            "classification": classify_record(suggestion),
            "classification_labels": sorted(classification_labels(suggestion)),
        }
        # Keep historical first decisions immutable. A saved snooze opens a separate accept claim.
        claim_key = "radar-decision-claim:" + suggestion_id
        if resuming:
            claim_key += ":after-snooze"
        try:
            self.domain.create_task(
                "radar_decision",
                {"suggestion_id": suggestion_id, "decision": decision},
                actor=actor,
                idempotency_key=claim_key,
            )
        except DomainError as error:
            if (
                ordinary and state == "proposed" and decision == "accept"
                and error.code == "IDEMPOTENCY_CONFLICT"
            ):
                # A competing first decision may still be saving its state.
                raise DomainError(error.code, str(error), retryable=True, status_code=409) from error
            raise
        if decision == "accept":
            learning = self.domain.create_task(
                "learning",
                {
                    "suggestion_id": suggestion_id,
                    **suggestion["payload"],
                },
                actor=actor,
                idempotency_key="radar:" + suggestion_id,
            )
            result["learning_id"] = learning["task_id"]
        self.domain.set_channel_state(
            "radar:" + suggestion_id,
            result,
            actor=actor,
            idempotency_key=f"radar-decision:{suggestion_id}:{decision}",
        )
        return result

    def complete(self, suggestion_id: str, outcome_id: str, *, actor: Any) -> dict[str, Any]:
        """Link genuine submitted learning facts without confirming a hypothesis."""
        from ahadiff.knowledge_platform.errors import DomainError

        if actor.kind != "user":
            raise DomainError(
                "FORBIDDEN", "Only the user can complete a learning exercise", status_code=403
            )

        suggestion = self.domain.read_task(suggestion_id, actor=actor)
        previous = self.domain.get_channel_state("radar:" + suggestion_id, actor=actor)
        if suggestion["kind"] != "radar_suggestion" or previous.get("suggestion_state") not in {
            "accepted",
            "completed",
        }:
            raise DomainError("INVALID_STATE", "Suggestion has not been accepted", status_code=409)
        outcome = self.domain.get_learning_outcome(outcome_id, actor=actor)
        payload = outcome.get("payload", outcome)
        if (
            payload.get("metadata", {}).get("radar_suggestion_id") != suggestion_id
            or not str(payload.get("user_text", "")).strip()
            or payload.get("session_id") != "radar:" + suggestion_id
            or payload.get("version") != 1
        ):
            raise DomainError(
                "INVALID_INPUT", "Outcome is not this user's exercise response", status_code=422
            )
        if previous.get("learning_outcome_id") not in {None, outcome_id}:
            raise DomainError(
                "INVALID_STATE", "Completion cannot replace an existing response", status_code=409
            )
        if payload.get("entry_id") not in suggestion["payload"]["target_entry_ids"]:
            raise DomainError(
                "INVALID_INPUT", "Learning outcome does not target this suggestion", status_code=422
            )
        self.validate_submission(payload, actor=actor)
        self.domain.complete_learning_task(
            previous["learning_id"],
            outcome_id,
            actor=actor,
            idempotency_key="radar-learning-completed:" + suggestion_id,
        )
        result = {
            **previous,
            "suggestion_state": "completed",
            "learning_outcome_id": outcome_id,
            "relation_confirmed": False,
        }
        self.domain.set_channel_state(
            "radar:" + suggestion_id,
            result,
            actor=actor,
            idempotency_key=f"radar-completed:{suggestion_id}:{outcome_id}",
        )
        return result
