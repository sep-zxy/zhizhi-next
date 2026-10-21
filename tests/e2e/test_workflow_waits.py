"""Wait outcomes remain resumable and do not disclose arbitrary model content."""

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.workflow import KnowledgeWorkflow, WorkflowSettings
import pytest


@pytest.mark.parametrize("reason", [
    "DERIVATIVE_TEXT_BOUNDS", "DERIVATIVE_SENSITIVE_PATTERN",
    "DERIVATIVE_ASSET_OR_QUOTATION", "DERIVATIVE_UNVERIFIED_LINK",
    "DERIVATIVE_UNSAFE_CITATION", "DERIVATIVE_COPY_ALARM",
    "DERIVATIVE_REVIEW_INCOMPLETE", "PUBLICATION_BLOCKED",
    "private model explanation", {"secret": "must not persist"}, None,
])
def test_publication_block_reason_is_fixed_code_only(tmp_path, reason):
    domain = DomainService(tmp_path / "db.sqlite", tmp_path / "vault")
    user = Actor("owner", "user", "user", frozenset({"*"}))
    workflow = KnowledgeWorkflow(domain, None, WorkflowSettings(owner_id="owner"))
    task = domain.create_task("knowledge.generate", {}, actor=user,
                              idempotency_key="publication-test")
    claimed = domain.claim_task("knowledge-worker", actor=workflow.worker)
    assert workflow._settle_model(claimed, {
        "status": "blocked", "reason": reason,
    }, "generate")
    saved = domain.read_task(task["task_id"], actor=user)
    if isinstance(reason, str) and reason.startswith(("DERIVATIVE_", "PUBLICATION_")):
        assert saved["result"]["publication_block_reason"] == reason
    else:
        assert "publication_block_reason" not in saved["result"]
    assert "private model explanation" not in str(saved)
    assert "must not persist" not in str(saved)


def test_quota_metadata_is_preserved_without_raw_text(tmp_path):
    domain = DomainService(tmp_path / "db.sqlite", tmp_path / "vault")
    user = Actor("owner", "user", "user", frozenset({"*"}))
    workflow = KnowledgeWorkflow(domain, None, WorkflowSettings(owner_id="owner"))
    task = domain.create_task("knowledge.generate", {}, actor=user,
                              idempotency_key="wait-test")
    claimed = domain.claim_task("knowledge-worker", actor=workflow.worker)
    assert claimed["task_id"] == task["task_id"]
    assert workflow._settle_model(claimed, {"status": "waiting_quota", "model": {
        "provider_id": "openai", "retry_after": "2030-01-01T00:00:00+00:00",
        "error_code": "SUBSCRIPTION_QUOTA", "output": "sensitive source text",
        "usage": {"unexpected": "private"}}}, "generate")
    saved = domain.read_task(task["task_id"], actor=user)
    assert saved["state"] == "waiting_quota"
    assert saved["result"]["model"]["retry_after"] == "2030-01-01T00:00:00+00:00"
    assert "sensitive source text" not in str(saved)
    assert "private" not in str(saved)
    assert domain.claim_task("knowledge-worker", actor=workflow.worker) is None
