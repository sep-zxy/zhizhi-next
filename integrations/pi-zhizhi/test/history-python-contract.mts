/** Optional cross-runtime contract: real domain ASGI/Git/SQLite, synthetic data, no model/network E2E. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DomainClient } from '../src/client.ts';
import { readVersions, readDiff } from '../src/entry-history.ts';
const result=spawnSync('uv',['run','--no-project','--offline','python','-B','-s',fileURLToPath(new URL('./fixtures/history-python-contract.py',import.meta.url))],{env:process.env,encoding:'utf8',timeout:30000});
assert.equal(result.status,0,result.stderr);
const responses=JSON.parse(result.stdout);let requests=0;
const client=new DomainClient('http://localhost:8766','synthetic-client-token',false,()=>{},(async(url)=>{
 const expected=responses[requests++];const target=new URL(String(url));assert.equal(target.pathname+target.search,expected.path);
 return Response.json(expected.body,{status:expected.status});
}) as typeof fetch);
const versions=await readVersions(client,'history-example');assert.deepEqual((versions.data.items as any[]).map(x=>x.revision),[2,1]);
const diff=await readDiff(client,'history-example',1,2);assert.match(String(diff.data.unified_diff),/\+Additional boundary/);assert.equal(requests,2);
console.log(JSON.stringify({status:'passed',evidence:'actual_asgi_git_sqlite_responses_validated_by_typescript',actual_model_calls:0,real_network:false}));
