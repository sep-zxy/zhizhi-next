import assert from 'node:assert/strict';
import test from 'node:test';
import extension from '../src/index.ts';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';

test('reject and needs_evidence are explicit human inputs bound to the exact challenge, not model tools',async()=>{
 const previous={token:process.env.ZHIZHI_USER_TOKEN,fetch:globalThis.fetch};
 const commands=new Map<string,any>(); const tools:string[]=[];const writes:any[]=[];const prompts:string[]=[];
 try {
  process.env.ZHIZHI_USER_TOKEN='fixture-human';
  globalThis.fetch=(async(url,init)=>{
   const path=String(url);
   if(init?.method==='POST')writes.push(JSON.parse(String(init.body)));
   const data=path.endsWith('/decision-challenge')?{patch_hash:'exact-patch',base_revision:0,policy_version:'v1',decision_nonce:'one-time-fixture'}:
      {patch_hash:'exact-patch',base_revision:0,body:'new exact body',metadata:{topic:'architecture'},risk_flags:['major']};
   return Response.json({data,task_id:null,trace_id:'fixture'});
  }) as typeof fetch;
  extension({registerTool:(tool:any)=>tools.push(tool.name),registerCommand:(name:string,command:any)=>commands.set(name,command),on(){}} as unknown as ExtensionAPI);
  assert.ok(!tools.some(name=>/reject|approve|decide/.test(name)));
  const ctx={mode:'tui',hasUI:true,ui:{input:async()=>'本人发现证据缺口',confirm:async(_title:string,body:string)=>{prompts.push(body);return true;},notify(){}}};
  for(const decision of ['reject','needs_evidence']) await commands.get('zhizhi-review').handler('change-1 '+decision,ctx);
  assert.deepEqual(writes.map(value=>value.decision),['reject','needs_evidence']);
  for(const value of writes){assert.equal(value.reason,'本人发现证据缺口');assert.equal(value.expected_patch_hash,'exact-patch');assert.equal(value.base_revision,0);assert.equal(value.decision_nonce,'one-time-fixture');}
  assert.match(prompts[0],/决定: 拒绝/);assert.match(prompts[1],/决定: 退回补证/);
  const before=writes.length;
  await commands.get('zhizhi-review').handler('change-1 reject',{...ctx,ui:{...ctx.ui,input:async()=>undefined}});
  await commands.get('zhizhi-review').handler('change-1 needs_evidence',{...ctx,ui:{...ctx.ui,confirm:async()=>false}});
  assert.equal(writes.length,before);
  await assert.rejects(commands.get('zhizhi-review').handler('change-1 arbitrary',ctx),/EXPECTED_REVIEW_DECISION/);
 }finally{globalThis.fetch=previous.fetch;if(previous.token===undefined)delete process.env.ZHIZHI_USER_TOKEN;else process.env.ZHIZHI_USER_TOKEN=previous.token;}
});
