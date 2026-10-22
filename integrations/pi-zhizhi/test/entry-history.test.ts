import assert from 'node:assert/strict';
import test from 'node:test';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import extension from '../src/index.ts';
import { DomainClient } from '../src/client.ts';
import { readVersions, readDiff } from '../src/entry-history.ts';
const h='a'.repeat(64);
const version={revision:2,title:'架构',updated_at:'2026-10-23T00:00:00Z',content_hash:h,classification_labels:['personal']};
const versions={entry_id:'entry-1',items:[version],has_more:false,classification_labels:['personal']};
const diff={entry_id:'entry-1',from_revision:1,to_revision:2,from_content_hash:h,to_content_hash:h,unified_diff:'--- revision-1\n+++ revision-2\n',classification_labels:['personal']};
function client(data:unknown,calls:string[]=[]){return new DomainClient('http://localhost:8766','fixture',false,()=>{},(async(url,init)=>{calls.push(String(url));assert.equal(init?.method,'GET');return Response.json({data,data_classification:'personal',task_id:null,trace_id:'fixture'});}) as typeof fetch);}
test('history and diff use exact bounded GET routes and retain classification seals',async()=>{
 const calls:string[]=[]; assert.deepEqual((await readVersions(client(versions,calls),'entry-1',20,0)).data,versions);
 assert.match(calls[0],/\/entries\/entry-1\/versions\?limit=20&offset=0$/);
 assert.deepEqual((await readDiff(client(diff,calls),'entry-1',1,2)).data,diff);
 assert.match(calls[1],/\/entries\/entry-1\/diff\?from_revision=1&to_revision=2$/);
});
test('history validates IDs, exact revision seals, hashes, duplicate revisions and shape',async()=>{
 for(const data of [{...versions,entry_id:'entry-2'},{...versions,total:50},{...versions,items:[version,version]},{...versions,items:[{...version,content_hash:'bad'}]},{...versions,items:[{...version,revision:'2'}]},{...versions,items:[{...version,path:'private.md'}]}]) await assert.rejects(readVersions(client(data),'entry-1'),/HISTORY_UNVERIFIED/);
 for(const data of [{...diff,entry_id:'wrong'},{...diff,to_revision:3},{...diff,from_content_hash:'bad'},{...diff,commit:'private'},{...diff,unified_diff:'x'.repeat(307201)}]) await assert.rejects(readDiff(client(data),'entry-1',1,2),/DIFF_UNVERIFIED/);
});
test('history query parser rejects extra/duplicate/missing/unsafe parameters without HTTP',async()=>{
 const calls:string[]=[];const c=client(versions,calls);
 for(const path of ['/entries/entry-1/versions?offset=1001','/entries/entry-1/versions?limit=0','/entries/entry-1/versions?limit=20&limit=30','/entries/entry-1/versions?scope=secret','/entries/entry-1/diff?from_revision=1','/entries/entry-1/diff?from_revision=1&to_revision=9007199254740992']) await assert.rejects(c.request('history','GET',path),/ROUTE_FORBIDDEN/);
 await assert.rejects(c.request('history','POST','/entries/entry-1/versions'),/ROUTE_FORBIDDEN/);
 await assert.rejects(readVersions(c,'entry-1',20,1001),/RANGE_INVALID/);
 await assert.rejects(readDiff(c,'entry-1',0,2),/RANGE_INVALID/);
 assert.equal(calls.length,0);
});
test('historical classifications cannot be broadened by server response',async()=>{
 await assert.rejects(readVersions(client({...versions,items:[{...version,classification_labels:['secret']}]}),'entry-1'),/CLASSIFICATION_FORBIDDEN/);
 await assert.rejects(readDiff(client({...diff,classification_labels:['employer']}),'entry-1',1,2),/CLASSIFICATION_FORBIDDEN/);
});
test('human history commands need TUI, use existing agent identity, and make no write/model call',async()=>{
 const prior={fetch:globalThis.fetch,token:process.env.ZHIZHI_AGENT_TOKEN};const commands=new Map<string,any>();const tools=new Map<string,any>();const calls:string[]=[];const notices:string[]=[];
 try{
 process.env.ZHIZHI_AGENT_TOKEN='fixture';globalThis.fetch=(async(url,init)=>{calls.push(String(url));assert.equal(init?.method,'GET');assert.equal((init?.headers as any).Authorization,'Bearer fixture');return Response.json({data:String(url).includes('/diff?')?diff:versions,data_classification:'personal',task_id:null,trace_id:'fixture'});}) as typeof fetch;
 extension({registerTool:(t:any)=>tools.set(t.name,t),registerCommand:(n:string,c:any)=>commands.set(n,c),on(){}} as unknown as ExtensionAPI);
 const ctx={mode:'tui',hasUI:true,ui:{notify:(x:string)=>notices.push(x)}};
 await commands.get('zhizhi-versions').handler('entry-1 20 0',ctx);await commands.get('zhizhi-diff').handler('entry-1 1 2',ctx);
 await assert.rejects(commands.get('zhizhi-diff').handler('entry-1 1 2',{...ctx,mode:'rpc'}),/LOCAL_TUI/);
 await assert.rejects(commands.get('zhizhi-versions').handler('entry-1 20 0 extra',ctx),/USAGE_INVALID/);
 assert.equal(calls.length,2);assert.equal(notices.length,2);assert.ok(tools.has('zhizhi_entry_versions'));assert.ok(tools.has('zhizhi_entry_diff'));
 }finally{globalThis.fetch=prior.fetch;if(prior.token===undefined)delete process.env.ZHIZHI_AGENT_TOKEN;else process.env.ZHIZHI_AGENT_TOKEN=prior.token;}
});
