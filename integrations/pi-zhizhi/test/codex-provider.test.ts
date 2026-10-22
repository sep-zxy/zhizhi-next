import assert from 'node:assert/strict';
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import type { Message, Tool } from '@earendil-works/pi-ai';
import { CodexBridgeProvider, CODEX_MODEL, assertToolSchemas } from '../src/codex-provider.ts';
import { canonical, parseStrictJson, capabilities, type BridgeRequest, type BridgeResponse } from '../src/codex-bridge-protocol.ts';
import { context, domainEnvelope, fixture, makeCaps, registeredTools, success, toolOutput, toolResult, user } from './fixtures/bridge-fixture.ts';

const finalText = { type: 'text' as const, text: 'Synthetic result; not real model evidence.' };
function failed(message: { stopReason: string; errorMessage?: string }, code?: RegExp) {
  assert.equal(message.stopReason, 'error');
  if (code) assert.match(message.errorMessage ?? '', code);
  assert.ok(!JSON.stringify(message).includes('PRIVATE_EXCEPTION_SENTINEL'));
}

test('native Provider emits a single provenanced tool then text using official transcript helpers', async () => {
  const call = toolOutput(); const f = fixture([call, finalText], { limits: { model_calls_per_run: 2, tool_calls_per_run: 1 } });
  const first = await f.run([user()]);
  assert.deepEqual(first.events, ['start', 'toolcall_start', 'toolcall_end', 'done']);
  assert.equal(first.message.stopReason, 'toolUse');
  assert.equal(first.message.responseModel, undefined);
  assert.equal(first.message.diagnostics![0].details!.usage_available, false);
  assert.equal(first.message.diagnostics![0].details!.cost_available, false);
  assert.equal(f.bridge.canExecute(call.id, call.name, call.arguments), true);
  assert.equal(f.bridge.canExecute(call.id, call.name, { ...call.arguments, owner: 'other' }), false);
  f.bridge.claimExecution(call.id, call.name, call.arguments);
  assert.throws(() => f.bridge.claimExecution(call.id, call.name, call.arguments), /PROVENANCE/);
  first.message.content.unshift({ type: 'thinking', thinking: 'DO_NOT_SEND_HIDDEN_SENTINEL' });
  const second = await f.run([user(), first.message, toolResult(call.id)]);
  assert.deepEqual(second.events, ['start', 'text_start', 'text_delta', 'text_end', 'done']);
  assert.equal(second.message.stopReason, 'stop');
  assert.deepEqual(f.requests.filter(r => r.type === 'turn').map(r => r.turn), [1, 2]);
  const sent = JSON.stringify(f.requests);
  assert.ok(!sent.includes('DO_NOT_SEND'));
  const secondRequest = f.requests[2]; assert.equal(secondRequest.type, 'turn');
  if (secondRequest.type === 'turn') assert.deepEqual(secondRequest.messages.at(-1), { role: 'tool', tool_call_id: call.id, name: call.name, result: domainEnvelope });
  await f.bridge.close(); assert.equal(f.closed(), 1);
});

test('ambient provider auth does not manufacture keys or accept stored credentials', async () => {
  const f = fixture([]); const slot = f.bridge.provider.auth.apiKey!;
  const args = { ctx: {} } as Parameters<NonNullable<typeof slot.resolve>>[0];
  assert.deepEqual(await slot.resolve!(args), { auth: {}, source: 'ambient' });
  assert.equal(await slot.resolve!({ ...args, credential: { type: 'api_key', key: 'PRIVATE_EXCEPTION_SENTINEL' } } as typeof args), undefined);
  assert.equal(f.requests.length, 1); assert.equal(f.requests[0].type, 'hello');
});

test('trusted allowed subset must exactly match official current tools, normalizing only empty required', () => {
  const tools = registeredTools(); const selected = tools.filter(t => t.name === 'zhizhi_growth'); const caps = makeCaps(selected);
  caps.tools[0].parameters.required = [];
  assert.doesNotThrow(() => assertToolSchemas(selected, tools, caps));
  assert.throws(() => assertToolSchemas(tools, tools, caps), /SCOPE/);
  caps.tools[0].parameters.additionalProperties = true;
  assert.throws(() => assertToolSchemas(selected, tools, caps), /SCHEMA/);
});

for (const [label, output] of [
  ['unknown tool', toolOutput('bash', { command: 'echo wrong' })],
  ['extra authority', toolOutput('zhizhi_search', { query: 'x', owner: 'other' })],
  ['coercible integer', toolOutput('zhizhi_search', { query: 'x', limit: '5' })],
  ['null optional value', toolOutput('zhizhi_search', { query: 'x', limit: null })],
  ['out of range', toolOutput('zhizhi_search', { query: 'x', limit: 51 })],
] as const) test(`rejects ${label} without an executable tool or successful terminal event`, async () => {
  const f = fixture([output]); const result = await f.run([user()]);
  failed(result.message, /TOOL_ARGUMENTS/); assert.deepEqual(result.events, ['start', 'error']);
  assert.equal(f.bridge.canExecute(output.id, output.name, output.arguments), false);
});

