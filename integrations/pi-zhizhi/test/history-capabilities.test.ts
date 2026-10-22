import assert from 'node:assert/strict';
import test from 'node:test';
import { capabilities, MAX_DOMAIN_TOOLS, type BridgeResponse } from '../src/codex-bridge-protocol.ts';
import { registeredTools, makeCaps } from './fixtures/bridge-fixture.ts';
import { CodexBridgeProvider } from '../src/codex-provider.ts';
test('actual capabilities parser and provider handshake admit all 18 trusted tools without raising call budgets',async()=>{
 const tools=registeredTools();assert.equal(tools.length,MAX_DOMAIN_TOOLS);const caps=makeCaps(tools);
 const response={version:1,id:null,type:'response',status:'ready',capabilities:caps} as BridgeResponse;
 assert.equal(capabilities(response).tools.length,18);
 const bridge=new CodexBridgeProvider(tools,()=>({request:async request=>({...response,id:request.id}),close:async()=>{}}),true);
 try{assert.equal((await bridge.initialize()).tools.length,18);}finally{await bridge.close();}
 assert.equal(caps.limits.model_calls_per_run,4);assert.equal(caps.limits.tool_calls_per_run,3);assert.equal(caps.limits.model_calls_per_process,16);
 for(const name of ['zhizhi_search','zhizhi_entry_versions','zhizhi_entry_diff']) {
  const subset=makeCaps(tools.filter(t=>t.name===name));assert.equal(capabilities({...response,capabilities:subset}).tools.length,1);
 }
 assert.throws(()=>capabilities({...response,capabilities:{...caps,tools:[...caps.tools,{name:'untrusted_extra',parameters:{type:'object'}}]}}),/PROTOCOL/);
 for(const [key,value] of [['model_calls_per_run',5],['tool_calls_per_run',4],['model_calls_per_process',17]] as const) assert.throws(()=>capabilities({...response,capabilities:{...caps,limits:{...caps.limits,[key]:value}}}),/PROTOCOL/);
});
