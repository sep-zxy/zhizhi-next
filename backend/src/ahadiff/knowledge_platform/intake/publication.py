"""Read-only publication status; acquisition is not a copyright/privacy grant."""

from __future__ import annotations

from typing import Any

from ..auth import classification_labels

REASON_LABELS = {
    "SOURCE_CLASSIFICATION_PRIVATE": "来源尚不满足公开分类要求",
    "SOURCE_RIGHTS_UNVERIFIED": "来源权利未核实，不能据此公开原文",
    "DERIVATIVE_REVIEW_PENDING": "原创摘要资格及独立审核尚未完成",
    "PERSONAL_OR_PRIVACY_REVIEW_REQUIRED": "涉及个人立场或隐私，待复核",
    "LEARNING_STATEMENT_PRIVATE": "个人学习原话保持私有",
    "PUBLICATION_STATUS_UNAVAILABLE": "当前公开资格暂无法核对",
    "PUBLICATION_REVIEW_PENDING": "公开资格仍待领域审核",
    "DERIVATIVE_TEXT_BOUNDS": "原创摘要长度不符合公开资格规则",
    "DERIVATIVE_SENSITIVE_PATTERN": "摘要检测到敏感信息风险，待复核",
    "DERIVATIVE_ASSET_OR_QUOTATION": "摘要含未获资格的素材或引用形式",
    "DERIVATIVE_UNVERIFIED_LINK": "摘要链接缺少可核对来源",
    "DERIVATIVE_UNSAFE_CITATION": "摘要引用不满足安全来源要求",
    "DERIVATIVE_COPY_ALARM": "摘要与原文重合触发复核，不代表法律侵权认定",
    "DERIVATIVE_REVIEW_INCOMPLETE": "独立原创摘要资格复核未全部通过",
    "PUBLICATION_BLOCKED": "领域上下文未通过公开资格校验",
    "EXPLICIT_PRIVATE_INTENT": "已按明确私有意图保留，不自动公开",
}


def status_projection(
    record: dict[str, Any],
    *,
    authoritative: dict[str, Any] | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Never approve a derivative here; only echo a domain ChangeSet/Entry decision."""
    metadata = record.get("metadata") or (record.get("descriptor") or {}).get("metadata") or {}
    intent = metadata.get("publication_intent", "public")
    source_private_intent = intent == "private"
    reasons: list[str] = []
    if classification_labels(record, default="personal") != {"public"}:
        reasons.append("SOURCE_CLASSIFICATION_PRIVATE")
    # RAW rights remain unknown. This is not a ruling that original summaries
    # necessarily need a reproduction licence; A/C must establish that policy.
    if record.get("rights", "unknown") not in {
        "public",
        "public_domain",
        "licensed",
        "summary_allowed",
    }:
        reasons.append("SOURCE_RIGHTS_UNVERIFIED")
    reasons.append("DERIVATIVE_REVIEW_PENDING")
    # A verified, context-separated document starts a distinct derivative review;
    # the private RAW's classification/rights no longer falsely claim that every
    # original summary is permanently blocked. This still grants no eligibility.
    if record.get("external_document_id"):
        reasons = ["DERIVATIVE_REVIEW_PENDING"]
    eligibility = "blocked" if len(reasons) > 1 else "pending_review"
    authority_id = None
    if authoritative is not None:
        intent = (
            "private" if source_private_intent else authoritative.get("publication_intent", intent)
        )
        authority_id = authoritative.get("change_id") or authoritative.get("entry_id")
        eligible = authoritative.get("publication_eligible") is True
        if eligible:
            eligibility, reasons = "eligible", []
        else:
            reason = (authoritative.get("metadata") or {}).get("publication_block_reason")
            if isinstance(reason, str) and reason in REASON_LABELS:
                reasons = [reason]
            elif not reasons:
                reasons = ["PUBLICATION_REVIEW_PENDING"]
            eligibility = (
                "pending_review"
                if reasons in (["PUBLICATION_REVIEW_PENDING"], ["DERIVATIVE_REVIEW_PENDING"])
                else "blocked"
            )
    if (
        eligibility != "eligible"
        and isinstance(blocked_reason, str)
        and blocked_reason in REASON_LABELS
    ):
        reasons = [blocked_reason]
        eligibility = (
            "pending_review"
            if blocked_reason in {"DERIVATIVE_REVIEW_PENDING", "PUBLICATION_REVIEW_PENDING"}
            else "blocked"
        )
    # An explicit private choice is a normal result, not a publication failure;
    # it also overrides a previously eligible derivative's stale public flag.
    if intent == "private":
        eligibility, reasons = "not_requested", ["EXPLICIT_PRIVATE_INTENT"]
    return {
        "publication_intent": intent
        if isinstance(intent, str) and intent in {"public", "private", "legacy_unclassified"}
        else "private",
        "eligibility": eligibility,
        "publication_eligible": eligibility == "eligible",
        "reason_codes": reasons,
        "reason_details": [REASON_LABELS[reason] for reason in reasons],
        "authority_id": authority_id,
        "raw_visibility": "private",
        "raw_rights": record.get("rights", "unknown"),
        "publication_delivery": "not_verified",
    }


def status_text(status: dict[str, Any]) -> str:
    """Fixed native-channel wording, never arbitrary model output."""
    if status["eligibility"] == "not_requested":
        return "公开意图：保持私有。已按明确私有意图保留，不自动公开。原始材料仍私有。"
    intent = "公开" if status["publication_intent"] == "public" else "保持私有"
    state = {"eligible": "已获领域审核资格", "blocked": "受阻", "pending_review": "待审核"}[
        status["eligibility"]
    ]
    result = f"公开意图：{intent}。当前资格：{state}。"
    reasons = status.get("reason_codes") or []
    if reasons:
        result += (
            "原因：" + "；".join(REASON_LABELS.get(reason, "待复核") for reason in reasons) + "。"
        )
    return result + "原始材料仍私有；不代表已经发布。"
