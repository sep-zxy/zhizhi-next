/** Explicit test extension: real Pi runtime, injected synthetic model and HTTP transports. */
import assert from 'node:assert/strict';
import { appendFileSync } from 'node:fs';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import extension from '../../src/index.ts';
import { CodexBridgeProvider } from '../../src/codex-provider.ts';
import { domainEnvelope, makeCaps, success, toolOutput } from './bridge-fixture.ts';
const audit = (value: unknown) => appendFileSync(process.env.ZHIZHI_FIXTURE_AUDIT!, JSON.stringify(value) + '\n');
export default function fixtureExtension(pi: ExtensionAPI) {
  const call = toolOutput(); let modelCalls=0; let domainCalls=0;
  globalThis.fetch = (async (input,init) => {
    const url=new URL(String(input));
    assert.equal(url.origin,'http://127.0.0.1:8766'); assert.equal(url.pathname,'/api/knowledge/v1/search'); assert.equal(init?.method,'GET');
    assert.equal(url.searchParams.get('q'),'synthetic'); assert.equal(url.searchParams.get('limit'),'5');
    assert.equal(new Headers(init?.headers).get('Authorization'),'Bearer synthetic-agent-not-a-credential');
    domainCalls++; audit({event:'domain',calls:domainCalls,path:url.pathname,method:init?.method});
    return Response.json(domainEnvelope);
  }) as typeof fetch;
  extension(pi,{bridge:tools=>new CodexBridgeProvider(tools,()=>({
    async request(request) {
      audit({event:'bridge',request});
      if(request.type==='hello')return {version:1,id:request.id,type:'response',status:'ready',capabilities:makeCaps(tools.filter(t=>t.name==='zhizhi_search'),{model_calls_per_run:2,tool_calls_per_run:1})};
      assert.equal(request.type,'turn'); modelCalls++;
      if(process.env.ZHIZHI_FIXTURE_BRIDGE_FAILURE==='timeout') return {version:1,id:request.id,type:'response',status:'blocked',output:null,error_code:'BRIDGE_TIMEOUT'};
      if(modelCalls===1) {assert.equal(domainCalls,0); return success(request,call);}
      assert.equal(modelCalls,2); assert.equal(domainCalls,1);
      assert.equal(request.turn,2); assert.deepEqual(request.messages.at(-1),{role:'tool',tool_call_id:call.id,name:call.name,result:domainEnvelope});
      return success(request,{type:'text',text:'SYNTHETIC_PI_TOOL_LOOP_COMPLETE'});
    },async close(){audit({event:'closed',modelCalls,domainCalls});},
  }),true)});
}
