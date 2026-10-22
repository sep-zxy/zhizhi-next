"""Synthetic P16/P17 fixture, using the real public projection. Never user knowledge."""
import argparse
import json
from pathlib import Path
from ahadiff.knowledge_platform.publish.projection import build_projection

parser = argparse.ArgumentParser()
parser.add_argument("--staging", type=Path, required=True)
parser.add_argument("--variant", choices=["A", "B"], default="A")
args = parser.parse_args()
files = {}
items = [
    ("sqlite-cas", "SQLite 单写与 Git CAS 协作边界", "核心机制"),
    ("transaction", "事务与一致性边界", "核心机制"),
    ("modularity", "模块化设计与依赖边界", "架构设计"),
    ("events", "事件驱动与任务编排", "架构设计"),
    ("sqlite", "SQLite 的工程取舍", "技术栈"),
    ("typescript", "TypeScript 类型边界", "技术栈"),
    ("recovery", "任务恢复与幂等", "工程实践"),
    ("testing", "测试分层与证据", "工程实践"),
]
for slug, title, tag in items:
    body = f"""---
title: '{title}（测试条目）'
publish: true
publication_eligible: true
created: '2026-10-19'
updated: '2026-10-20'
tags: ['{tag}']
summary: '隔离测试内容，用于验证 Markdown 自动投影与公开页面，不是用户知识。'
sources: ['sqlite-doc']
---

> 测试数据：仅供 P16/P17 布局和产物验证，不代表真实个人知识库。

## 1. 各自保证什么

本条目是可复现的测试内容。SQLite 事务边界与 Git 提交边界应分别验证。

## 2. 失败与恢复

1. 先确认持久化状态。
2. 再决定重试或恢复。
3. 不把测试数据成功当作真实账号端到端通过。

## 3. 取舍与适用范围

架构设计、技术栈特性和工程约束是知识组织的重点。

[关联测试条目](/{'transaction' if slug == 'sqlite-cas' else 'sqlite-cas'})
"""
    files[slug + ".md"] = body.replace("测试条目", "测试条目 " + args.variant).encode()
files["private.md"] = b"---\ntitle: PRIVATE_TITLE_SENTINEL\nvisibility: private\n---\nPRIVATE_ORIGINAL_SENTINEL"
files["ineligible.md"] = b"---\ntitle: INELIGIBLE_TITLE_SENTINEL\npublish: true\npublication_eligible: false\n---\nPRIVATE_TOOL_PERMISSION_SENTINEL"
files["canonical-unreviewed.md"] = b"---\ntitle: UNREVIEWED_TITLE_SENTINEL\nschema_version: 6\nvisibility: public\npublication_eligible: false\nowner_id: test\nentry_id: test\n---\nPRIVATE_ACCOUNT_SENTINEL"
policy = {"sources": {"sqlite-doc": {"url": "https://www.sqlite.org/transactional.html", "title": "SQLite 官方事务说明（测试引用）"}}, "assets": {}}
result = build_projection(files, args.staging, revision="0" * 40, policy=policy, policy_version="test", template_version="public-portal-v1")
public = b"\n".join(p.read_bytes() for p in args.staging.rglob("*") if p.is_file())
assert b"SENTINEL" not in public
assert len(result["public_manifest"]["pages"]) == 8
print(json.dumps({"kind": "synthetic-fixture-not-live-e2e", "public_pages": 8, "private_fingerprints_absent": True}, ensure_ascii=False))
