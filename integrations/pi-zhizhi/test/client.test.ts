import assert from "node:assert/strict";
import test from "node:test";
import { assertDomainClassification, DomainClient, safeId, validateBaseUrl, type AuditEvent } from "../src/client.ts";
import { boundedPayload } from "../src/model-guard.ts";

test("domain URL rejects cleartext remote, embedded credentials and alternate paths", () => {
  for (const url of ["http://example.com", "https://user:pass@example.com", "https://example.com/admin"]) {
    assert.throws(() => validateBaseUrl(url));
  }
  assert.equal(validateBaseUrl("http://127.0.0.1:8766").pathname, "/api/knowledge/v1/");
  for (const id of ["../secret", "//evil", "a?token=x", "%2e%2e", "a/b"]) assert.throws(() => safeId(id));
});

test("agent client cannot call keep, approve, shell or arbitrary URLs", async () => {
  let calls = 0;
  const client = new DomainClient("https://knowledge.example", "fixture-token", false, () => {},
    (async () => { calls++; throw new Error("must not request"); }) as typeof fetch);
  for (const path of ["/intakes/x/decision", "/changes/x/decisions", "/bash", "https://evil.example",
                       "/entries/%2e%2e", "/entries/..", "/changes/x/apply"]) {
    await assert.rejects(client.request("attack", "POST", path, {}, "fixture-idem"));
  }
  assert.equal(calls, 0);
});

test("domain request sends fixed auth and idempotency but audits no content or credentials", async () => {
  const events: AuditEvent[] = [];
  let observed: RequestInit | undefined;
  const client = new DomainClient("https://knowledge.example", "fixture-private-token", false,
    (event) => events.push(event), (async (url, init) => {
      assert.equal(String(url), "https://knowledge.example/api/knowledge/v1/intakes");
      observed = init;
      return Response.json({ data: { intake_id: "in-1" }, trace_id: "trace-1", task_id: "task-1", data_classification: "personal" });
    }) as typeof fetch);
  const result = await client.request("intake", "POST", "/intakes", { text: "private source text" }, "idem-1");
  assert.equal(result.data.intake_id, "in-1");
  assert.equal((observed?.headers as Record<string, string>)["Idempotency-Key"], "idem-1");
  assert.equal(observed?.redirect, "error");
  assert.equal(events.length, 1);
  assert.ok(!JSON.stringify(events).includes("private"));
});

test("unknown or nested restricted classification never enters the model context", () => {
  assert.throws(() => assertDomainClassification({ data: {} }), /CLASSIFICATION_REQUIRED/);
  assert.throws(() => assertDomainClassification({ data_classification: "unknown", data: {} }), /CLASSIFICATION_REQUIRED/);
  assert.throws(() => assertDomainClassification({ data_classification: "public", data: {
    results: [{ classification: "employer", text: "not public" }],
  } }), /CLASSIFICATION_FORBIDDEN/);
  assert.throws(() => assertDomainClassification({ data_classification: "public", data: {
    metadata: { classification: "secret" },
  } }, ["public", "personal", "employer"]), /CLASSIFICATION_FORBIDDEN/);
  assert.doesNotThrow(() => assertDomainClassification({ data_classification: "employer", data: {} }, ["employer"]));
  assert.throws(() => assertDomainClassification({ data_classification: "employer", data: {
    classification_labels: ["employer", "third_party_private"],
  } }, ["employer"]), /CLASSIFICATION_FORBIDDEN/);
  assert.throws(() => assertDomainClassification({ data_classification: "personal", data: {
    access_token: "fixture-never-real-sensitive-value",
  } }), /SECRET_CONTENT_FORBIDDEN/);
});

test("redirects and non-envelope replies cannot be treated as success", async () => {
  const client = new DomainClient("https://knowledge.example", "fixture-token", false, () => {},
    (async () => Response.json({ arbitrary: true })) as typeof fetch);
  await assert.rejects(client.request("read", "GET", "/tasks/x"), /DOMAIN_ENVELOPE_INVALID/);
});

test("relation routes are bounded read-only filters and preserve graph fact labels", async () => {
  const calls: string[] = [];
  const graph = { inference: "none", nodes: [], edges: [{ kind: "candidate", is_fact: false }] };
  const client = new DomainClient("https://knowledge.example", "fixture-token", false, () => {},
    (async (url) => {
      calls.push(String(url));
      return Response.json({ data: graph, task_id: null, trace_id: "fixture", data_classification: "public" });
    }) as typeof fetch);
  const path = "/relations/entry-1?kind=confirmed&kind=candidate&direction=both&visibility=public&limit=100";
  const result = await client.request("relations.read", "GET", path);
  assert.deepEqual(result.data, graph);
  assert.ok(calls[0].endsWith(path));
  await client.request("relations.traverse", "GET", "/relations/entry-1/traverse?max_depth=3&max_nodes=200");
  for (const invalid of [
    "/relations/entry-1?limit=101", "/relations/entry-1?limit=0", "/relations/entry-1?limit=1e2",
    "/relations/entry-1?limit=1&limit=2", "/relations/entry-1?kind=fact",
    "/relations/entry-1?kind=candidate&kind=candidate", "/relations/entry-1?direction=any",
    "/relations/entry-1?visibility=secret", "/relations/entry-1?owner_id=other",
    "/relations/entry-1/traverse?max_depth=4", "/relations/entry-1/traverse?max_nodes=201",
    "/relations/entry-1/traverse?limit=1", "/relations/entry-1?max_depth=1",
  ]) await assert.rejects(client.request("relations.read", "GET", invalid), /DOMAIN_ROUTE_FORBIDDEN/);
  await assert.rejects(client.request("relations.write", "POST", "/relations/entry-1", {}, "fixture"), /DOMAIN_ROUTE_FORBIDDEN/);
  assert.equal(calls.length, 2);
});

test("relation graph cannot wash nested target classification through a public envelope", async () => {
  const client = new DomainClient("https://knowledge.example", "fixture-token", false, () => {},
    (async () => Response.json({ data: { nodes: [{ classification_labels: ["public", "employer"] }] },
      data_classification: "public", task_id: null, trace_id: "fixture" })) as typeof fetch);
  await assert.rejects(client.request("relations.read", "GET", "/relations/entry-1"), /CLASSIFICATION_FORBIDDEN/);
});

test("request bounds do not enable unsupported background or server history", () => {
  const payload = boundedPayload({ background: true, previous_response_id: "x", store: true }, "openai", 100) as Record<string, unknown>;
  assert.equal(payload.max_output_tokens, 100);
  assert.equal(payload.store, false);
  assert.equal(payload.stream, true);
  assert.ok(!("background" in payload));
  assert.ok(!("previous_response_id" in payload));
  assert.throws(() => boundedPayload({}, "openai", 100000));
});

test("resource IDs reject a trailing newline rather than relying on dollar anchors", () => {
  for (const value of ["entry-1\n", "entry-1\r", "entry-1\r\n"]) assert.throws(() => safeId(value), /INVALID_RESOURCE_ID/);
  assert.equal(safeId("entry-1"), "entry-1");
});
