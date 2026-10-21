"""Capture real ASGI/domain responses using synthetic data and temporary SQLite/Git.

No listener, live account, external fetch, model, worker, or public publication.
Run through uv with the integrated growth source and its locked test dependencies.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from ahadiff.knowledge_platform.app import create_application
from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService


async def main() -> None:
    captures: dict[str, dict] = {}
    with TemporaryDirectory(prefix="wiki-private-contract-") as directory:
        root = Path(directory)
        owner = Actor("isolated-owner", "isolated-user", "user", frozenset({"*"}))
        generator = Actor(
            owner.owner_id, "isolated-generator", "agent",
            frozenset({"knowledge:read", "knowledge:write"}),
        )
        writer = Actor(owner.owner_id, "isolated-writer", "service", frozenset({"*"}))
        domain = DomainService(root / "platform.db")
        try:
            evidence = "Evidence says addition preserves history."
            intake = domain.create_intake(
                {"text": evidence, "data_classification": "public"},
                actor=owner, idempotency_key="fixture-source",
            )
            preview = domain.update_intake(
                intake["intake_id"],
                {"state": "preview_ready", "preview": {"summary": "Isolated evidence"}},
                actor=owner, idempotency_key="fixture-preview",
            )
            source = domain.retain_source(
                intake["intake_id"],
                {"raw_text": evidence, "preview_revision": preview["preview_revision"],
                 "rights": "summary_allowed"},
                actor=owner, idempotency_key="fixture-keep",
            )
            proposal = domain.propose_change(
                {"title": "Isolated durable evidence", "body": evidence + "\n",
                 "evidence_refs": [{"source_id": source["source_id"]}]},
                actor=generator, idempotency_key="fixture-proposal",
            )
            committed = domain.apply_change(
                proposal["change_id"], actor=writer, idempotency_key="fixture-apply",
            )
            entry_id = committed["entry_id"]
            major = domain.propose_change(
                {"entry_id": entry_id, "base_revision": committed["revision"],
                 "title": "Isolated durable evidence", "body": "# Isolated reference\n\n" + evidence + "\n",
                 "operation": "update", "risk_flags": ["core_conclusion"],
                 "evidence_refs": [{"source_id": source["source_id"]}]},
                actor=generator, idempotency_key="fixture-major",
            )
            task = domain.create_task(
                "knowledge.generate",
                {"data_classification": "personal", "raw_fixture": "never display"},
                actor=owner, idempotency_key="fixture-task",
            )
            domain.update_task(
                task["task_id"],
                {"state": "waiting_auth", "error": {"code": "AUTH_REQUIRED", "detail": "never display"}},
                actor=owner, idempotency_key="fixture-task-wait",
            )
            # An inert fixture credential, never persisted or sent over a network.
            fixture_token = "isolated-test-only-credential-no-real-account"
            app = create_application(root, tokens={fixture_token: owner}, domain=domain)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://asgi.test") as client:
                unauthenticated = await client.get("/api/knowledge/v1/entries?limit=100&offset=0")
                assert unauthenticated.status_code == 401
                captures["unauthenticated"] = {
                    "status": unauthenticated.status_code, "body": unauthenticated.json(),
                }
                client.headers["Authorization"] = "Bearer " + fixture_token
                paths = {
                    "entries": "/api/knowledge/v1/entries?limit=100&offset=0",
                    "entry": f"/api/knowledge/v1/entries/{entry_id}",
                    "history": f"/api/knowledge/v1/entries/{entry_id}?revision=1",
                    "search": "/api/knowledge/v1/search?q=Evidence&scope=wiki&limit=100",
                    "tasks": "/api/knowledge/v1/tasks?limit=100&offset=0",
                    "task": f"/api/knowledge/v1/tasks/{task['task_id']}",
                    "events": f"/api/knowledge/v1/tasks/{task['task_id']}/events?after=0&limit=100",
                    "intakes": "/api/knowledge/v1/intakes?limit=100&offset=0",
                    "intake": f"/api/knowledge/v1/intakes/{intake['intake_id']}",
                    "reviews": "/api/knowledge/v1/reviews?state=needs_user&limit=100",
                    "change": f"/api/knowledge/v1/changes/{major['change_id']}",
                    "challenge": f"/api/knowledge/v1/changes/{major['change_id']}/decision-challenge",
                    "capabilities": "/api/knowledge/v1/intakes/capabilities",
                    "health": "/health",
                }
                for name, path in paths.items():
                    response = await client.get(path)
                    assert response.status_code == 200, (name, response.status_code, response.json())
                    captures[name] = {"status": response.status_code, "body": response.json()}
                assert "lease_token" not in captures["task"]["body"]["data"]
                assert "payload" not in captures["task"]["body"]["data"]
                assert captures["task"]["body"]["data"]["state"] == "waiting_auth"
                assert captures["tasks"]["body"]["data"]["has_more"] is False
                assert len(captures["reviews"]["body"]["data"]) == 1
        finally:
            domain.close()
    target = Path("test-results/domain-contract.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Captured {len(captures)} real ASGI responses; synthetic data, no live E2E.")


if __name__ == "__main__":
    asyncio.run(main())
