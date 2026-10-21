"""Pass actual temporary ASGI intake receipts through the unchanged Pi client.

Synthetic in-process domain plus a captured-response fetch adapter, not browser,
network, model or full E2E acceptance. No response bodies or credentials are saved.
The Pi package's locked tsx loader runs tests only; this does not build an artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from starlette.applications import Starlette

from ahadiff.knowledge_platform.auth import Actor
from ahadiff.knowledge_platform.domain import DomainService
from ahadiff.knowledge_platform.intake import IntakeService, create_intake_routes

ROOT = Path(__file__).resolve().parents[4]

PI_CHECK = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const { assertDomainClassification, DomainClient } = await import(pathToFileURL(process.argv[1]).href);
const captures = JSON.parse(readFileSync(0, 'utf8'));
process.env.ZHIZHI_ALLOWED_CLASSIFICATIONS = 'public,personal';
let checks = 0;
for (const capture of captures) {
  const events = [];
  const client = new DomainClient('http://127.0.0.1:8766/', 'synthetic-in-memory-contract-only', false,
    event => events.push(event), async (url, init) => {
      assert.equal(new URL(url).pathname, '/api/knowledge/v1' + capture.path);
      assert.equal(init.method, capture.method);
      assert.equal(init.credentials, 'omit');
      assert.equal(init.redirect, 'error');
      return new Response(JSON.stringify(capture.body), { status: capture.status });
    });
  const operation = client.request('intake.contract', capture.method, capture.path,
    capture.method === 'POST' ? capture.input : undefined,
    capture.method === 'POST' ? 'synthetic-idempotency' : undefined);
  if (capture.expected_error) {
    await assert.rejects(operation, new RegExp(capture.expected_error));
  } else {
    const value = await operation;
    assert.deepEqual(value, capture.body);
    assertDomainClassification(value, ['public', 'personal']);
    assert.ok(Array.isArray(value.classification_labels));
    const missing = { ...value };
    delete missing.data_classification;
    assert.throws(() => assertDomainClassification(missing), /DOMAIN_CLASSIFICATION_REQUIRED/);
  }
  assert.equal(events.length, 1);
  assert.deepEqual(Object.keys(events[0]).sort(),
    ['action', 'event', 'method', 'request_hash', 'status', 'trace_id']);
  checks++;
}
console.log(`Intake ASGI to unchanged Pi client: ${checks} response contracts passed; synthetic, not full E2E.`);
"""


async def capture() -> list[dict]:
    result: list[dict] = []
    actor = Actor(
        "synthetic-intake-contract-owner",
        "synthetic-intake-contract-user",
        "user",
        frozenset({"knowledge:read", "knowledge:write"}),
    )
    with TemporaryDirectory(prefix="intake-pi-contract-") as directory:
        domain = DomainService(Path(directory) / "domain.sqlite")
        try:
            service = IntakeService(domain, input_classification="public")
            app = Starlette(routes=create_intake_routes(domain, intake_service=service))

            async def trusted_context(scope, receive, send):
                scope.setdefault("state", {})["actor"] = actor
                await app(scope, receive, send)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(trusted_context),
                base_url="http://testserver",
            ) as client:
                for classification in (
                    "public",
                    "personal",
                    "employer",
                    "secret",
                    "unknown",
                ):
                    body = {
                        "text": "Synthetic response-contract material only.",
                        "data_classification": classification,
                    }
                    response = await client.post(
                        "/api/knowledge/v1/intakes",
                        json=body,
                        headers={"Idempotency-Key": classification},
                    )
                    assert response.status_code == 202
                    value = response.json()
                    for private_key in (
                        "payload",
                        "owner_id",
                        "metadata",
                        "cache_ref",
                        "object_path",
                    ):
                        assert private_key not in value["data"]
                    expected_error = (
                        "DOMAIN_CLASSIFICATION_REQUIRED"
                        if classification == "unknown"
                        else "DOMAIN_CLASSIFICATION_FORBIDDEN"
                        if classification in {"employer", "secret"}
                        else None
                    )
                    result.append(
                        {
                            "method": "POST",
                            "path": "/intakes",
                            "input": body,
                            "status": response.status_code,
                            "body": value,
                            "expected_error": expected_error,
                        }
                    )
                    path = "/intakes/" + value["data"]["intake_id"]
                    read = await client.get("/api/knowledge/v1" + path)
                    assert read.status_code == 200
                    result.append(
                        {
                            "method": "GET",
                            "path": path,
                            "status": read.status_code,
                            "body": read.json(),
                            "expected_error": expected_error,
                        }
                    )
                mixed = domain.create_intake(
                    {
                        "text": "Synthetic mixed source.",
                        "data_classification": "public",
                        "metadata": {
                            "classification_labels": ["employer", "third_party_private"]
                        },
                    },
                    actor=actor,
                    idempotency_key="mixed",
                )
                path = "/intakes/" + mixed["intake_id"]
                read = await client.get("/api/knowledge/v1" + path)
                assert read.status_code == 200
                assert read.json()["classification_labels"] == [
                    "employer",
                    "public",
                    "third_party_private",
                ]
                result.append(
                    {
                        "method": "GET",
                        "path": path,
                        "status": read.status_code,
                        "body": read.json(),
                        "expected_error": "DOMAIN_CLASSIFICATION_FORBIDDEN",
                    }
                )
        finally:
            domain.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tsx-loader",
        type=Path,
        default=ROOT / "integrations/pi-zhizhi/node_modules/tsx/dist/loader.mjs",
        help="Existing official locked tsx loader; never installs dependencies.",
    )
    args = parser.parse_args()
    if not args.tsx_loader.is_file():
        parser.error("The locked Pi test dependencies must already be installed.")
    subprocess.run(
        [
            "node",
            "--import",
            str(args.tsx_loader.resolve()),
            "--input-type=module",
            "-e",
            PI_CHECK,
            str(ROOT / "integrations/pi-zhizhi/src/client.ts"),
        ],
        input=json.dumps(asyncio.run(capture())),
        text=True,
        check=True,
    )


if __name__ == "__main__":
    main()
