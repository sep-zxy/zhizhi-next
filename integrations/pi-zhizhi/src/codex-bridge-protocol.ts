/** Private JSONL v1: no credentials, executable names, system prompt or authority in requests. */
export const BRIDGE_PROVIDER = 'zhizhi-codex-cli';
export const BRIDGE_MODEL = 'gpt-6.1-sol';
export const MAX_INPUT_BYTES = 256_000;
export const MAX_OUTPUT_BYTES = 1_048_576;
export type ObjectValue = Record<string, unknown>;
export type BridgeCall = { id: string; name: string; arguments: ObjectValue };
export type BridgeMessage = { role: 'user' | 'assistant'; text: string }
  | { role: 'assistant'; tool_call: BridgeCall }
  | { role: 'tool'; tool_call_id: string; name: string; result: ObjectValue }
  | { role: 'tool'; tool_call_id: string; name: string; error_code: 'DOMAIN_REQUEST_FAILED' };
export type BridgeRequest = { version: 1; id: string; type: 'hello' }
  | { version: 1; id: string; type: 'turn'; run_id: string; turn: number; messages: BridgeMessage[] }
  | { version: 1; id: string; type: 'cancel'; request_id: string };
export type BridgeCapabilities = { provider: typeof BRIDGE_PROVIDER; model: typeof BRIDGE_MODEL;
  tools: { name: string; parameters: ObjectValue }[];
  limits: { model_calls_per_run: number; tool_calls_per_run: number; model_calls_per_process: number; timeout_seconds: number } };
export type BridgeResponse = { version: 1; id: string | null; type: 'response'; status: string;
  capabilities?: BridgeCapabilities; error_code?: string | null; task_id?: string;
  requested_model_id?: string; actual_model_id?: string | null; fixture?: boolean;
  usage?: Record<string, number>; output?: ({ type: 'text'; text: string } | ({ type: 'tool_call' } & BridgeCall)) | null };
export class BridgeError extends Error { constructor(readonly code: string) { super(code); } }
export function fail(code = 'BRIDGE_PROTOCOL_INVALID'): never { throw new BridgeError(code); }
export function record(value: unknown): value is ObjectValue { return !!value && typeof value === 'object' && !Array.isArray(value); }
export function exact(value: ObjectValue, required: string[], optional: string[] = []): void {
  if (required.some(key => !Object.hasOwn(value, key)) || Object.keys(value).some(key => ![...required, ...optional].includes(key))) fail();
}
export const uuid = (value: unknown): value is string => typeof value === 'string' && value.length === 36 && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/u.test(value);
export const bounded = (value: unknown, max: number, min = 1): value is number => Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= max;
/** Sort object keys, dropping only empty required arrays for official TypeBox/JSON schema parity. */
export function canonical(value: unknown, schema = false): string {
  function clean(input: unknown): unknown {
    if (Array.isArray(input)) return input.map(clean);
    if (record(input)) return Object.fromEntries(Object.keys(input).sort().filter(key => !(schema && key === 'required' && Array.isArray(input[key]) && !input[key].length)).map(key => [key, clean(input[key])]));
    return input;
  }
  return JSON.stringify(clean(value));
}
/** JSON.parse validates grammar; a second bounded scan rejects duplicate keys rather than last-key wins. */
export function parseStrictJson(line: string): unknown {
  let parsed: unknown; try { parsed = JSON.parse(line); } catch { return fail(); }
  let index = 0; const white = () => { while (/\s/u.test(line[index] ?? '') && index < line.length) index++; };
  const string = () => { const start = index++; while (index < line.length) { const ch = line[index++]; if (ch === '\\') index++; else if (ch === '"') return JSON.parse(line.slice(start, index)) as string; } return fail(); };
  function scan(depth: number): void {
    if (depth > 64) fail(); white(); const ch = line[index];
    if (ch === '{') { index++; white(); const keys = new Set<string>(); if (line[index] === '}') { index++; return; }
      for (;;) { white(); const key = string(); if (keys.has(key)) fail(); keys.add(key); white(); index++; scan(depth + 1); white(); if (line[index++] === '}') return; }
    }
    if (ch === '[') { index++; white(); if (line[index] === ']') { index++; return; } for (;;) { scan(depth + 1); white(); if (line[index++] === ']') return; } }
    if (ch === '"') { string(); return; }
    while (index < line.length && !/[\s,\]}]/u.test(line[index])) index++;
  }
  scan(0);
  const queue = [parsed]; while (queue.length) { const value = queue.pop(); if (typeof value === 'number' && !Number.isFinite(value)) fail(); if (Array.isArray(value)) queue.push(...value); else if (record(value)) queue.push(...Object.values(value)); }
  return parsed;
}
export function parseResponse(raw: unknown): BridgeResponse {
  if (!record(raw)) fail(); exact(raw, ['version', 'id', 'type', 'status'], ['capabilities', 'error_code', 'task_id', 'requested_model_id', 'actual_model_id', 'fixture', 'usage', 'output']);
  if (raw.version !== 1 || raw.type !== 'response' || (raw.id !== null && !uuid(raw.id)) || typeof raw.status !== 'string') fail();
  if (raw.error_code !== undefined && raw.error_code !== null && (typeof raw.error_code !== 'string' || /^[A-Z][A-Z0-9_]{0,99}$/u.exec(raw.error_code)?.[0] !== raw.error_code)) fail();
  return raw as BridgeResponse;
}
export function capabilities(response: BridgeResponse): BridgeCapabilities {
  if (response.status !== 'ready') fail('BRIDGE_NOT_READY');
  const value = response.capabilities; if (!record(value)) fail(); exact(value, ['provider', 'model', 'tools', 'limits']);
  if (value.provider !== BRIDGE_PROVIDER || value.model !== BRIDGE_MODEL || !Array.isArray(value.tools) || !value.tools.length || value.tools.length > 14) fail();
  const names = new Set<string>();
  for (const tool of value.tools) { if (!record(tool)) fail(); exact(tool, ['name', 'parameters']); if (typeof tool.name !== 'string' || names.has(tool.name) || !record(tool.parameters)) fail(); names.add(tool.name); }
  if (!record(value.limits)) fail(); exact(value.limits, ['model_calls_per_run', 'tool_calls_per_run', 'model_calls_per_process', 'timeout_seconds']);
  for (const [key, max] of Object.entries({ model_calls_per_run: 4, tool_calls_per_run: 3, model_calls_per_process: 16, timeout_seconds: 180 })) if (!bounded(value.limits[key as keyof typeof value.limits], max)) fail();
  return value;
}
export interface BridgeTransport { request(request: BridgeRequest, signal?: AbortSignal, timeoutMs?: number): Promise<BridgeResponse>; close(): Promise<void> }
