/** Pi v1.3.0 native Provider. Python suggests, Pi executes audited domain tools. */
import { randomUUID } from 'node:crypto';
import { createAssistantMessageEventStream, getCurrentTools, getCurrentSystemPrompt, validateToolCall,
  type AssistantMessage, type Message, type Model, type Provider, type Tool, type ToolCall, type TranscriptContext } from '@earendil-works/pi-ai';
import { assertDomainClassification } from './client.ts';
import { BRIDGE_MODEL, BRIDGE_PROVIDER, BridgeError, bounded, canonical, capabilities, exact, fail, parseResponse, parseStrictJson, record, uuid,
  type BridgeCall, type BridgeCapabilities, type BridgeMessage, type BridgeResponse, type BridgeTransport } from './codex-bridge-protocol.ts';

export const CODEX_MODEL: Model<'zhizhi-codex-jsonl'> = { id: BRIDGE_MODEL, name: 'Codex CLI subscription · exact gpt-6.1-sol', provider: BRIDGE_PROVIDER,
  api: 'zhizhi-codex-jsonl', baseUrl: '', input: ['text'], reasoning: false, contextWindow: 32768, maxTokens: 4096,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const zeroUsage = () => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } });
const writing = new Set(['zhizhi_intake', 'zhizhi_propose_patch', 'zhizhi_radar']);
const fixedText = (value: unknown, max = 64000): string => { if (typeof value !== 'string' || !value || Buffer.byteLength(value) > max) fail('BRIDGE_TRANSCRIPT_INVALID'); return value; };
function textContent(content: Message['content']): string {
  if (typeof content === 'string') return fixedText(content);
  if (!Array.isArray(content) || !content.length || content.some(part => part.type !== 'text')) fail('BRIDGE_TEXT_ONLY');
  return fixedText(content.map(part => part.type === 'text' ? part.text : '').join(''));
}
export function assertToolSchemas(current: Tool[], trusted: Tool[], caps: BridgeCapabilities): void {
  const expected = new Map(caps.tools.map(tool => [tool.name, tool.parameters]));
  if (new Set(current.map(tool => tool.name)).size !== current.length || current.length !== expected.size) fail('BRIDGE_TOOL_SCOPE_CHANGED');
  for (const tool of current) {
    const local = trusted.find(item => item.name === tool.name); const advertised = expected.get(tool.name);
    if (!local || !advertised || canonical(tool.parameters, true) !== canonical(local.parameters, true) || canonical(advertised, true) !== canonical(local.parameters, true)) fail('BRIDGE_TOOL_SCHEMA_CHANGED');
  }
}
function strictTool(tool: BridgeCall, tools: Tool[]): ToolCall {
  if (!uuid(tool.id) || !record(tool.arguments)) fail('BRIDGE_TOOL_INVALID');
  const call: ToolCall = { type: 'toolCall', id: tool.id, name: tool.name, arguments: tool.arguments as ToolCall['arguments'] };
  let checked: unknown;
  try { checked = validateToolCall(tools, call); } catch { return fail('BRIDGE_TOOL_ARGUMENTS_INVALID'); }
  if (canonical(checked) !== canonical(call.arguments)) fail('BRIDGE_TOOL_ARGUMENTS_INVALID');
  return call;
}
function envelope(value: unknown): Record<string, unknown> {
  if (!record(value)) fail('BRIDGE_DOMAIN_ENVELOPE_INVALID');
  exact(value, ['data', 'task_id', 'trace_id', 'data_classification'], ['classification_labels']);
  if ((!record(value.data) && !Array.isArray(value.data)) || (value.task_id !== null && typeof value.task_id !== 'string') || typeof value.trace_id !== 'string') fail('BRIDGE_DOMAIN_ENVELOPE_INVALID');
  try { assertDomainClassification(value); } catch { return fail('BRIDGE_DOMAIN_ENVELOPE_INVALID'); }
  return value;
}
export class CodexBridgeProvider {
  readonly provider: Provider; private transport?: BridgeTransport; private hello?: Promise<BridgeCapabilities>;
  private busy = false; private terminated = false; private processCalls = 0; private startedUsers = new Set<string>(); private run?: { id: string; turn: number; calls: number; tools: number; prefix: BridgeMessage[]; closed: boolean; unknownWrite: boolean; fingerprints: Set<string> };
  private issued = new Map<string, { call: BridgeCall; executed: boolean }>();
  constructor(private readonly trustedTools: Tool[], private readonly factory: () => BridgeTransport, private readonly allowFixture = false) {
    const configured = async () => { try { await this.initialize(); return true; } catch { return false; } };
    this.provider = {
      id: BRIDGE_PROVIDER, name: '知枝 · 现有 Codex CLI 订阅',
      auth: { apiKey: { name: '包内本机配置（不是订阅认证证明）',
        check: async ({ credential }) => !credential && await configured() ? { type: 'api_key', source: 'ambient' } : undefined,
        resolve: async ({ credential }) => !credential && await configured() ? { auth: {}, source: 'ambient' } : undefined } },
      getModels: () => [CODEX_MODEL],
      stream: (model, context, options) => this.stream(model, context, options?.signal, options?.apiKey),
      streamSimple: (model, context, options) => this.stream(model, context, options?.signal, options?.apiKey),
    };
  }
  initialize(): Promise<BridgeCapabilities> {
    if (this.terminated) return Promise.reject(new BridgeError('BRIDGE_CLOSED'));
    // A failed handshake is sticky. Restarting is an explicit host action, never a hidden retry.
    return this.hello ??= (async () => {
      this.transport = this.factory(); const id = randomUUID();
      const response = parseResponse(await this.transport.request({ version: 1, id, type: 'hello' }));
      if (response.id !== id) fail(); const caps = capabilities(response);
      assertToolSchemas(this.trustedTools.filter(tool => caps.tools.some(value => value.name === tool.name)), this.trustedTools, caps);
      return caps;
    })();
  }
  canExecute(id: string, name: string, args: unknown): boolean {
    const entry = this.issued.get(id);
    return !!entry && !entry.executed && entry.call.name === name && canonical(entry.call.arguments) === canonical(args) && this.run?.closed === false;
  }
  claimExecution(id: string, name: string, args: unknown): void {
    if (!this.canExecute(id, name, args)) fail('BRIDGE_TOOL_PROVENANCE_INVALID'); this.issued.get(id)!.executed = true;
  }
  async close(): Promise<void> { this.terminated = true; if (this.run) this.run.closed = true; await this.transport?.close(); }
  private messages(context: TranscriptContext): BridgeMessage[] {
    // Official replay helpers, not the obsolete context.tools/context.systemPrompt fields.
    void getCurrentSystemPrompt(context.messages); // Never sent; cwd, hidden prompts and signatures stay in Pi.
    let start = -1; context.messages.forEach((message, index) => { if (message.role === 'user') start = index; });
    if (start < 0) fail('BRIDGE_NEW_USER_MESSAGE_REQUIRED');
    const values: BridgeMessage[] = [];
    for (const message of context.messages.slice(start)) {
      if (message.role === 'system') continue;
      if (message.role === 'user') values.push({ role: 'user', text: textContent(message.content) });
      else if (message.role === 'assistant') {
        const visible = message.content.filter(part => part.type !== 'thinking');
        if (visible.length === 1 && visible[0].type === 'toolCall') {
          const part = visible[0]; const previous = this.issued.get(part.id);
          const call = { id: part.id, name: part.name, arguments: part.arguments };
          if (!previous || canonical(previous.call) !== canonical(call) || !previous.executed) fail('BRIDGE_TOOL_PROVENANCE_INVALID');
          values.push({ role: 'assistant', tool_call: call });
        } else if (visible.length && visible.every(part => part.type === 'text')) values.push({ role: 'assistant', text: textContent(visible) });
        else fail('BRIDGE_TRANSCRIPT_INVALID');
      } else if (message.role === 'toolResult') {
        const previous = this.issued.get(message.toolCallId);
        if (!previous?.executed || previous.call.name !== message.toolName) fail('BRIDGE_TOOL_PROVENANCE_INVALID');
        if (message.isError) values.push({ role: 'tool', tool_call_id: message.toolCallId, name: message.toolName, error_code: 'DOMAIN_REQUEST_FAILED' });
        else {
          const result = message.details ?? parseStrictJson(textContent(message.content));
          values.push({ role: 'tool', tool_call_id: message.toolCallId, name: message.toolName, result: envelope(result) });
        }
      } else fail('BRIDGE_TRANSCRIPT_INVALID');
    }
    if (values.length > 32) fail('BRIDGE_TRANSCRIPT_INVALID'); return values;
  }
  private stream(model: Model<any>, context: TranscriptContext, signal?: AbortSignal, apiKey?: string) {
    const stream = createAssistantMessageEventStream();
    const partial: AssistantMessage = { role: 'assistant', content: [], api: CODEX_MODEL.api, provider: BRIDGE_PROVIDER, model: BRIDGE_MODEL,
      usage: zeroUsage(), stopReason: 'pending', timestamp: Date.now(), diagnostics: [{ type: 'zhizhi.codex_cli', timestamp: Date.now(), details: { usage_available: false, cost_available: false, actual_model_available: false } }] };
    void (async () => {
      let owns = false;
      try {
        if (this.terminated) fail('BRIDGE_CLOSED');
        if (this.busy) fail('BRIDGE_BUSY'); this.busy = true; owns = true;
        if (signal?.aborted) fail('BRIDGE_CANCELLED');
        if (model.id !== BRIDGE_MODEL || model.provider !== BRIDGE_PROVIDER || model.api !== CODEX_MODEL.api || model.baseUrl !== '' || apiKey) fail('BRIDGE_MODEL_SELECTION_CHANGED');
        const caps = await this.initialize(); const current = getCurrentTools(context.messages); assertToolSchemas(current, this.trustedTools, caps);
        const messages = this.messages(context);
        if (messages.length === 1 && messages[0].role === 'user') {
          const user = context.messages.filter(message => message.role === 'user').at(-1)!;
          const userKey = canonical({ timestamp: user.timestamp, text: messages[0] });
          if (this.startedUsers.has(userKey)) fail('BRIDGE_RETRY_REQUIRES_NEW_USER_MESSAGE');
          if (this.run && !this.run.closed) fail('BRIDGE_RUN_UNFINISHED');
          if (this.startedUsers.size >= 16) fail('BRIDGE_PROCESS_BUDGET_EXHAUSTED');
          this.startedUsers.add(userKey);
          this.run = { id: randomUUID(), turn: 1, calls: 0, tools: 0, prefix: [], closed: false, unknownWrite: false, fingerprints: new Set() };
        }
        const run = this.run; if (!run || run.closed) fail('BRIDGE_RUN_NOT_RESTORABLE');
        if (run.prefix.length && (messages.length !== run.prefix.length + 1 || canonical(messages.slice(0, -1)) !== canonical(run.prefix) || messages.at(-1)?.role !== 'tool')) fail('BRIDGE_TRANSCRIPT_CHANGED');
        const expected = run.prefix.at(-1); const receipt = messages.at(-1);
        if (expected && expected.role === 'assistant' && 'tool_call' in expected &&
          (!receipt || receipt.role !== 'tool' || receipt.tool_call_id !== expected.tool_call.id || receipt.name !== expected.tool_call.name)) fail('BRIDGE_TOOL_PROVENANCE_INVALID');
        const last = messages.at(-1); if (last?.role === 'tool' && 'error_code' in last && writing.has(last.name)) run.unknownWrite = true;
        if (run.calls >= caps.limits.model_calls_per_run || this.processCalls >= caps.limits.model_calls_per_process) fail('BRIDGE_MODEL_BUDGET_EXHAUSTED');
        run.calls++; this.processCalls++; const id = randomUUID();
        stream.push({ type: 'start', partial });
        const response = parseResponse(await this.transport!.request({ version: 1, id, type: 'turn', run_id: run.id, turn: run.turn++, messages }, signal, caps.limits.timeout_seconds * 1000));
        if (signal?.aborted || this.terminated || run.closed) fail('BRIDGE_CANCELLED');
        this.validateTurn(response, id); const output = response.output!;
        partial.diagnostics = [{ type: 'zhizhi.codex_cli', timestamp: Date.now(), details: { task_id: response.task_id!, task_id_kind: 'model_call_correlation_not_domain_task', requested_model_id: BRIDGE_MODEL, actual_model_id: response.actual_model_id ?? null, actual_model_available: response.actual_model_id === BRIDGE_MODEL, fixture: response.fixture!, usage_available: Object.keys(response.usage!).length > 0, cost_available: false } }];
        const usage = response.usage!;
        partial.usage.input = usage.input_tokens ?? 0; partial.usage.output = usage.output_tokens ?? 0; partial.usage.cacheRead = usage.cached_input_tokens ?? 0; partial.usage.totalTokens = usage.total_tokens ?? 0;
        if (response.actual_model_id === BRIDGE_MODEL) partial.responseModel = BRIDGE_MODEL;
        if (output.type === 'text') {
          exact(output, ['type', 'text']); const text = fixedText(output.text, 128000);
          const block = { type: 'text' as const, text: '' }; partial.content.push(block); stream.push({ type: 'text_start', contentIndex: 0, partial });
          block.text = text; stream.push({ type: 'text_delta', contentIndex: 0, delta: text, partial }); stream.push({ type: 'text_end', contentIndex: 0, content: text, partial });
          partial.stopReason = 'stop'; run.closed = true; stream.push({ type: 'done', reason: 'stop', message: partial });
        } else {
          exact(output, ['type', 'id', 'name', 'arguments']);
          if (run.tools >= caps.limits.tool_calls_per_run || run.calls >= caps.limits.model_calls_per_run || (run.unknownWrite && writing.has(output.name))) fail('BRIDGE_TOOL_BUDGET_OR_REPLAY_FORBIDDEN');
          const call = strictTool(output, current); const fingerprint = canonical({ name: call.name, arguments: call.arguments });
          if (this.issued.has(call.id) || run.fingerprints.has(fingerprint)) fail('BRIDGE_TOOL_REPLAY_FORBIDDEN');
          run.tools++; run.fingerprints.add(fingerprint);
          const issued = { id: call.id, name: call.name, arguments: call.arguments };
          this.issued.set(call.id, { call: structuredClone(issued), executed: false }); run.prefix = [...messages, { role: 'assistant', tool_call: structuredClone(issued) }];
          partial.content.push(call); stream.push({ type: 'toolcall_start', contentIndex: 0, partial }); stream.push({ type: 'toolcall_end', contentIndex: 0, toolCall: call, partial });
          partial.stopReason = 'toolUse'; stream.push({ type: 'done', reason: 'toolUse', message: partial });
        }
      } catch (error) {
        if (owns && this.run) this.run.closed = true;
        const code = error instanceof BridgeError ? error.code : 'BRIDGE_REQUEST_FAILED';
        partial.content = []; partial.stopReason = code === 'BRIDGE_CANCELLED' ? 'aborted' : 'error'; partial.errorMessage = code;
        stream.push({ type: 'error', reason: partial.stopReason, error: partial });
      } finally { if (owns) this.busy = false; stream.end(partial); }
    })();
    return stream;
  }
  private validateTurn(value: BridgeResponse, id: string): void {
    if (value.id !== id) fail();
    if (value.status !== 'succeeded') { if (value.output !== null) fail(); fail(value.status === 'cancelled' ? 'BRIDGE_CANCELLED' : value.error_code ?? 'BRIDGE_MODEL_FAILED'); }
    exact(value, ['version', 'id', 'type', 'status', 'task_id', 'requested_model_id', 'actual_model_id', 'fixture', 'usage', 'output', 'error_code']);
    if (!uuid(value.task_id) || value.requested_model_id !== BRIDGE_MODEL || ![null, BRIDGE_MODEL].includes(value.actual_model_id!) || (typeof value.fixture !== 'boolean' || (value.fixture && !this.allowFixture)) || !record(value.usage) || value.error_code !== null || !record(value.output) || !['text', 'tool_call'].includes(value.output.type)) fail();
    for (const [key, count] of Object.entries(value.usage)) if (!['input_tokens', 'output_tokens', 'cached_input_tokens', 'total_tokens'].includes(key) || !bounded(count, 100000000, 0)) fail();
  }
}
