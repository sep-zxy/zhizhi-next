/** Actual Python intake/CAS receipts consumed by the real TypeScript owner command; synthetic only. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DomainClient } from '../src/client.ts';
import { installMaterialCommand } from '../src/material-commands.ts';
const run=spawnSync('uv',['run','--no-project','--offline','python','-B','-s',fileURLToPath(new URL('./fixtures/material-python-contract.py',import.meta.url))],{env:process.env,encoding:'utf8',timeout:30000});assert.equal(run.status,0,run.stderr);const fixture=JSON.parse(run.stdout);
let command:any;let calls=0;const notes:string[]=[];
const client=new DomainClient('http://localhost:8766','synthetic-owner-client',true,()=>{},(async(url,init)=>{
 calls++;assert.equal(new URL(String(url)).pathname,`/api/knowledge/v1/intakes/${fixture.id}${init?.method==='POST'?'/supplement':''}`);
 if(init?.method==='POST'){assert.deepEqual(JSON.parse(String(init.body)),fixture.payload);assert.ok((init.headers as any)['Idempotency-Key']);}
 return Response.json(init?.method==='POST'?fixture.changed:fixture.saved);
}) as typeof fetch);
installMaterialCommand({registerCommand:(_name:string,value:any)=>command=value} as any,()=>client,s=>s);
await command.handler(fixture.id,{mode:'tui',hasUI:true,ui:{editor:async()=>fixture.text,confirm:async()=>true,notify:(text:string)=>notes.push(text)}});
assert.equal(calls,2);assert.match(notes[0],/补料回执已核验/);console.log(JSON.stringify({status:'passed',evidence:'actual_python_intake_cas_receipt_to_ts_owner_command',actual_model_calls:0,real_network:false}));
