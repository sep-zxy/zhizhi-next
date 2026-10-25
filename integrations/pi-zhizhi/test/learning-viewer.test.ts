/** Explicit synthetic viewer fixtures; no credential discovery or live models. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { LearningViewerClient, noteHash } from '../src/learning-viewer-client.ts';
import { installLearningCommands } from '../src/learning-commands.ts';
import type { ExtensionAPI, ExtensionCommandContext } from '@earendil-works/pi-coding-agent';
const id='11111111-1111-4111-8111-111111111111';
const req='22222222-2222-4222-8222-222222222222';
const body='架构机制与技术栈取舍';
const fixture=(text='本人理解',revision=1)=>({context_id:id,context_version:revision,source_kind:'wiki_entry',state:'reading',note_text:text,note_sha256:noteHash(text),
  entry:{entry_id:'entry-1',revision:2,title:'架构',body,content_hash:noteHash(body)}});
const reference={entry_id:'entry-1',revision:2,expected_content_hash:noteHash(body)};

test('viewer binding is explicit loopback and never accepts domain URL, remote hosts or embedded auth',()=>{
  for(const url of ['http://127.0.0.1','https://example.com:443','http://user:pass@localhost:3000','http://localhost:3000/admin','file:///tmp'])
    assert.throws(()=>new LearningViewerClient(url,'fixture'),/EXPLICIT_LOCAL_BINDING/);
  assert.doesNotThrow(()=>new LearningViewerClient('http://127.0.0.1:3210','fixture'));
});

test('viewer keeps exact entry hash, note CAS and saved-note-only submission contract',async()=>{
  const calls:any[]=[]; let current=fixture();
  const client=new LearningViewerClient('http://127.0.0.1:3210','fixture-viewer',(async(url,init)=>{
    calls.push({url:String(url),init});
    const input=init?.body?JSON.parse(String(init.body)):null;
    if(String(url).endsWith('/note')) current=fixture(input.note_text,2);
    if(String(url).endsWith('/understanding/submissions')) return Response.json({...current,submission_id:'a'.repeat(64),user_text:current.note_text,delivery_state:'pending',remote_state:'unknown',task_id:null});
    return Response.json(current);
  }) as typeof fetch);
  const created=await client.create(reference,req);
  const saved=await client.save(created,'我自己的原话\n',req);
  const result=await client.submit(saved);
  assert.equal(result.delivery_state,'pending');
  assert.equal(result.remote_state,'unknown');
  const inputs=calls.map(c=>JSON.parse(c.init.body));
  assert.deepEqual(inputs[1],{request_id:req,expected_context_version:1,note_text:'我自己的原话\n'});
  assert.deepEqual(inputs[2],{context_version:2,note_sha256:noteHash('我自己的原话\n')});
  for(const call of calls){assert.equal(call.init.headers['X-AhaDiff-Token'],'fixture-viewer');assert.equal(call.init.headers.Authorization,undefined);assert.equal(call.init.headers.Origin,'http://127.0.0.1:3210');assert.equal(call.init.redirect,'error');}
});

test('viewer rejects forged hashes, wrong contexts, changed entry and false successful submissions',async()=>{
  for(const raw of [null,{...fixture(),context_id:req},{...fixture(),note_sha256:'0'.repeat(64)},
    {...fixture(),entry:{...fixture().entry,body:'tampered'}}]){
    const client=new LearningViewerClient('http://127.0.0.1:3210','fixture',(async()=>Response.json(raw)) as typeof fetch);
    await assert.rejects(client.read(id),/VIEWER_CONTEXT_UNVERIFIED/);
  }
  const client=new LearningViewerClient('http://127.0.0.1:3210','fixture',(async()=>Response.json(fixture())) as typeof fetch);
  await assert.rejects(client.create({...reference,revision:3},req),/VIEWER_REFERENCE_CHANGED/);
  await assert.rejects(client.submit(fixture()),/VIEWER_SUBMISSION_UNVERIFIED/);
});

test('unknown write outcome never retries or exposes transport details',async()=>{
  let calls=0;
  const client=new LearningViewerClient('http://127.0.0.1:3210','fixture',(async()=>{calls++;throw Error('private transport details');}) as typeof fetch);
  await assert.rejects(client.create(reference,req),/VIEWER_WRITE_UNCONFIRMED_READ_BEFORE_RETRY/);
  assert.equal(calls,1);
});

test('learning commands disabled without explicit identity and reject RPC before fetching',async()=>{
  const prior={url:process.env.ZHIZHI_LEARNING_VIEWER_URL,token:process.env.ZHIZHI_LEARNING_VIEWER_TOKEN,fetch:globalThis.fetch};
  const commands=new Map<string,any>();
  const pi={registerCommand:(name:string,command:any)=>commands.set(name,command)} as unknown as ExtensionAPI;
  try{
    delete process.env.ZHIZHI_LEARNING_VIEWER_URL;delete process.env.ZHIZHI_LEARNING_VIEWER_TOKEN;
    assert.equal(installLearningCommands(pi,s=>s),false);assert.equal(commands.size,0);
    process.env.ZHIZHI_LEARNING_VIEWER_URL='http://127.0.0.1:3210';process.env.ZHIZHI_LEARNING_VIEWER_TOKEN='fixture';
    globalThis.fetch=(async()=>{throw Error('NETWORK_MUST_NOT_RUN');}) as typeof fetch;
    assert.equal(installLearningCommands(pi,s=>s),true);
    assert.equal(commands.size,6);
    for(const command of commands.values()) await assert.rejects(command.handler(id,{mode:'rpc',hasUI:true}),/REQUIRES_LOCAL_TUI/);
  }finally{globalThis.fetch=prior.fetch;for(const [name,value] of [['ZHIZHI_LEARNING_VIEWER_URL',prior.url],['ZHIZHI_LEARNING_VIEWER_TOKEN',prior.token]]){if(value===undefined)delete process.env[name!];else process.env[name!]=value;}}
});

test('only actual editor input is saved; cancellation never writes and submission rereads saved note',async()=>{
  const prior={url:process.env.ZHIZHI_LEARNING_VIEWER_URL,token:process.env.ZHIZHI_LEARNING_VIEWER_TOKEN,fetch:globalThis.fetch};
  const commands=new Map<string,any>(); const writes:any[]=[];const notices:string[]=[];let current=fixture();
  try{
    process.env.ZHIZHI_LEARNING_VIEWER_URL='http://127.0.0.1:3210';process.env.ZHIZHI_LEARNING_VIEWER_TOKEN='fixture';
    globalThis.fetch=(async(url,init)=>{
      if(init?.method==='POST')writes.push(JSON.parse(String(init.body)));
      if(String(url).endsWith('/note'))current=fixture(writes.at(-1).note_text,2);
      if(String(url).endsWith('/understanding/submissions'))return Response.json({...current,submission_id:'b'.repeat(64),user_text:current.note_text,delivery_state:'pending',remote_state:'unknown',task_id:null});
      return Response.json(current);
    }) as typeof fetch;
    installLearningCommands({registerCommand:(name:string,command:any)=>commands.set(name,command)} as unknown as ExtensionAPI,s=>s);
    const ctx={mode:'tui',hasUI:true,ui:{editor:async()=>undefined,confirm:async()=>true,notify:(s:string)=>notices.push(s)}} as unknown as ExtensionCommandContext;
    await commands.get('zhizhi-learn-note').handler(id,ctx);assert.equal(writes.length,0);
    const editing={...ctx,ui:{...ctx.ui,editor:async()=>'真实编辑器原话'}};
    await commands.get('zhizhi-learn-note').handler(id,editing);
    assert.equal(writes[0].note_text,'真实编辑器原话');
    await commands.get('zhizhi-learn-submit').handler(id,ctx);
    assert.deepEqual(writes[1],{context_version:2,note_sha256:noteHash('真实编辑器原话')});
    assert.match(notices.at(-1)!,/不推定正式提交或公开/);
  }finally{globalThis.fetch=prior.fetch;for(const [name,value] of [['ZHIZHI_LEARNING_VIEWER_URL',prior.url],['ZHIZHI_LEARNING_VIEWER_TOKEN',prior.token]]){if(value===undefined)delete process.env[name!];else process.env[name!]=value;}}
});

test('malformed write receipts and version conflicts never look successful or leak server details',async()=>{
  const malformed=new LearningViewerClient('http://localhost:3210','fixture',(async()=>new Response('not-json')) as typeof fetch);
  await assert.rejects(malformed.create(reference,req),/VIEWER_WRITE_UNCONFIRMED_READ_BEFORE_RETRY/);
  const conflict=new LearningViewerClient('http://localhost:3210','fixture',(async()=>Response.json({error:{code:'CONTEXT_VERSION_CONFLICT',message:'private note contents'}},{status:409})) as typeof fetch);
  await assert.rejects(conflict.save(fixture(),'replacement',req),error=>{
    assert.match(String(error),/CONTEXT_VERSION_CONFLICT.*409/);assert.doesNotMatch(String(error),/private note contents/);return true;
  });
});

test('same context cannot double-submit concurrently; cancel after reread has no write',async()=>{
  const prior={url:process.env.ZHIZHI_LEARNING_VIEWER_URL,token:process.env.ZHIZHI_LEARNING_VIEWER_TOKEN,fetch:globalThis.fetch};
  const commands=new Map<string,any>();let calls=0;let release!:()=>void;
  const ready=new Promise<void>(resolve=>release=resolve);
  try{
    process.env.ZHIZHI_LEARNING_VIEWER_URL='http://localhost:3210';process.env.ZHIZHI_LEARNING_VIEWER_TOKEN='fixture';
    globalThis.fetch=(async()=>{calls++;await ready;return Response.json(fixture());}) as typeof fetch;
    installLearningCommands({registerCommand:(name:string,command:any)=>commands.set(name,command)} as unknown as ExtensionAPI,s=>s);
    const ctx={mode:'tui',hasUI:true,ui:{confirm:async()=>false}};
    const first=commands.get('zhizhi-learn-submit').handler(id,ctx);
    await assert.rejects(commands.get('zhizhi-learn-submit').handler(id,ctx),/VIEWER_OPERATION_IN_PROGRESS/);
    release();await first;assert.equal(calls,1);
  }finally{release();globalThis.fetch=prior.fetch;for(const [name,value] of [['ZHIZHI_LEARNING_VIEWER_URL',prior.url],['ZHIZHI_LEARNING_VIEWER_TOKEN',prior.token]]){if(value===undefined)delete process.env[name!];else process.env[name!]=value;}}
});

test('note receipt revision must exactly stay unchanged or advance by one, never stale or jumped',async()=>{
  for(const responseVersion of [1,3,9]){
    const client=new LearningViewerClient('http://localhost:3210','fixture',(async()=>Response.json(fixture('changed',responseVersion))) as typeof fetch);
    await assert.rejects(client.save(fixture(),'changed',req),/VIEWER_NOTE_UNCONFIRMED/);
  }
  const unchanged=new LearningViewerClient('http://localhost:3210','fixture',(async()=>Response.json(fixture())) as typeof fetch);
  assert.equal((await unchanged.save(fixture(),fixture().note_text,req)).context_version,1);
  const spurious=new LearningViewerClient('http://localhost:3210','fixture',(async()=>Response.json(fixture(fixture().note_text,2))) as typeof fetch);
  await assert.rejects(spurious.save(fixture(),fixture().note_text,req),/VIEWER_NOTE_UNCONFIRMED/);
  let calls=0;
  const overflow=new LearningViewerClient('http://localhost:3210','fixture',(async()=>{calls++;return Response.json(fixture());}) as typeof fetch);
  await assert.rejects(overflow.save(fixture('old',Number.MAX_SAFE_INTEGER),'new',req),/VIEWER_NOTE_INVALID/);
  assert.equal(calls,0);
});

const planFixture=()=>({source_reference:reference,focus:'architecture',plan_hash:'c'.repeat(64),title:'固定版本',question:'架构边界与取舍？',policy_version:'architecture-stack-v1',authorship:'deterministic_template_not_user_answer',state:'preview',published:false});
const acceptedFixture=()=>({source_reference:reference,plan_hash:'c'.repeat(64),suggestion_id:'tsk_'+'1'.repeat(32),learning_id:'tsk_'+'2'.repeat(32),suggestion_state:'accepted',published:false});
test('architecture plan is shown before explicit acceptance; cancel and invalid focus never enroll',async()=>{
  const prior={url:process.env.ZHIZHI_LEARNING_VIEWER_URL,token:process.env.ZHIZHI_LEARNING_VIEWER_TOKEN,fetch:globalThis.fetch};
  const commands=new Map<string,any>();const calls:any[]=[];const notices:string[]=[];let approve=false;
  try{
    process.env.ZHIZHI_LEARNING_VIEWER_URL='http://localhost:3210';process.env.ZHIZHI_LEARNING_VIEWER_TOKEN='fixture';
    globalThis.fetch=(async(url,init)=>{assert.equal((init?.headers as any).Origin,'http://localhost:3210');calls.push({url:String(url),payload:JSON.parse(String(init?.body))});return Response.json(String(url).endsWith('/accept')?acceptedFixture():planFixture());}) as typeof fetch;
    installLearningCommands({registerCommand:(name:string,command:any)=>commands.set(name,command)} as unknown as ExtensionAPI,s=>s);
    const ctx={mode:'tui',hasUI:true,ui:{confirm:async(_title:string,body:string)=>{assert.match(body,/不是本人回答/);assert.match(body,/plan-status/);assert.match(body,/架构边界与取舍/);return approve;},notify:(message:string)=>notices.push(message)}};
    const args=`--entry entry-1 --revision 2 --hash ${reference.expected_content_hash} --focus architecture`;
    await commands.get('zhizhi-learn-plan').handler(args,ctx);
    assert.equal(calls.length,1);assert.match(calls[0].url,/preview$/);
    await assert.rejects(commands.get('zhizhi-learn-plan').handler(args.replace('architecture','syntax'),ctx),/EXPECTED_EXACT/);
    assert.equal(calls.length,1);
    approve=true;await commands.get('zhizhi-learn-plan').handler(args,ctx);
    assert.deepEqual(calls.at(-1).payload,{...reference,focus:'architecture',plan_hash:'c'.repeat(64)});
    assert.equal(calls.filter(x=>x.url.endsWith('/accept')).length,1);
    assert.equal(calls.some(x=>'user_text' in x.payload),false);
    assert.match(notices.at(-1)!,/#\/growth\?view=radar&radar=tsk_/);
  }finally{globalThis.fetch=prior.fetch;for(const [name,value] of [['ZHIZHI_LEARNING_VIEWER_URL',prior.url],['ZHIZHI_LEARNING_VIEWER_TOKEN',prior.token]]){if(value===undefined)delete process.env[name!];else process.env[name!]=value;}}
});

test('plans reject source swaps and forged acceptance; timeout is not retried and status is read only',async()=>{
  for(const raw of [{...planFixture(),source_reference:{...reference,revision:3}}, {...planFixture(),authorship:'user'}]){
    const client=new LearningViewerClient('http://localhost:3210','fixture',(async()=>Response.json(raw)) as typeof fetch);
    await assert.rejects(client.previewPlan(reference),/PLAN_UNVERIFIED/);
  }
  const client=new LearningViewerClient('http://localhost:3210','fixture',(async()=>Response.json({...acceptedFixture(),source_reference:{...reference,revision:3}})) as typeof fetch);
  await assert.rejects(client.acceptPlan(planFixture() as any),/ACCEPTANCE_UNVERIFIED/);
  let calls=0;
  const failed=new LearningViewerClient('http://localhost:3210','fixture',(async()=>{calls++;throw Error('offline');}) as typeof fetch);
  await assert.rejects(failed.acceptPlan(planFixture() as any),/WRITE_UNCONFIRMED/);assert.equal(calls,1);
  const status=new LearningViewerClient('http://localhost:3210','fixture',(async(_url,init)=>{assert.equal(init?.method,'GET');return Response.json({plan_hash:'c'.repeat(64),delivery_state:'pending',receipt:null});}) as typeof fetch);
  assert.equal((await status.planStatus('c'.repeat(64))).delivery_state,'pending');
});