for (const [label, patch] of [
  ['model', { id: 'other' }], ['provider', { provider: 'openai' }], ['api', { api: 'openai-responses' }], ['base URL', { baseUrl: 'https://example.invalid' }],
] as const) test(`rejects changed ${label} before sending any bridge request`, async () => {
  const f = fixture([finalText]); const result = await f.run([user()], { ...CODEX_MODEL, ...patch });
  failed(result.message, /MODEL_SELECTION/); assert.equal(f.requests.length, 0);
});

test('fixture evidence is rejected by production provider; actual model is not guessed', async () => {
  const f = fixture([finalText], { allowFixture: false }); failed((await f.run([user()])).message, /PROTOCOL/);
  const g = fixture([request => ({ ...success(request, finalText), actual_model_id: 'unproved-model' })]);
  failed((await g.run([user()])).message, /PROTOCOL/);
});

test('declared usage is preserved but absent fields/cost and model identity remain explicitly unknown', async () => {
  const f = fixture([request => ({ ...success(request, finalText), usage: { input_tokens: 3, output_tokens: 2, total_tokens: 5 } })]);
  const { message } = await f.run([user()]); assert.equal(message.usage.totalTokens, 5);
  assert.equal(message.diagnostics![0].details!.usage_available, true); assert.equal(message.responseModel, undefined);
  assert.equal(message.diagnostics![0].details!.task_id_kind, 'model_call_correlation_not_domain_task');
});

test('model error is a failed terminal event with no automatic retry or leaked exception', async () => {
  const f = fixture([() => { throw new Error('PRIVATE_EXCEPTION_SENTINEL'); }]);
  failed((await f.run([user()])).message, /REQUEST_FAILED/);
  failed((await f.run([user()])).message, /RETRY_REQUIRES/);
  assert.equal(f.requests.filter(r => r.type === 'turn').length, 1);
});

test('old process tool receipts and changed visible transcript never reach the model', async () => {
  const f = fixture([finalText]);
  failed((await f.run([user(), toolResult(randomUUID())])).message, /PROVENANCE/);
  assert.equal(f.requests.filter(r => r.type === 'turn').length, 0);
  const call = toolOutput(); const g = fixture([call, finalText]); const first = await g.run([user()]);
  g.bridge.claimExecution(call.id, call.name, call.arguments);
  failed((await g.run([user('altered'), first.message, toolResult(call.id)])).message, /TRANSCRIPT_CHANGED/);
  assert.equal(g.requests.filter(r => r.type === 'turn').length, 1);
});

test('a different tool ID cannot replay the same name and arguments in one run', async () => {
  const call = toolOutput(); const f = fixture([call, { ...call, id: randomUUID() }]); const first = await f.run([user()]);
  f.bridge.claimExecution(call.id, call.name, call.arguments);
  failed((await f.run([user(), first.message, toolResult(call.id)])).message, /REPLAY_FORBIDDEN/);
});

test('failed writes transmit only the fixed error code and may not suggest another write', async () => {
  const call = toolOutput('zhizhi_intake', { text: 'synthetic only' });
  const f = fixture([call, toolOutput('zhizhi_intake', { text: 'do not retry altered write' })], { names: ['zhizhi_intake', 'zhizhi_search'] });
  const first = await f.run([user()]); f.bridge.claimExecution(call.id, call.name, call.arguments);
  failed((await f.run([user(), first.message, toolResult(call.id, call.name, undefined, true)])).message, /REPLAY_FORBIDDEN/);
  const sent = JSON.stringify(f.requests); assert.ok(sent.includes('DOMAIN_REQUEST_FAILED')); assert.ok(!sent.includes('PRIVATE_EXCEPTION_SENTINEL'));
});

test('classification failure blocks private tool data before a further model turn', async () => {
  const call = toolOutput(); const f = fixture([call, finalText]); const first = await f.run([user()]);
  f.bridge.claimExecution(call.id, call.name, call.arguments);
  failed((await f.run([user(), first.message, toolResult(call.id, call.name, { ...domainEnvelope, classification_labels: ['secret'] })])).message, /ENVELOPE/);
  assert.equal(f.requests.filter(r => r.type === 'turn').length, 1);
});

test('last model call cannot suggest a tool; process model budget counts successful previous runs', async () => {
  const f = fixture([toolOutput()], { limits: { model_calls_per_run: 1 } });
  failed((await f.run([user()])).message, /BUDGET/);
  const g = fixture([finalText, finalText], { limits: { model_calls_per_process: 1 } });
  assert.equal((await g.run([user('one', 1)])).message.stopReason, 'stop');
  failed((await g.run([user('two', 2)])).message, /BUDGET/);
  assert.equal(g.requests.filter(r => r.type === 'turn').length, 1);
});

