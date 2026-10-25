"""Deterministic exact-source practice previews; only explicit users enroll."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ahadiff.growth.knowledge_focus import POLICY_VERSION, assess_focus

from ..auth import classification_labels, classify_record
from ..errors import DomainError

QUESTION = (
    "仅依据此正式版本，指出一个组件职责或技术栈机制、适用边界和设计取舍；"
    "比较一种替代方案，并说明条件改变时预期结果如何变化。"
    "证据不足请明确缺口，不推断完整系统架构；请用本人原话作答。"
)


def preview_plan(domain: Any, payload: dict[str, Any], *, actor: Any) -> dict[str, Any]:
    """Read one authorized immutable revision, without tasks, models or user text."""
    if set(payload) != {"entry_id", "revision", "expected_content_hash", "focus"} or (
        not isinstance(payload.get("entry_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", payload["entry_id"])
        or type(payload.get("revision")) is not int
        or not 1 <= payload["revision"] <= 2**53 - 1
        or not isinstance(payload.get("expected_content_hash"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", payload["expected_content_hash"])
        or payload.get("focus") != "architecture"
    ):
        raise DomainError("INVALID_INPUT", "Exact source and architecture focus required", status_code=422)
    entry = domain.get_entry(payload["entry_id"], revision=payload["revision"], actor=actor)
    if entry.get("deleted") or entry.get("content_hash") != payload["expected_content_hash"]:
        raise DomainError("CONTENT_CONFLICT", "Exact source is unavailable or changed", status_code=409)
    plan = {
        "source_reference": {key: payload[key] for key in ("entry_id", "revision", "expected_content_hash")},
        "focus": "architecture",
        "policy_version": POLICY_VERSION,
        "title": entry["title"],
        "question": QUESTION,
        "learning_focus": assess_focus(entry["title"], entry["body"]),
        "evidence_refs": entry.get("evidence_refs", []),
        "classification": classify_record(entry),
        "classification_labels": sorted(classification_labels(entry)),
        "authorship": "deterministic_template_not_user_answer",
    }
    digest = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**plan, "plan_hash": digest, "state": "preview", "published": False}


def accept_plan(domain: Any, payload: dict[str, Any], *, actor: Any) -> dict[str, Any]:
    """Revalidate the shown plan and resume the existing immutable radar decision."""
    from ..radar.service import RadarService

    if actor.kind != "user":
        raise DomainError("FORBIDDEN", "Only the user can accept a plan", status_code=403)
    if set(payload) != {"entry_id", "revision", "expected_content_hash", "focus", "plan_hash"}:
        raise DomainError("INVALID_INPUT", "Exact plan confirmation required", status_code=422)
    plan = preview_plan(domain, {key: value for key, value in payload.items() if key != "plan_hash"}, actor=actor)
    if payload["plan_hash"] != plan["plan_hash"]:
        raise DomainError("PLAN_CHANGED", "Preview the current plan before accepting", status_code=409)
    suggestion = domain.create_task(
        "radar_suggestion",
        {"target_entry_ids": [payload["entry_id"]], "source_reference": plan["source_reference"],
         "reason": "本人明确接受精确正式版本的架构练习计划。", "proposed_exercise": plan["question"],
         "expected_artifact": "用户解释或可复核的实践证据", "suggestion_state": "proposed",
         "plan_hash": plan["plan_hash"], "focus": plan["focus"], "evidence_refs": plan["evidence_refs"],
         "classification": plan["classification"], "classification_labels": plan["classification_labels"]},
        actor=actor, idempotency_key="learning-plan:" + plan["plan_hash"],
    )
    previous = domain.get_channel_state("radar:" + suggestion["task_id"], actor=actor)
    # Another device may already have completed this immutable exercise. A replay
    # restores that task, never reenrolls or erases the observed completion.
    if previous.get("suggestion_state") == "completed":
        decision = previous
    else:
        try:
            decision = RadarService(domain).decide(suggestion["task_id"], "accept", actor=actor)
        except DomainError as error:
            # Completion can race between the read above and the decision read.
            # Only an actual completed state recovers; reject/snooze stay rejected.
            current = domain.get_channel_state("radar:" + suggestion["task_id"], actor=actor)
            if error.code != "INVALID_STATE" or current.get("suggestion_state") != "completed":
                raise
            decision = current
    return {**decision, "plan_hash": plan["plan_hash"], "source_reference": plan["source_reference"], "published": False}
