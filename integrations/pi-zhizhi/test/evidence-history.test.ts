import assert from 'node:assert/strict';
import test from 'node:test';
import extension from '../src/index.ts';
import {DomainClient} from '../src/client.ts';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';

test('source and immutable evidence reads use only their formal endpoints and preserve hash/revision/locator',async()=>{
 const prior={token:process.env.ZHIZHI_AGENT_TOKEN,fetch:globalThis.fetch}; const tools=new Map<string,any>();const calls:any[]=[];
 try{
  process.env.ZHIZHI_AGENT_TOKEN='fixture-agent';
  globalThis.fetch=(async(url,init)=>{
   calls.push({url:new URL(String(url)),init});
   return Response.json({data:{source_id:'source-1',revision:2,content_hash:'a'.repeat(64),text:'synthetic architecture evidence'},data_classification:'public',task_id:null,trace_id:'fixture'});
  }) as typeof fetch;
  extension({registerTool:(tool:any)=>tools.set(tool.name,tool),registerCommand(){},on(){}} as unknown as ExtensionAPI);
  await tools.get('zhizhi_source').execute('source-call',{source_id:'source-1'});
  const reference={source_id:'source-1',source_revision:2,content_hash:'a'.repeat(64),locator:'lines 1-2'};
  await tools.get('zhizhi_evidence').execute('evidence-call',{reference});
  assert.equal(calls[0].url.pathname,'/api/knowledge/v1/sources/source-1');
  assert.equal(calls[1].url.pathname,'/api/knowledge/v1/evidence/read');
  assert.deepEqual(JSON.parse(calls[1].init.body),reference);
  assert.equal(typeof calls[1].init.headers['Idempotency-Key'],'string');
  for(const data of [{source_id:'source-1',revision:2,content_hash:'b'.repeat(64)},
    {source_id:'source-2',revision:2,content_hash:'a'.repeat(64)},
    {source_id:'source-1',revision:9,content_hash:'a'.repeat(64)}]) {
    globalThis.fetch=(async()=>Response.json({data,data_classification:'public',task_id:null,trace_id:'fixture'})) as typeof fetch;
    await assert.rejects(tools.get('zhizhi_evidence').execute('wrong-reference',{reference}),/EVIDENCE_REFERENCE_UNVERIFIED/);
  }
 }finally{globalThis.fetch=prior.fetch;if(prior.token===undefined)delete process.env.ZHIZHI_AGENT_TOKEN;else process.env.ZHIZHI_AGENT_TOKEN=prior.token;}
});

test('historical entry refuses a substituted revision and invalid integer before exposing a result',async()=>{
 const prior={token:process.env.ZHIZHI_AGENT_TOKEN,fetch:globalThis.fetch};const tools=new Map<string,any>();let calls=0;let revision=3;
 try{
  process.env.ZHIZHI_AGENT_TOKEN='fixture-agent';
  globalThis.fetch=(async(url)=>{calls++;assert.equal(new URL(String(url)).searchParams.get('revision'),'3');return Response.json({data:{revision},data_classification:'public',task_id:null,trace_id:'fixture'});}) as typeof fetch;
  extension({registerTool:(tool:any)=>tools.set(tool.name,tool),registerCommand(){},on(){}} as unknown as ExtensionAPI);
  const tool=tools.get('zhizhi_read_entry');
  assert.equal((await tool.execute('history',{entry_id:'entry-1',revision:3})).details.data.revision,3);
  revision=4;await assert.rejects(tool.execute('history',{entry_id:'entry-1',revision:3}),/ENTRY_REVISION_UNVERIFIED/);
  const count=calls;
  for(const revision of [0,-1,1.1,Number.MAX_SAFE_INTEGER+1]) await assert.rejects(tool.execute('bad',{entry_id:'entry-1',revision}),/ENTRY_REVISION_INVALID/);
  assert.equal(calls,count);
 }finally{globalThis.fetch=prior.fetch;if(prior.token===undefined)delete process.env.ZHIZHI_AGENT_TOKEN;else process.env.ZHIZHI_AGENT_TOKEN=prior.token;}
});

test('new evidence reads do not bypass classification or broaden arbitrary routes',async()=>{
 let calls=0;const client=new DomainClient('http://localhost:3210','fixture',false,()=>{},(async()=>{calls++;return Response.json({data:{text:'private'},data_classification:'secret',task_id:null,trace_id:'fixture'});}) as typeof fetch);
 await assert.rejects(client.request('source.read','GET','/sources/source-1'),/CLASSIFICATION_FORBIDDEN/);
 await assert.rejects(client.request('evidence.read','POST','/evidence/read',{source_id:'source-1'},'fixture-read-key'),/CLASSIFICATION_FORBIDDEN/);
 for(const path of ['/sources/source-1/delete','/evidence/arbitrary','/entries/entry-1?revision=9007199254740992']) await assert.rejects(client.request('bad','GET',path),/ROUTE_FORBIDDEN/);
 assert.equal(calls,2);
});