test('cancellation never becomes a successful answer even if a late transport response arrives', async () => {
  let deliver!: (value: BridgeResponse) => void; let request!: BridgeRequest;
  const f = fixture([value => { request = value; return new Promise(resolve => { deliver = resolve; }); }]);
  const controller = new AbortController(); const stream = f.bridge.provider.streamSimple(CODEX_MODEL, context([user()], f.tools), { signal: controller.signal });
  while (!deliver) await new Promise(resolve => setImmediate(resolve));
  controller.abort(); deliver(success(request, finalText)); const events = [];
  for await (const event of stream) events.push(event.type);
  assert.equal((await stream.result()).stopReason, 'aborted'); assert.deepEqual(events, ['start', 'error']);
});

test('strict JSON rejects duplicate decoded keys, deep values and nonfinite numbers', () => {
  for (const raw of ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"x":{"a":1,"a":2}}', '{"x":1e309}', '['.repeat(66) + '0' + ']'.repeat(66)]) assert.throws(() => parseStrictJson(raw), /PROTOCOL/);
  assert.deepEqual(parseStrictJson('{"quoted":"a\\\"b", "array": [1, true, null, {}]}'), { quoted: 'a"b', array: [1, true, null, {}] });
  assert.notEqual(canonical({ required: ['x'] }, true), canonical({}, true));
});

test('hello hard budgets and unknown fields fail closed', () => {
  const caps = makeCaps(registeredTools().filter(t => t.name === 'zhizhi_search'));
  const response = { version: 1 as const, id: randomUUID(), type: 'response' as const, status: 'ready', capabilities: caps };
  caps.limits.model_calls_per_run = 5; assert.throws(() => capabilities(response), /PROTOCOL/);
  caps.limits.model_calls_per_run = 2; (caps as unknown as Record<string, unknown>).owner = 'model-controlled';
  assert.throws(() => capabilities(response), /PROTOCOL/);
});

test('a provenanced but stale tool receipt cannot replace the currently awaited receipt', async () => {
  const firstCall=toolOutput();const secondCall=toolOutput('zhizhi_search',{query:'second'});
  const f=fixture([firstCall,secondCall,finalText]);const first=await f.run([user()]);
  f.bridge.claimExecution(firstCall.id,firstCall.name,firstCall.arguments);
  const history:Message[]=[user(),first.message,toolResult(firstCall.id)];const second=await f.run(history);
  f.bridge.claimExecution(secondCall.id,secondCall.name,secondCall.arguments);
  failed((await f.run([...history,second.message,toolResult(firstCall.id)])).message,/PROVENANCE/);
  assert.equal(f.requests.filter(r=>r.type==='turn').length,2);
});

test('shutdown closes the provider permanently and cannot create a new model process', async () => {
  const f=fixture([finalText]);await f.bridge.close();failed((await f.run([user()])).message,/CLOSED/);
  assert.equal(f.requests.length,0);
});

test('blocked configuration never consumes a model turn and handshake failure is not retried', async () => {
  const requests:BridgeRequest[]=[];let spawned=0;
  const bridge=new CodexBridgeProvider(registeredTools(),()=>{
    spawned++;return {async request(request){requests.push(request);return {version:1,id:request.id,type:'response',status:'blocked',output:null,error_code:'MODEL_PROCESSING_DISABLED'};},async close(){}};
  });
  const tools=registeredTools().filter(t=>t.name==='zhizhi_search');
  for(let i=0;i<2;i++)failed(await bridge.provider.streamSimple(CODEX_MODEL,context([user('synthetic',i)],tools)).result(),/NOT_READY/);
  assert.equal(spawned,1);assert.deepEqual(requests.map(r=>r.type),['hello']);
});

test('system-message insertion cannot turn an automatic retry into a new explicit user action', async () => {
  const f=fixture([()=>{throw new Error('PRIVATE_EXCEPTION_SENTINEL');},finalText]);
  failed((await f.run([user()])).message);
  failed((await f.run([{role:'system',content:'changed host context',timestamp:0},user()])).message,/RETRY_REQUIRES/);
  assert.equal(f.requests.filter(r=>r.type==='turn').length,1);
});

test('fixture: slow verified hello has its own bounded startup allowance, not a longer model deadline', async () => {
  const tools = registeredTools().filter(t => t.name === 'zhizhi_search');
  const caps = makeCaps(tools, { timeout_seconds: 120 });
  const timeouts: number[] = [];
  let closed = 0;
  const bridge = new CodexBridgeProvider(tools, () => ({
    async request(request, _signal, timeoutMs = 10000) {
      timeouts.push(timeoutMs);
      if (request.type === 'hello') {
        // Deterministic simulated 35-second package verification, no sleep or model.
        if (timeoutMs < 35000) throw new Error('PRIVATE_EXCEPTION_SENTINEL');
        assert.equal(timeoutMs, 90000);
        assert.ok(timeoutMs <= 180000);
        return { version: 1, id: request.id, type: 'response', status: 'ready', capabilities: caps };
      }
      assert.equal(timeoutMs, 120000);
      return success(request, finalText);
    }, async close() { closed++; },
  }), true);
  await bridge.initialize();
  const stream = bridge.provider.streamSimple!(CODEX_MODEL, context([user()], tools), {});
  assert.equal((await stream.result()).stopReason, 'stop');
  assert.deepEqual(timeouts, [90000, 120000]);
  await bridge.close(); assert.equal(closed, 1);
});
