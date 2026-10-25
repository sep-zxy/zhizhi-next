"""Evidence-bounded radar recommendations, persisted in the domain ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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
            metadata = entry.get("metadata") or {}
            questions = metadata.get("open_questions", [])
            if not isinstance(questions, list):
                questions = []
            records = metadata.get("open_question_records")
            question_records = (
                {
                    item["text"]: item
                    for item in records
                    if isinstance(item, dict)
                    and isinstance(item.get("text"), str)
                    and item.get("state") == "open"
                }
                if isinstance(records, list)
                else None
            )
            if question_records is not None:
                closed = {
                    item.get("text")
                    for item in records
                    if isinstance(item, dict)
                    and isinstance(item.get("text"), str)
                    and item.get("state") in {"resolved", "dismissed"}
                }
                question_records = {
                    text: item for text, item in question_records.items() if text not in closed
                }
                questions = list(
                    dict.fromkeys(
                        [
                            *(
                                item
                                for item in questions
                                if isinstance(item, str) and item not in closed
                            ),
                            *question_records,
                        ]
                    )
                )
            for question in questions:
                if (
                    not isinstance(question, str)
                    or not question.strip()
                    or not entry.get("evidence_refs")
                ):
                    continue
                identity_payload = {
                    "entry_id": entry["entry_id"],
                    "question": question,
                    "evidence_refs": entry["evidence_refs"],
                }
                if question in (question_records or {}):
                    identity_payload["question_provenance"] = question_records[question]
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
                if question_records is not None and question in question_records:
                    payload["question_provenance"] = question_records[question]
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
        if state != "proposed":
            if state == target_state:
                return previous
            raise DomainError(
                "INVALID_STATE", "Suggestion already has a different decision", status_code=409
            )
        from ahadiff.knowledge_platform.auth import classification_labels, classify_record

        result: dict[str, Any] = {
            "suggestion_id": suggestion_id,
            "suggestion_state": target_state,
            "classification": classify_record(suggestion),
            "classification_labels": sorted(classification_labels(suggestion)),
        }
        # The immutable decision claim serializes accept/reject across processes.
        # If interrupted later, replay of this same decision resumes safely.
        self.domain.create_task(
            "radar_decision",
            {"suggestion_id": suggestion_id, "decision": decision},
            actor=actor,
            idempotency_key="radar-decision-claim:" + suggestion_id,
        )
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
