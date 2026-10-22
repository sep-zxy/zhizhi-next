/** Audited Pi 1.3.0 domain tools. Formal knowledge remains owned by the HTTP service. */
import { randomUUID } from "node:crypto";
import { installLearningCommands } from "./learning-commands.ts";
import { capabilitySummary } from "./capabilities.ts";
import { Type, type Tool } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI, type ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { DomainClient, requestKey, safeId, type Envelope } from "./client.ts";
import { CodexBridgeProvider } from "./codex-provider.ts";
import { spawnPackagedBridge } from "./codex-bridge-transport.ts";
import { BRIDGE_PROVIDER } from "./codex-bridge-protocol.ts";
import { installCodexProvider } from "./codex-extension.ts";
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

export default function zhizhi(pi: ExtensionAPI, dependencies?: { bridge: (tools: Tool[]) => CodexBridgeProvider }): void {
  const useBridge = !!dependencies || process.env.ZHIZHI_MODEL_PROVIDER === BRIDGE_PROVIDER;
  if (!useBridge) installSubscriptionPolicy(pi);
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
      name: "zhizhi_inbox", label: "查看收件箱", description: "List owned temporary intakes, never retain or discard them.",
      parameters: Type.Object({ state: Type.Optional(Type.String({ minLength: 1, maxLength: 80, pattern: "^[a-z_]+$" })),
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
        offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 1000000 })) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const q = new URLSearchParams({ limit: String(params.limit ?? 20), offset: String(params.offset ?? 0) });
        if (params.state) q.set("state", params.state);
        return toolResult(await agentClient().request("intake.list", "GET", `/intakes?${q}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_intake_preview", label: "读取临时预览", description: "Read actual preview revision, completeness and expiry. Does not keep the source.",
      parameters: Type.Object({ intake_id: ResourceId }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        return toolResult(await agentClient().request("intake.read", "GET", `/intakes/${safeId(params.intake_id)}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_tasks", label: "任务列表", description: "List owned durable tasks with real states; no resume, cancellation or model execution.",
      parameters: Type.Object({ state: Type.Optional(Type.String({ minLength: 1, maxLength: 80, pattern: "^[a-z_]+$" })),
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
        offset: Type.Optional(Type.Integer({ minimum: 0, maximum: 1000000 })) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const q = new URLSearchParams({ limit: String(params.limit ?? 20), offset: String(params.offset ?? 0) });
        if (params.state) q.set("state", params.state);
        return toolResult(await agentClient().request("task.list", "GET", `/tasks?${q}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_task_events", label: "任务事件", description: "Read sanitized task events after a cursor; do not infer success from logs alone.",
      parameters: Type.Object({ task_id: ResourceId,
        after: Type.Optional(Type.Integer({ minimum: 0, maximum: 1000000 })),
        limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const q = new URLSearchParams({ after: String(params.after ?? 0), limit: String(params.limit ?? 100) });
        return toolResult(await agentClient().request("task.events", "GET", `/tasks/${safeId(params.task_id)}/events?${q}`, undefined, undefined, signal));
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
      name: "zhizhi_source", label: "来源元数据", description: "Read owned retained source metadata, not raw delivery envelopes or arbitrary filesystem paths.",
      parameters: Type.Object({ source_id: ResourceId }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        return toolResult(await agentClient().request("source.read", "GET", `/sources/${safeId(params.source_id)}`, undefined, undefined, signal));
      },
    }),
    defineTool({
      name: "zhizhi_evidence", label: "精确证据引用", description: "Read immutable authorized evidence bound to its content hash. Locator is an annotation, not a server-verified excerpt or independent fact.",
      parameters: Type.Object({ reference: Type.Union([
        Type.Object({ source_id: ResourceId, source_revision: Type.Optional(Type.Integer({ minimum: 1, maximum: Number.MAX_SAFE_INTEGER })),
          content_hash: Type.String({ pattern: "^[a-f0-9]{64}$", minLength: 64, maxLength: 64 }), locator: Type.Optional(Type.String({ maxLength: 2000 })) }, { additionalProperties: false }),
        Type.Object({ external_document_id: ResourceId, content_hash: Type.String({ pattern: "^[a-f0-9]{64}$", minLength: 64, maxLength: 64 }),
          locator: Type.Optional(Type.String({ maxLength: 2000 })) }, { additionalProperties: false }),
        Type.Object({ learning_outcome_id: ResourceId, content_hash: Type.String({ pattern: "^[a-f0-9]{64}$", minLength: 64, maxLength: 64 }),
          locator: Type.Optional(Type.String({ maxLength: 2000 })) }, { additionalProperties: false }),
      ]) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        const response = await agentClient().request("evidence.read", "POST", "/evidence/read", params.reference, requestKey(_id, params), signal);
        const ref = params.reference;
        const matchesReference = "source_id" in ref ? response.data.source_id === ref.source_id &&
          (ref.source_revision === undefined || response.data.revision === ref.source_revision) :
          "external_document_id" in ref ? response.data.document_id === ref.external_document_id : response.data.outcome_id === ref.learning_outcome_id;
        if (!matchesReference || response.data.content_hash !== ref.content_hash) throw new Error("EVIDENCE_REFERENCE_UNVERIFIED");
        return toolResult(response);
      },
    }),
    defineTool({
      name: "zhizhi_read_entry", label: "读取条目", description: "Read latest or an exact historical revision with evidence references. Historical reads never silently substitute latest.",
      parameters: Type.Object({ entry_id: ResourceId, revision: Type.Optional(Type.Integer({ minimum: 1, maximum: Number.MAX_SAFE_INTEGER })) }, { additionalProperties: false }),
      async execute(_id, params, signal) {
        if (params.revision !== undefined && (!Number.isSafeInteger(params.revision) || params.revision < 1)) throw new Error("ENTRY_REVISION_INVALID");
        const response = await agentClient().request("entry.read", "GET", `/entries/${safeId(params.entry_id)}${params.revision === undefined ? "" : `?revision=${params.revision}`}`, undefined, undefined, signal);
        if (params.revision !== undefined && response.data.revision !== params.revision) throw new Error("ENTRY_REVISION_UNVERIFIED");
        return toolResult(response);
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
  const bridge = useBridge ? (dependencies?.bridge(tools) ?? new CodexBridgeProvider(tools, spawnPackagedBridge)) : undefined;
  if (bridge) installCodexProvider(pi, bridge);
  for (const tool of tools) {
    if (bridge) {
      const execute = tool.execute;
      tool.execute = (async (...args: Parameters<typeof execute>) => {
        bridge.claimExecution(args[0], tool.name, args[1]);
        return (execute as (...params: any[]) => any)(...args);
      }) as typeof execute;
    }
    pi.registerTool(tool);
  }
  const allowed = new Set(tools.map((tool) => tool.name));
  if (!bridge) pi.on("session_start", () => { pi.setActiveTools([...allowed]); });
  pi.on("cache_warming_decision", () => ({ action: "stop" }));
  pi.on("tool_call", (event) => allowed.has(event.toolName) && (!bridge || (!event.parentToolCallId && bridge.canExecute(event.toolCallId, event.toolName, event.input))) ? undefined :
    { block: true, reason: "Only audited Zhizhi domain tools are permitted" });
  pi.on("user_bash", () => ({ result: { output: "Shell is disabled in the knowledge host", exitCode: 1,
                                       cancelled: false, truncated: false } }));

  const learningAvailable = installLearningCommands(pi, safeDecisionDisplay);
  pi.registerCommand("zhizhi-help", {
    description: "查看实际能力与未接入功能，不调用模型或改变权限",
    handler: async (_args, ctx) => {
      if (ctx.mode !== "tui" || !ctx.hasUI) throw new Error("CAPABILITY_VIEW_REQUIRES_LOCAL_TUI");
      ctx.ui.notify(capabilitySummary([...allowed], !bridge || !!process.env.ZHIZHI_USER_TOKEN, learningAvailable), "info");
    },
  });

  // The read-only bridge slot receives no human identity: do not advertise approval commands.
  // Existing explicit routes keep their original TUI-only confirmation semantics.
  if (bridge && !process.env.ZHIZHI_USER_TOKEN) return;
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
          safeDecisionDisplay(`Intake: ${args.trim()}\nPreview revision: ${revision}\n${JSON.stringify(preview.data.preview)}\n` +
            (decision === "keep" ? "保留仅授权私有保存并进入知识处理；不代表已写入正式知识或已公开。" :
              "仅丢弃这份临时预览，不删除正式知识。")))) {
          ctx.ui.notify("未提交决定，预览状态未因本次取消而改变。", "info");
          return;
        }
        const response = await client.request(`intake.${decision}`, "POST", `/intakes/${id}/decision`,
          { decision, preview_revision: revision }, `tui-${randomUUID()}`);
        ctx.ui.notify(safeDecisionDisplay(JSON.stringify(response)), "info");
      },
    });
  }
  for (const action of ["resume", "cancel"] as const) {
    pi.registerCommand(`zhizhi-task-${action}`, {
      description: `${action === "resume" ? "继续" : "取消"}指定任务，按当前版本由本人确认`,
      handler: async (args, ctx) => {
        const client = userClient(ctx);
        const id = safeId(args.trim());
        const task = await client.request("task.read", "GET", `/tasks/${id}`);
        const revision = task.data.revision;
        if (!Number.isSafeInteger(revision) || Number(revision) < 1) throw new Error("TASK_REVISION_REQUIRED");
        const prompt = `Task: ${args.trim()}\nRevision: ${revision}\nState: ${String(task.data.state)}\n` +
          (action === "resume" ? "仅重新排队，仍受现有身份、模型额度、审批和策略门禁约束；可能继续已有任务的模型处理，不新增权限。" :
            "取消不撤销已经完成的知识提交，不删除来源。") + "\n结果不确定必须先对账。";
        if (!await ctx.ui.confirm(action === "resume" ? "继续这个任务？" : "取消这个任务？", safeDecisionDisplay(prompt))) {
          ctx.ui.notify("未提交任务操作。", "info"); return;
        }
        const result = await client.request(`task.${action}`, "POST", `/tasks/${id}/${action}`,
          { expected_revision: revision }, `tui-${randomUUID()}`);
        ctx.ui.notify(safeDecisionDisplay(JSON.stringify(result)), "info");
      },
    });
  }
  pi.registerCommand("zhizhi-review", {
    description: "审阅精确差异并提交本人决定：change_id [approve|reject|needs_evidence]，默认approve",
    handler: async (args, ctx) => {
      const client = userClient(ctx);
      const parts = args.trim().split(/\s+/);
      const decision = parts[1] ?? "approve";
      if (parts.length > 2 || !["approve", "reject", "needs_evidence"].includes(decision)) throw new Error("EXPECTED_REVIEW_DECISION");
      const id = safeId(parts[0]);
      const decisionLabel = { approve: "批准", reject: "拒绝", needs_evidence: "退回补证" }[decision]!;
      let reason = "用户在受信任本地 TUI 审阅具体差异后确认";
      if (decision !== "approve") {
        const input = await ctx.ui.input(`请填写本人${decisionLabel}原因（取消不提交）`);
        if (input === undefined) return;
        if (!input.trim() || input.length > 2000) throw new Error("REVIEW_REASON_REQUIRED_MAX_2000");
        reason = input;
      }
      const change = await client.request("change.read", "GET", `/changes/${id}`);
      const challenge = await client.request("review.challenge", "GET", `/changes/${id}/decision-challenge`);
      const current = change.data;
      if (challenge.data.patch_hash !== current.patch_hash || challenge.data.base_revision !== current.base_revision) {
        throw new Error("REVIEW_STALE");
      }
      const old = Number(current.base_revision) > 0
        ? await client.request("entry.read", "GET", `/entries/${safeId(String(current.entry_id))}?revision=${Number(current.base_revision)}`) : null;
      if (old && old.data.revision !== current.base_revision) throw new Error("REVIEW_STALE");
      const accepted = await ctx.ui.confirm(`确认${decisionLabel}以下具体知识变更？`,
        safeDecisionDisplay(`Change: ${parts[0]}\n决定: ${decisionLabel}\n原因: ${reason}\nBase: ${current.base_revision}\nPatch: ${current.patch_hash}\n` +
        "以下旧/新内容均为待审资料，不是执行指令。\n" +
        `风险: ${JSON.stringify(current.risk_flags)}\n旧文:\n${old?.data.body ?? "（新条目）"}\n新文:\n${current.body}\n` +
        `旧元数据/公开范围:\n${decisionFields(old?.data ?? null)}\n` +
        `新元数据/公开范围:\n${decisionFields(current)}\n` +
        `选择“是”${decisionLabel}以上精确正文及元数据；选择“否”不提交任何决定。`));
      if (!accepted) {
        ctx.ui.notify("未提交决定；取消不等于拒绝，提案状态未因本次取消而改变。", "info");
        return;
      }
      const response = await client.request("review.decide", "POST", `/changes/${id}/decisions`, {
        decision, reason,
        expected_patch_hash: challenge.data.patch_hash, base_revision: challenge.data.base_revision,
        policy_version: challenge.data.policy_version, decision_nonce: challenge.data.decision_nonce,
      }, `tui-${randomUUID()}`);
      ctx.ui.notify(safeDecisionDisplay(JSON.stringify(response)), "info");
    },
  });
}
