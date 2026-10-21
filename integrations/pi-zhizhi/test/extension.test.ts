import assert from "node:assert/strict";
import test from "node:test";
import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { normalizeContext, type Provider, type Model } from "@earendil-works/pi-ai";
import extension, { decisionFields, safeDecisionDisplay } from "../src/index.ts";
import { guardProvider } from "../src/subscription-policy.ts";

test("human decision display makes terminal escape and bidi evidence controls visible", () => {
  assert.equal(safeDecisionDisplay("旧文\n\t原文"), "旧文\n\t原文");
  assert.equal(safeDecisionDisplay("\u001b[2Jfake\r\u202e批准\u2069"),
    "\\u001b[2Jfake\\u000d\\u202e批准\\u2069");
});

test("human approval includes metadata and publication fields, not only the body", () => {
  const view = JSON.parse(decisionFields({
    title: "架构边界", visibility: "private", publication_intent: "public",
    publication_eligible: false, metadata: { open_questions: ["哪些边界尚未验证？"] },
    evidence_refs: [{ source_id: "source-1" }],
  }));
  assert.equal(view.visibility, "private");
  assert.equal(view.publication_intent, "public");
  assert.equal(view.publication_eligible, false);
  assert.deepEqual(view.metadata.open_questions, ["哪些边界尚未验证？"]);
  assert.equal(JSON.parse(decisionFields(null)), null);
});

test("only domain tools are exposed; human decision commands reject RPC invocation", async () => {
  const tools: Array<{ name: string }> = [];
  const commands = new Map<string, { handler: (args: string, ctx: ExtensionCommandContext) => Promise<void> }>();
  const events = new Map<string, Array<(...args: unknown[]) => unknown>>();
  extension({
    registerTool: (tool: { name: string }) => tools.push(tool),
    registerCommand: (name: string, command: { handler: (args: string, ctx: ExtensionCommandContext) => Promise<void> }) => commands.set(name, command),
    on: (name: string, handler: (...args: unknown[]) => unknown) => events.set(name, [...events.get(name) ?? [], handler]),
  } as unknown as ExtensionAPI);
  assert.ok(tools.some((tool) => tool.name === "zhizhi_intake"));
  assert.ok(tools.some((tool) => tool.name === "zhizhi_relations"));
  assert.ok(tools.some((tool) => tool.name === "zhizhi_traverse_relations"));
  assert.ok(!tools.some((tool) => /keep|discard|approve|decision|bash|write/.test(tool.name)));
  const hook = events.get("tool_call")![0];
  for (const toolName of ["bash", "write", "codemode", "mcp__evil", "zhizhi_submit_user_decision"]) {
    assert.deepEqual(hook({ toolName }), { block: true, reason: "Only audited Zhizhi domain tools are permitted" });
  }
  const ctx = { mode: "rpc", hasUI: true } as ExtensionCommandContext;
  await assert.rejects(commands.get("zhizhi-review")!.handler("change-1", ctx), /REQUIRES_LOCAL_TUI/);
  await assert.rejects(commands.get("zhizhi-keep")!.handler("intake-1", ctx), /REQUIRES_LOCAL_TUI/);
});

test("relation tools serialize repeated kinds and preserve non-inference evidence", async () => {
  type Tool = { name: string; execute: (id: string, params: Record<string, unknown>) => Promise<{ details: unknown }> };
  const tools = new Map<string, Tool>();
  const previous = { token: process.env.ZHIZHI_AGENT_TOKEN, url: process.env.ZHIZHI_API_URL, fetch: globalThis.fetch };
  const calls: URL[] = [];
  const data = { inference: "none", edges: [{ kind: "navigation", is_fact: false }] };
  try {
    process.env.ZHIZHI_AGENT_TOKEN = "fixture-only-token";
    process.env.ZHIZHI_API_URL = "http://127.0.0.1:8766";
    globalThis.fetch = (async (url) => {
      calls.push(new URL(String(url)));
      return Response.json({ data, data_classification: "public", task_id: null, trace_id: "fixture" });
    }) as typeof fetch;
    extension({ registerTool: (tool: Tool) => tools.set(tool.name, tool), registerCommand: () => {}, on: () => {} } as unknown as ExtensionAPI);
    const result = await tools.get("zhizhi_relations")!.execute("tool-1", {
      entry_id: "entry:1", kinds: ["confirmed", "candidate"], direction: "in", visibility: "public", limit: 20,
    });
    assert.deepEqual((result.details as { data: unknown }).data, data);
    assert.equal(calls[0].pathname, "/api/knowledge/v1/relations/entry%3A1");
    assert.deepEqual(calls[0].searchParams.getAll("kind"), ["confirmed", "candidate"]);
    assert.equal(calls[0].searchParams.get("direction"), "in");
    assert.equal(calls[0].searchParams.get("limit"), "20");
    await tools.get("zhizhi_traverse_relations")!.execute("tool-2", { entry_id: "entry-1" });
    assert.equal(calls[1].searchParams.get("max_depth"), "2");
    assert.equal(calls[1].searchParams.get("max_nodes"), "100");
    assert.equal(calls[1].searchParams.get("visibility"), "all");
    await assert.rejects(tools.get("zhizhi_relations")!.execute("tool-3", { entry_id: "../other" }), /INVALID_RESOURCE_ID/);
    assert.equal(calls.length, 2);
  } finally {
    globalThis.fetch = previous.fetch;
    if (previous.token === undefined) delete process.env.ZHIZHI_AGENT_TOKEN;
    else process.env.ZHIZHI_AGENT_TOKEN = previous.token;
    if (previous.url === undefined) delete process.env.ZHIZHI_API_URL;
    else process.env.ZHIZHI_API_URL = previous.url;
  }
});

test("native provider gate fails closed before any upstream call, independently of event hooks", async () => {
  let upstreamCalls = 0;
  const original = { id: "openai", name: "fixture", auth: {}, getModels: () => [],
    stream: () => { upstreamCalls++; throw new Error("must not be called"); },
    streamSimple: () => { upstreamCalls++; throw new Error("must not be called"); },
  } as unknown as Provider;
  const guarded = guardProvider(original, async () => { throw new Error("METERED_AUTH_FORBIDDEN"); });
  const model = { id: "fixture", provider: "openai", api: "openai-responses" } as Model<"openai-responses">;
  const result = await guarded.streamSimple(model, normalizeContext({ messages: [] })).result();
  assert.equal(result.stopReason, "error");
  assert.match(result.errorMessage ?? "", /METERED_AUTH_FORBIDDEN/);
  assert.equal(upstreamCalls, 0);
});

test("read-only bridge slot does not advertise human approval commands without an independent human identity", async () => {
  const { CodexBridgeProvider } = await import('../src/codex-provider.ts');
  const prior = process.env.ZHIZHI_USER_TOKEN; delete process.env.ZHIZHI_USER_TOKEN;
  const commands: string[] = [];
  try {
    extension({registerTool() {}, registerProvider() {}, on() {}, registerCommand: (name: string) => commands.push(name)} as unknown as ExtensionAPI,
      {bridge: tools => new CodexBridgeProvider(tools, () => {throw new Error('NO_PROCESS_EXPECTED');})});
    assert.deepEqual(commands, []);
  } finally {if (prior === undefined) delete process.env.ZHIZHI_USER_TOKEN; else process.env.ZHIZHI_USER_TOKEN = prior;}
});
