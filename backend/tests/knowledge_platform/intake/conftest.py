"""Real isolated DomainService with synthetic network/model input only."""

import pytest

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.intake.service import IntakeService


@pytest.fixture
def actors():
    scopes = frozenset({"knowledge:read", "knowledge:write", "tasks:work"})
    return (
        Actor("fixture-owner", "fixture-user", "user", scopes),
        Actor("fixture-owner", "fixture-worker", "service", scopes),
        Actor("fixture-owner", "fixture-agent", "agent", scopes),
    )


@pytest.fixture
def service(tmp_path):
    return IntakeService(DomainService(tmp_path / "domain.sqlite"))
