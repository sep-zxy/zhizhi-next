"""Architecture/technology focus without rewriting historical concept identity."""

from __future__ import annotations

import re
from typing import Any

POLICY_VERSION = "architecture-stack-v1"
FOCUS_PROMPT = (
    "学习重点是架构设计和技术栈深度运用，不是代码语法细节。"
    "优先解释系统组件职责与边界、数据流与一致性、并发与资源模型、可靠性与故障传播、"
    "安全边界、部署和演进，以及技术栈的核心机制、特性、约束、取舍和适用场景。"
    "把局部代码变化提升为证据能支持的可迁移设计问题：为什么这样设计，"
    "还有什么方案，分别付出什么代价，条件改变后何时应换方案，如何深入应用该技术。"
    "代码只用于必要的机制证据与最小示例，不逐行讲解，也不默认深挖语法、API拼写或语言冷知识。"
    "技术栈名字不是合并依据；同一技术的不同机制、约束和取舍应保留独立主题。"
    "证据不足时明确缺口，不把局部片段推断成已存在的全系统架构；只有语法变化时可以不提新机会。"
    "练习优先考察设计原因、方案取舍、适用边界和场景迁移，预测系统结果而非背诵语法输出。"
    "用户明确追问具体语法时仍可按需解答；保留既有学习历史，不改写用户原话或声称已经掌握。\n"
)

_ARCHITECTURE = (
    (
        "consistency",
        ("一致性", "事务边界", "事务隔离", "幂等", "补偿事务", "consistency", "transaction"),
    ),
    (
        "reliability",
        ("可靠性", "可用性", "熔断", "背压", "故障", "重试策略", "容错", "backpressure"),
    ),
    (
        "boundaries",
        ("架构", "职责", "边界", "解耦", "分层", "微服务", "architecture", "bounded context"),
    ),
    (
        "security",
        ("认证", "授权", "越权", "租户隔离", "权限模型", "authentication", "authorization"),
    ),
    ("resources", ("并发模型", "线程池", "连接池", "资源模型", "调度", "内存模型", "executor")),
    (
        "evolution",
        ("扩展性", "可扩展", "演进", "部署", "迁移策略", "可观测", "scalability", "observability"),
    ),
)
_STACKS = (
    "postgresql",
    "postgres",
    "mysql",
    "redis",
    "kafka",
    "rabbitmq",
    "spring",
    "react",
    "vue",
    "kubernetes",
    "docker",
    "sqlite",
    "netty",
    "completablefuture",
)
_SYNTAX = (
    "语法",
    "语法糖",
    "运算符",
    "拼写",
    "分号",
    "括号",
    "语句",
    "syntax",
    "operator precedence",
)


def assess_focus(title: str, reason: str = "") -> dict[str, Any]:
    """Explain deterministic ranking signals, not a claim of learning value."""
    text = f"{title} {reason}".casefold()
    stacks = [
        name for name in _STACKS if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", text)
    ]
    facets = [name for name, words in _ARCHITECTURE if any(word in text for word in words)]
    if facets:
        category, priority = "architecture", 3
        rationale = "候选描述包含架构机制、约束或设计取舍；代码保留为证据。"
    elif stacks or any(
        word in text for word in ("技术栈", "框架机制", "深度应用", "适用场景", "方案取舍")
    ):
        category, priority = "technology_stack", 2
        rationale = "候选聚焦技术栈特性或应用，优先于孤立语法细节。"
    elif any(word in text for word in _SYNTAX):
        category, priority = "syntax_detail", 0
        rationale = "主要是局部语法细节，保留历史并降低默认推荐优先级。"
    else:
        category, priority = "needs_context", 1
        rationale = "描述不足以判断架构价值，不自动推断或合并。"
    return {
        "policy_version": POLICY_VERSION,
        "category": category,
        "priority": priority,
        "reason": rationale,
        "stacks": stacks,
        "facets": facets,
    }


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank existing groups, suggesting related facets without merging IDs."""
    result = []
    for candidate in candidates:
        item = dict(candidate)
        item["learning_focus"] = assess_focus(item["title"], item.get("reason", ""))
        item["related_candidate_ids"] = []
        result.append(item)
    for item in result:
        focus = item["learning_focus"]
        if not focus["facets"] or not focus["stacks"]:
            continue
        item["related_candidate_ids"] = [
            other["candidate_id"]
            for other in result
            if other["candidate_id"] != item["candidate_id"]
            and set(focus["stacks"]) & set(other["learning_focus"]["stacks"])
            and set(focus["facets"]) & set(other["learning_focus"]["facets"])
        ]
    return sorted(
        result,
        key=lambda item: (
            -item["learning_focus"]["priority"],
            -item.get("evidence_count", 0),
            item["title"],
        ),
    )
