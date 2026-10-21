/** Explicit optional command, not an npm-test prerequisite: needs the Python test environment. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { JsonlBridgeTransport } from '../src/codex-bridge-transport.ts';
import { CODEX_MODEL, CodexBridgeProvider } from '../src/codex-provider.ts';
import { registeredTools, context, toolResult, user } from './fixtures/bridge-fixture.ts';
const child=spawn('uv',['run','--no-project','--offline','python','-B','-s',fileURLToPath(new URL('./fixtures/bridge-python-contract.py',import.meta.url))],{stdio:['pipe','pipe','pipe'],env:process.env});
const transport=new JsonlBridgeTransport(child);
const tools=registeredTools(); const bridge=new CodexBridgeProvider(tools,()=>transport,true);
try {
  const caps=await bridge.initialize(); assert.deepEqual(caps.tools.map(t=>t.name),['zhizhi_search']);
  const selected=tools.filter(t=>caps.tools.some(item=>item.name===t.name));
  const first=await bridge.provider.streamSimple(CODEX_MODEL,context([user()],selected)).result();
  assert.equal(first.stopReason,'toolUse',first.errorMessage); const call=first.content[0]; assert.equal(call.type,'toolCall');
  if(call.type!=='toolCall')throw new Error('FIXTURE_CALL_REQUIRED');
  bridge.claimExecution(call.id,call.name,call.arguments);
  const last=await bridge.provider.streamSimple(CODEX_MODEL,context([user(),first,toolResult(call.id)],selected)).result();
  assert.equal(last.stopReason,'stop',last.errorMessage); assert.deepEqual(last.content,[{type:'text',text:'PYTHON_JSONL_SYNTHETIC_COMPLETE'}]);
  assert.equal(last.diagnostics![0].details!.fixture,true); assert.equal(last.responseModel,undefined);
  console.log(JSON.stringify({status:'passed',evidence:'real_python_serve_and_ts_provider_with_synthetic_runner',model_turns:2,tool_receipts:1,actual_model_calls:0}));
} finally {await bridge.close();}
