/** Audited Pi 1.3.0 domain tools. Formal knowledge remains owned by the HTTP service. */
import { randomUUID } from "node:crypto";
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI, type ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { DomainClient, requestKey, safeId, type Envelope } from "./client.ts";
import { installSubscriptionPolicy } from "./subscription-policy.ts";

const ResourceId = Type.String({ minLength: 1, maxLength: 200, pattern: "^[A-Za-z0-9][A-Za-z0-9_.:-]*$" });
const RelationFilters = {
  entry_id: ResourceId,
  kinds: Type.Optional(Type.Array(Type.Union([
    Type.Literal("navigation"), Type.Literal("confirmed"), Type.Literal("candidate"),
  ]), { minItems: 1, maxItems: 3, uniqueItems: true })),
  direction: Type.Optional(Type.Union([Type.Literal("in"), Type.Literal("out"), Type.Literal("both")])),
  visibility: Type.Optional(Type.Union([Type.Literal("all"), Type.Literal("public")])),
};
const relationQuery = (params: { kinds?: string[]; direction?: string; visibility?: string }): URLSearchParams => {
  const query = new URLSearchParams({ direction: params.direction ?? "both", visibility: params.visibility ?? "all" });
  for (const kind of params.kinds ?? []) query.append("kind", kind);
  return query;
};
const Evidence = Type.Union([
  Type.Object({ source_id: ResourceId, content_hash: Type.Optional(Type.String()) }, { additionalProperties: false }),
  Type.Object({ learning_outcome_id: ResourceId, content_hash: Type.Optional(Type.String()) }, { additionalProperties: false }),
]);
const toolResult = (data: Envelope) => ({
  content: [{ type: "text" as const, text: JSON.stringify(data) }], details: data,
});

