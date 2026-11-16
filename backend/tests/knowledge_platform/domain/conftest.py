"""Isolated, real SQLite/Git fixtures; never a live-model acceptance substitute."""

from __future__ import annotations

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService


@pytest.fixture
def owner():
    return Actor("owner-one", "human-one", "user", frozenset({"*"}))


@pytest.fixture
def generator():
    return Actor(
        "owner-one", "generator", "agent", frozenset({"knowledge:read", "knowledge:write"})
    )


@pytest.fixture
def reviewer():
    return Actor(
        "owner-one",
        "independent-reviewer",
        "agent",
        frozenset({"knowledge:read", "knowledge:review"}),
    )


@pytest.fixture
def writer():
    return Actor("owner-one", "writer", "service", frozenset({"*"}))


@pytest.fixture
def domain(tmp_path):
    service = DomainService(tmp_path / "platform.db")
    yield service
    service.close()


@pytest.fixture
def source(domain, owner):
    intake = domain.create_intake(
        {"text": "Evidence says addition preserves history.", "data_classification": "public"},
        actor=owner,
        idempotency_key="source-intake",
    )
    preview = domain.update_intake(
        intake["intake_id"],
        {"state": "preview_ready", "preview": {"summary": "Evidence"}},
        actor=owner,
        idempotency_key="source-preview",
    )
    return domain.retain_source(
        intake["intake_id"],
        {
            "raw_text": "Evidence says addition preserves history.",
            "preview_revision": preview["preview_revision"],
            "rights": "summary_allowed",
        },
        actor=owner,
        idempotency_key="source-keep",
    )


@pytest.fixture
def proposal(domain, generator, source):
    return domain.propose_change(
        {
            "title": "Durable evidence",
            "body": "Evidence says addition preserves history.\n",
            "evidence_refs": [{"source_id": source["source_id"]}],
        },
        actor=generator,
        idempotency_key="proposal",
    )
