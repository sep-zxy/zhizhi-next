import assert from 'node:assert/strict';
import test from 'node:test';
import { DomainClient } from '../src/client.ts';
import { installMaterialCommand } from '../src/material-commands.ts';
import extension from '../src/index.ts';
const saved={intake_id:'intake-1',preview_revision:2,state:'partial',source_id:null};
const receipt=(text:string)=>({...saved,preview_revision:3,preview:{manual_supplement:true,document:{body_text:text}}});
function setup(options:{text?:string;cancel?:boolean;response?:unknown;failOnce?:boolean}={}) {
 const calls:any[]=[];const notes:string[]=[];let handler:any;let failed=false;
 const client=new DomainClient('http://localhost:8766','fixture-user',true,()=>{},(async(_url,init)=>{
   calls.push(init);if(init?.method==='POST' && options.failOnce && !failed){failed=true;throw new Error('synthetic uncertain transport');}
   return Response.json({data:init?.method==='POST'?(options.response??receipt(options.text??'本人补充来源文本')):saved,data_classification:'personal',task_id:null,trace_id:'fixture'});
 }) as typeof fetch);
 installMaterialCommand({registerCommand:(_n:string,c:any)=>handler=c.handler} as any,()=>client,x=>x);
 const ctx={mode:'tui',hasUI:true,ui:{editor:async()=>options.cancel?undefined:options.text??'本人补充来源文本',confirm:async()=>true,notify:(s:string)=>notes.push(s)}};
 return {calls,notes,handler,ctx};
}
test('manual supplement uses editor-only text, exact CAS and idempotency receipt; never keep/model',async()=>{
 const x=setup();await x.handler('intake-1',x.ctx);assert.equal(x.calls.length,2);
 assert.deepEqual(JSON.parse(x.calls[1].body),{text:'本人补充来源文本',expected_preview_revision:2});
 assert.ok(x.calls[1].headers['Idempotency-Key']);assert.match(x.notes[0],/未收藏、未写入知识、未公开/);
});
test('cancel, RPC, inline generated text and invalid text cannot write',async()=>{
 const x=setup({cancel:true});await x.handler('intake-1',x.ctx);assert.equal(x.calls.length,1);
 await assert.rejects(x.handler('intake-1',{...x.ctx,mode:'rpc'}),/LOCAL_TUI/);
 await assert.rejects(x.handler('intake-1 model-generated text',x.ctx),/RESOURCE_ID/);assert.equal(x.calls.length,1);
 for(const text of ['','   ','x'.repeat(100001),'\ud800']) {const y=setup({text});await assert.rejects(y.handler('intake-1',y.ctx),/TEXT_INVALID/);assert.equal(y.calls.length,1);}
 const y=setup();await y.handler('intake-1',{...y.ctx,ui:{...y.ctx.ui,confirm:async()=>false}});assert.equal(y.calls.length,1);
});
test('receipt cannot silently accept stale/jumped versions, wrong source or modified user text',async()=>{
 for(const response of [{...receipt('本人补充来源文本'),preview_revision:2},{...receipt('本人补充来源文本'),preview_revision:9},{...receipt('本人补充来源文本'),source_id:'retained'},{...receipt('本人补充来源文本'),intake_id:'wrong'},receipt('rewritten by model')]) {
  const x=setup({response});await assert.rejects(x.handler('intake-1',x.ctx),/RECEIPT_UNVERIFIED/);assert.equal(x.notes.length,0);
 }
});
test('uncertain retry reuses same payload key and duplicate invocation is blocked while editor open',async()=>{
 const x=setup({failOnce:true});await assert.rejects(x.handler('intake-1',x.ctx));await x.handler('intake-1',x.ctx);
 const posts=x.calls.filter(c=>c.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].headers['Idempotency-Key'],posts[1].headers['Idempotency-Key']);
 const y=setup();let unblock!:(v:undefined)=>void;
 const pending=y.handler('intake-1',{...y.ctx,ui:{...y.ctx.ui,editor:()=>new Promise<undefined>(resolve=>unblock=resolve)}});
 await new Promise(resolve=>setImmediate(resolve));await assert.rejects(y.handler('intake-1',y.ctx),/IN_PROGRESS/);unblock(undefined);await pending;assert.equal(y.calls.length,1);
});
test('model-side client cannot invoke human supplementation even if it knows the route',async()=>{
 const c=new DomainClient('http://localhost:8766','fixture-agent',false,()=>{},(async()=>{throw new Error('must not fetch');}) as typeof fetch);
 await assert.rejects(c.request('intake.supplement','POST','/intakes/intake-1/supplement',{text:'model'},'key'),/ROUTE_FORBIDDEN/);
});
test('change command is read-only, validates returned ID and does not claim reconciliation or commit',async()=>{
 const prior={fetch:globalThis.fetch,token:process.env.ZHIZHI_AGENT_TOKEN};const commands=new Map<string,any>();let calls=0;let id='change-1';const notes:string[]=[];
 try{process.env.ZHIZHI_AGENT_TOKEN='fixture-agent';globalThis.fetch=(async(url,init)=>{calls++;assert.match(String(url),/\/changes\/change-1$/);assert.equal(init?.method,'GET');return Response.json({data:{change_id:id,state:'reconcile_required'},data_classification:'personal',trace_id:'fixture',task_id:null});}) as typeof fetch;
 extension({registerTool(){},registerCommand:(n:string,c:any)=>commands.set(n,c),on(){}} as any);
 const ctx={mode:'tui',hasUI:true,ui:{notify:(text:string)=>notes.push(text)}};await commands.get('zhizhi-change').handler('change-1',ctx);assert.match(notes[0],/不代表已修复/);
 id='other';await assert.rejects(commands.get('zhizhi-change').handler('change-1',ctx),/UNVERIFIED/);await assert.rejects(commands.get('zhizhi-change').handler('change-1',{...ctx,mode:'rpc'}),/LOCAL_TUI/);assert.equal(calls,2);
 }finally{globalThis.fetch=prior.fetch;if(prior.token===undefined)delete process.env.ZHIZHI_AGENT_TOKEN;else process.env.ZHIZHI_AGENT_TOKEN=prior.token;}
});