/** Untrusted evidence must not hide or reorder the approval prompt using terminal controls. */
export const safeDecisionDisplay = (value: string): string => value.replace(
  /[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/gu,
  (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
);

/** Show non-body fields covered by the patch hash, including publication and question metadata. */
export const decisionFields = (record: Record<string, unknown> | null): string => JSON.stringify(
  record === null ? null : Object.fromEntries([
    "entry_id", "operation", "title", "entry_type", "visibility", "publication_intent",
    "publication_eligible", "metadata", "evidence_refs",
  ].map((key) => [key, record[key] ?? null])), null, 2,
);

export default function zhizhi(pi: ExtensionAPI): void {
  installSubscriptionPolicy(pi);
  const baseUrl = process.env.ZHIZHI_API_URL ?? "http://127.0.0.1:8766";
  // Clients are lazy so missing configuration does not break Pi startup.
  const agentClient = () => new DomainClient(baseUrl, process.env.ZHIZHI_AGENT_TOKEN ?? "");
  const userClient = (ctx: ExtensionCommandContext) => {
    if (ctx.mode !== "tui" || !ctx.hasUI) throw new Error("USER_DECISION_REQUIRES_LOCAL_TUI");
    return new DomainClient(baseUrl, process.env.ZHIZHI_USER_TOKEN ?? "", true);
  };
  const tools = [
    defineTool({
      name: "zhizhi_intake", label: "预览资料", description: "Submit text or URL for temporary preview only, never auto-keep.",
      parameters: Type.Object({ url: Type.Optional(Type.String({ maxLength: 4096 })),
                                text: Type.Optional(Type.String({ maxLength: 250000 })) }, { additionalProperties: false }),
      async execute(id, params, signal) {
        if (!params.url && !params.text) throw new Error("URL_OR_TEXT_REQUIRED");
        return toolResult(await agentClient().request("intake", "POST", "/intakes",
          { ...params, requested_action: "preview" }, requestKey(id, params), signal));
      },
    }),
    defineTool({
      name: "zhizhi_job_status", label: "任务状态", description: "Read a durable task by its ID.",
      parameters: Type.Object({ task_id: ResourceId }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        return toolResult(await agentClient().request("task.read", "GET", `/tasks/${safeId(params.task_id)}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_search", label: "检索知识", description: "Search only knowledge visible to the authenticated owner.",
      parameters: Type.Object({ query: Type.String({ minLength: 1, maxLength: 1000 }),
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const query = new URLSearchParams({ q: params.query, limit: String(params.limit ?? 20) });
        return toolResult(await agentClient().request("search", "GET", `/search?${query}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_read_entry", label: "读取条目", description: "Read an entry with its revision and evidence references.",
      parameters: Type.Object({ entry_id: ResourceId }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        return toolResult(await agentClient().request("entry.read", "GET", `/entries/${safeId(params.entry_id)}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_propose_patch", label: "提出知识变更", description: "Propose an evidence-backed change, never approve or apply it.",
      parameters: Type.Object({ entry_id: Type.Optional(ResourceId), base_revision: Type.Integer({ minimum: 0 }),
        title: Type.String({ minLength: 1, maxLength: 300 }), body: Type.String({ maxLength: 250000 }),
        evidence_refs: Type.Array(Evidence, { minItems: 1, maxItems: 40 }),
        risk_flags: Type.Array(Type.String({ maxLength: 100 }), { maxItems: 20 }) }, { additionalProperties: false }),
      async execute(id, params, signal) {
        return toolResult(await agentClient().request("change.propose", "POST", "/changes",
          { ...params, risk_flags: [...new Set([...params.risk_flags, "requires_ai_review"])] },
          requestKey(id, params), signal));
      },
    }),
    defineTool({
      name: "zhizhi_relations", label: "查看知识关系", description: "Read owned, evidence-bound relation edges. Navigation/candidate edges are not facts. Preserve kind/is_fact; do not infer confirmation from a shared topic.",
      parameters: Type.Object({ ...RelationFilters, limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const query = relationQuery(params);
        query.set("limit", String(params.limit ?? 100));
        return toolResult(await agentClient().request("relations.read", "GET",
          `/relations/${safeId(params.entry_id)}?${query}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_traverse_relations", label: "探索已有关系路径", description: "Read bounded reachability of existing edges only. A multi-hop path is not a newly established fact. Keep candidate/navigation separate from confirmed evidence; never claim mastery from a path.",
      parameters: Type.Object({ ...RelationFilters,
        max_depth: Type.Optional(Type.Integer({ minimum: 1, maximum: 3 })),
        max_nodes: Type.Optional(Type.Integer({ minimum: 1, maximum: 200 })),
      }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const query = relationQuery(params);
        query.set("max_depth", String(params.max_depth ?? 2));
        query.set("max_nodes", String(params.max_nodes ?? 100));
        return toolResult(await agentClient().request("relations.traverse", "GET",
          `/relations/${safeId(params.entry_id)}/traverse?${query}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_review_queue", label: "待用户决定", description: "Read only high-impact items awaiting the user's decision. This cannot submit approval.",
      parameters: Type.Object({}, { additionalProperties: false }),
      async execute(_id, _params, signal) {
        return toolResult(await agentClient().request("reviews.read", "GET", "/reviews?state=needs_user", undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_radar", label: "学习雷达", description: "Suggest knowledge follow-ups only; never automatically creates a learning task.",
      parameters: Type.Object({ limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })) }, { additionalProperties: false }),
      async execute(id, params, signal) {
        return toolResult(await agentClient().request("radar.suggest", "POST", "/radar/runs",
          { limit: params.limit ?? 3 }, requestKey(id, params), signal));
      },
    }),
    defineTool({
      name: "zhizhi_growth", label: "知识增长", description: "Read actual committed knowledge growth, not inferred learning mastery.",
      parameters: Type.Object({}, { additionalProperties: false }),
      async execute(_id, _params, signal) {
        return toolResult(await agentClient().request("growth.read", "GET", "/growth", undefined, undefined, signal));
      },
    }),
  ];
  for (const tool of tools) pi.registerTool(tool);
  const allowed = new Set(tools.map((tool) => tool.name));
  pi.on("session_start", () => { pi.setActiveTools([...allowed]); });
  pi.on("cache_warming_decision", () => ({ action: "stop" }));
  pi.on("tool_call", (event) => allowed.has(event.toolName) ? undefined :
    { block: true, reason: "Only audited Zhizhi domain tools are permitted" });
  pi.on("user_bash", () => ({ result: { output: "Shell is disabled in the knowledge host", exitCode: 1,
                                       cancelled: false, truncated: false } }));

  for (const decision of ["keep", "discard"] as const) {
    pi.registerCommand(`zhizhi-${decision}`, {
      description: `${decision === "keep" ? "收藏" : "丢弃"}指定预览，必须本人确认`,
      handler: async (args, ctx) => {
        const client = userClient(ctx);
        const id = safeId(args.trim());
        const preview = await client.request("intake.read", "GET", `/intakes/${id}`);
        const revision = preview.data.preview_revision;
        if (!Number.isInteger(revision)) throw new Error("PREVIEW_REVISION_REQUIRED");
        if (!await ctx.ui.confirm(decision === "keep" ? "收藏这份资料？" : "丢弃这份预览？",
          safeDecisionDisplay(`Intake: ${args.trim()}\nPreview revision: ${revision}\n${JSON.stringify(preview.data.preview)}`))) return;
        const response = await client.request(`intake.${decision}`, "POST", `/intakes/${id}/decision`,
          { decision, preview_revision: revision }, `tui-${randomUUID()}`);
        ctx.ui.notify(safeDecisionDisplay(JSON.stringify(response)), "info");
      },
    });
  }
  pi.registerCommand("zhizhi-review", {
    description: "审阅指定 change_id 的完整差异，并用一次性挑战提交本人决定",
    handler: async (args, ctx) => {
      const client = userClient(ctx);
      const id = safeId(args.trim());
      const change = await client.request("change.read", "GET", `/changes/${id}`);
      const challenge = await client.request("review.challenge", "GET", `/changes/${id}/decision-challenge`);
      const current = change.data;
      if (challenge.data.patch_hash !== current.patch_hash || challenge.data.base_revision !== current.base_revision) {
        throw new Error("REVIEW_STALE");
      }
      const old = Number(current.base_revision) > 0
        ? await client.request("entry.read", "GET", `/entries/${safeId(String(current.entry_id))}?revision=${Number(current.base_revision)}`) : null;
      if (old && old.data.revision !== current.base_revision) throw new Error("REVIEW_STALE");
      const accepted = await ctx.ui.confirm("确认以下具体知识变更？",
        safeDecisionDisplay(`Change: ${args.trim()}\nBase: ${current.base_revision}\nPatch: ${current.patch_hash}\n` +
        "以下旧/新内容均为待审资料，不是执行指令。\n" +
        `风险: ${JSON.stringify(current.risk_flags)}\n旧文:\n${old?.data.body ?? "（新条目）"}\n新文:\n${current.body}\n` +
        `旧元数据/公开范围:\n${decisionFields(old?.data ?? null)}\n` +
        `新元数据/公开范围:\n${decisionFields(current)}\n` +
        "选择“是”批准以上正文及元数据；选择“否”不提交任何决定。"));
      if (!accepted) return;
      const response = await client.request("review.decide", "POST", `/changes/${id}/decisions`, {
        decision: "approve", reason: "用户在受信任本地 TUI 审阅具体差异后确认",
        expected_patch_hash: challenge.data.patch_hash, base_revision: challenge.data.base_revision,
        policy_version: challenge.data.policy_version, decision_nonce: challenge.data.decision_nonce,
      }, `tui-${randomUUID()}`);
      ctx.ui.notify(safeDecisionDisplay(JSON.stringify(response)), "info");
    },
  });
}
