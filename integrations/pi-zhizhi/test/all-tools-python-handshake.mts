/** Actual Python serve -> JSONL -> TS capabilities/provider with full frozen schema; zero model turns. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { JsonlBridgeTransport } from '../src/codex-bridge-transport.ts';
import { CodexBridgeProvider } from '../src/codex-provider.ts';
import { MAX_DOMAIN_TOOLS } from '../src/codex-bridge-protocol.ts';
import { registeredTools } from './fixtures/bridge-fixture.ts';
const wire=JSON.parse(await readFile(new URL('../../../backend/src/ahadiff/knowledge_platform/model/pi_tools_v1.json',import.meta.url),'utf8'));
assert.equal(wire.tools.length,MAX_DOMAIN_TOOLS);
const child=spawn('uv',['run','--no-project','--offline','python','-B','-s',fileURLToPath(new URL('./fixtures/bridge-all-tools-hello.py',import.meta.url))],{stdio:['pipe','pipe','pipe'],env:process.env});
const transport=new JsonlBridgeTransport(child);const bridge=new CodexBridgeProvider(registeredTools(),()=>transport,true);
try {const caps=await bridge.initialize();assert.equal(caps.tools.length,MAX_DOMAIN_TOOLS);assert.deepEqual(caps.tools.map(t=>t.name).sort(),wire.tools.map((t:any)=>t.name).sort());assert.equal(caps.limits.model_calls_per_run,4);assert.equal(caps.limits.tool_calls_per_run,3);assert.equal(caps.limits.model_calls_per_process,16);console.log(JSON.stringify({status:'passed',evidence:'real_python_full_frozen_schema_jsonl_handshake',tools:caps.tools.length,actual_model_calls:0,tool_executions:0}));} finally{await bridge.close();}
