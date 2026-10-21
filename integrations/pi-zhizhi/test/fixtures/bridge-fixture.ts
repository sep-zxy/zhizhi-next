/** Test-only dependency injection. Never included in the portable production output. */
import { randomUUID } from 'node:crypto';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { normalizeContext, type Message, type Tool, type Model } from '@earendil-works/pi-ai';
import extension from '../../src/index.ts';
import { CodexBridgeProvider, CODEX_MODEL } from '../../src/codex-provider.ts';
import { BRIDGE_MODEL, BRIDGE_PROVIDER, type BridgeCapabilities, type BridgeRequest, type BridgeResponse, type BridgeTransport } from '../../src/codex-bridge-protocol.ts';

export function registeredTools(): Tool[] {
  const tools: Tool[] = [];
  extension({ registerTool: (tool: Tool) => tools.push(tool), registerProvider() {}, registerCommand() {}, on() {} } as unknown as ExtensionAPI,
    { bridge: local => new CodexBridgeProvider(local, () => { throw new Error('FIXTURE_NOT_INITIALIZED'); }, true) });
  return tools;
}
export function makeCaps(tools: Tool[], limits: Partial<BridgeCapabilities['limits']> = {}): BridgeCapabilities {
  return { provider: BRIDGE_PROVIDER, model: BRIDGE_MODEL, tools: tools.map(({ name, parameters }) => ({ name, parameters: JSON.parse(JSON.stringify(parameters)) })),
    limits: { model_calls_per_run: 4, tool_calls_per_run: 3, model_calls_per_process: 16, timeout_seconds: 180, ...limits } };
}
export const user = (text = 'Only synthetic evidence', timestamp = 1): Message => ({ role: 'user', content: text, timestamp });
export const context = (messages: Message[], tools: Tool[]) => normalizeContext({ systemPrompt: 'DO_NOT_SEND_SYSTEM_OR_CWD_SENTINEL', tools, messages });
export function success(request: BridgeRequest, output: NonNullable<BridgeResponse['output']>): BridgeResponse {
  return { version: 1, id: request.id, type: 'response', status: 'succeeded', task_id: randomUUID(), requested_model_id: BRIDGE_MODEL,
    actual_model_id: null, fixture: true, usage: {}, output, error_code: null };
}
export function fixture(outputs: Array<NonNullable<BridgeResponse['output']> | ((request: BridgeRequest) => BridgeResponse | Promise<BridgeResponse>)>, options: { names?: string[]; limits?: Partial<BridgeCapabilities['limits']>; allowFixture?: boolean } = {}) {
  const trusted = registeredTools(); const tools = trusted.filter(tool => (options.names ?? ['zhizhi_search']).includes(tool.name));
  const caps = makeCaps(tools, options.limits); const requests: BridgeRequest[] = [];
  let closed = 0;
  const transport: BridgeTransport = { async request(request) {
    requests.push(structuredClone(request));
    if (request.type === 'hello') return { version: 1, id: request.id, type: 'response', status: 'ready', capabilities: caps };
    const next = outputs.shift(); if (!next) throw new Error('PRIVATE_EXCEPTION_SENTINEL');
    return typeof next === 'function' ? next(request) : success(request, next);
  }, async close() { closed++; } };
  const bridge = new CodexBridgeProvider(trusted, () => transport, options.allowFixture ?? true);
  return { bridge, tools, caps, requests, closed: () => closed, async run(messages: Message[], model: Model<any> = CODEX_MODEL) {
    const stream = bridge.provider.streamSimple(model, context(messages, tools)); const events = [];
    for await (const event of stream) events.push(event.type);
    return { message: await stream.result(), events };
  } };
}
export function toolOutput(name = 'zhizhi_search', args: Record<string, unknown> = { query: 'synthetic', limit: 5 }): NonNullable<BridgeResponse['output']> & { type: 'tool_call' } {
  return { type: 'tool_call', id: randomUUID(), name, arguments: args };
}
export const domainEnvelope = { data: [{ entry_id: 'entry_fixture', revision: 1 }], task_id: null, trace_id: 'synthetic-domain', data_classification: 'public', classification_labels: ['public'] };
export function toolResult(id: string, name = 'zhizhi_search', details: unknown = domainEnvelope, isError = false): Message {
  return { role: 'toolResult', toolCallId: id, toolName: name, content: [{ type: 'text', text: isError ? 'PRIVATE_EXCEPTION_SENTINEL' : JSON.stringify(details) }], details: details as any, isError, timestamp: 3 };
}
