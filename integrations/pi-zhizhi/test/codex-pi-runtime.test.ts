/** Actual locked Pi 1.3.0 orchestration; all model/domain results are explicitly synthetic. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join,dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

for (const mode of ['rpc', 'json']) test(`real pinned Pi ${mode} completes two synthetic model turns and one actual domain-tool execution`, {timeout:25000}, async () => {
  const directory=await mkdtemp(join(tmpdir(),'zhizhi-native-provider-'));
  const cli=join(dirname(fileURLToPath(import.meta.resolve('@earendil-works/pi-coding-agent'))),'cli.js');
  const audit=join(directory,'synthetic-audit.jsonl');
  const child=spawn(process.execPath,[cli,'--mode',mode,'--no-session','--offline','--no-approve','--no-builtin-tools', '--tools', 'zhizhi_intake,zhizhi_job_status,zhizhi_search,zhizhi_read_entry,zhizhi_propose_patch,zhizhi_relations,zhizhi_traverse_relations,zhizhi_review_queue,zhizhi_radar,zhizhi_growth','--no-mcp','--no-extensions','--no-skills','--no-prompt-templates','--no-context-files','--no-themes',
    '--extension',fileURLToPath(new URL('./fixtures/pi-codex-extension.ts',import.meta.url)),'--provider','zhizhi-codex-cli','--model','gpt-6.1-sol'],{
    cwd:directory,env:{PATH:process.env.PATH,HOME:directory,PI_CODING_AGENT_DIR:join(directory,'agent'),PI_OFFLINE:'1',PI_SKIP_VERSION_CHECK:'1',
      ZHIZHI_MODEL_PROVIDER:'zhizhi-codex-cli',ZHIZHI_MODEL_ID:'gpt-6.1-sol',ZHIZHI_AGENT_TOKEN:'synthetic-agent-not-a-credential',ZHIZHI_API_URL:'http://127.0.0.1:8766',ZHIZHI_ALLOWED_CLASSIFICATIONS:'public',ZHIZHI_FIXTURE_AUDIT:audit},stdio:['pipe','pipe','pipe']});
  let stderr=''; let buffer=''; const records:Record<string,any>[]=[];
  child.stderr.on('data',chunk=>{stderr+=chunk.toString();});
  const deadline=setTimeout(()=>child.kill('SIGKILL'),20000);
  try {
    await new Promise<void>((done,reject)=>{
      child.on('error',reject);child.on('close',code=>mode==='json' && code===0 ? done() : reject(new Error(`Pi exited ${code}: ${stderr}; ${JSON.stringify(records)}`)));
      child.stdout.on('data',chunk=>{
        buffer+=chunk.toString(); let end:number;
        while((end=buffer.indexOf('\n'))>=0){
          const line=buffer.slice(0,end); buffer=buffer.slice(end+1);if(!line)continue;
          try {
            const value=JSON.parse(line); records.push(value);
            if(value.type==='response' && value.success===false)reject(new Error(JSON.stringify(value)));
            if(mode==='rpc' && value.type==='agent_settled')done();
          } catch(error){reject(error);}
        }
      });
      if(mode==='rpc') child.stdin.write(JSON.stringify({type:'prompt',id:'synthetic-loop',message:'Synthetic bridge fixture only. Query synthetic evidence once.'})+'\n');
      else child.stdin.end('合成 Pi 验收 v1：请检索“正式正文与原始来源证据”，只查询当前授权知识目录，报告实际匹配条目；不要收藏、批准、修改或发布。');
    });
    if(mode==='rpc') assert.ok(records.some(r=>r.type==='response' && r.id==='synthetic-loop' && r.success===true));
    assert.ok(records.some(r=>r.type==='tool_execution_end' && r.toolName==='zhizhi_search' && r.isError===false),JSON.stringify(records));
    const assistants=records.filter(r=>r.type==='message_end' && r.message?.role==='assistant').map(r=>r.message);
    assert.deepEqual(assistants.map(m=>m.stopReason),['toolUse','stop']);
    assert.equal(assistants[1].content[0].text,'SYNTHETIC_PI_TOOL_LOOP_COMPLETE');
    const lifecycle=records.filter(r=>r.type==='tool_execution_start' || r.type==='tool_execution_end' ||
      (r.type==='message_end' && r.message?.role==='assistant'));
    assert.deepEqual(lifecycle.map(r=>r.type),['message_end','tool_execution_start','tool_execution_end','message_end']);
    assert.equal(lifecycle[1].toolCallId,assistants[0].content[0].id);
    assert.equal(lifecycle[2].toolCallId,assistants[0].content[0].id);
    assert.ok(lifecycle[2].result.details);
    assert.ok(assistants.every(m=>m.provider==='zhizhi-codex-cli' && m.model==='gpt-6.1-sol' && !m.responseModel));
    assert.ok(assistants.every(m=>m.diagnostics[0].details.fixture===true));
    const events=(await readFile(audit,'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.equal(events.filter(e=>e.event==='bridge' && e.request.type==='turn').length,2);
    assert.equal(events.filter(e=>e.event==='domain').length,1);
    assert.ok(!events.some(e=>e.request?.messages?.some((m:{role:string})=>m.role==='system')));
    assert.ok(!/failed to load|extension error|unknown option/i.test(stderr),stderr);
  } finally {
    clearTimeout(deadline);child.stdin.end();
    if(child.exitCode===null && child.signalCode===null){const ended=new Promise<void>(resolve=>child.once('exit',()=>resolve()));child.kill('SIGTERM');await ended;}
    await rm(directory,{recursive:true,force:true});
  }
});


test('real pinned Pi JSON error uses exactly one synthetic bridge turn with production retry policy', {timeout:25000}, async()=>{
 const directory=await mkdtemp(join(tmpdir(),'zhizhi-no-retry-fixture-'));
 const pkg=dirname(fileURLToPath(import.meta.resolve('@earendil-works/pi-coding-agent')));
 const { SettingsManager }=await import(join(pkg,'core/settings-manager.js'));
 const { disableAutomaticRetries }=await import(new URL('../portable/retry-policy.mjs',import.meta.url).href);
 const agent=join(directory,'agent');await mkdir(agent);
 await disableAutomaticRetries(SettingsManager,agent);
 const audit=join(directory,'audit.jsonl');
 const child=spawn(process.execPath,[join(pkg,'cli.js'),'--mode','json','--no-session','--offline','--no-approve','--no-builtin-tools','--no-mcp','--no-extensions','--no-skills','--no-prompt-templates','--no-context-files','--no-themes','--extension',fileURLToPath(new URL('./fixtures/pi-codex-extension.ts',import.meta.url)),'--provider','zhizhi-codex-cli','--model','gpt-6.1-sol'],{cwd:agent,env:{PATH:process.env.PATH,HOME:directory,PI_CODING_AGENT_DIR:agent,PI_OFFLINE:'1',PI_SKIP_VERSION_CHECK:'1',ZHIZHI_MODEL_PROVIDER:'zhizhi-codex-cli',ZHIZHI_MODEL_ID:'gpt-6.1-sol',ZHIZHI_AGENT_TOKEN:'synthetic-agent-not-a-credential',ZHIZHI_API_URL:'http://127.0.0.1:8766',ZHIZHI_ALLOWED_CLASSIFICATIONS:'public',ZHIZHI_FIXTURE_AUDIT:audit,ZHIZHI_FIXTURE_BRIDGE_FAILURE:'timeout'},stdio:['pipe','pipe','pipe']});
 let stdout='';let stderr='';child.stdout.on('data',v=>stdout+=v);child.stderr.on('data',v=>stderr+=v);
 const deadline=setTimeout(()=>child.kill('SIGKILL'),20000);
 try {
  const exited=new Promise<number|null>((resolve,reject)=>{child.once('close',resolve);child.once('error',reject);});
  child.stdin.end('Explicit synthetic timeout fixture only.');
  assert.equal(await exited,0); // Official JSON mode can return zero on assistant error.
  const events=stdout.trim().split('\n').map(v=>JSON.parse(v));
  assert.equal(events.filter(e=>e.type==='auto_retry_start').length,0);
  const assistants=events.filter(e=>e.type==='message_end' && e.message?.role==='assistant');
  assert.equal(assistants.length,1);assert.equal(assistants[0].message.stopReason,'error');
  assert.equal(assistants[0].message.errorMessage,'BRIDGE_TIMEOUT');
  assert.equal(assistants[0].message.diagnostics.find((d:any)=>d.type==='zhizhi.codex_cli.failure').details.stage,'turn');
  const requests=(await readFile(audit,'utf8')).trim().split('\n').map(v=>JSON.parse(v));
  assert.equal(requests.filter(e=>e.event==='bridge' && e.request.type==='hello').length,1);
  assert.equal(requests.filter(e=>e.event==='bridge' && e.request.type==='turn').length,1);
  assert.equal(requests.filter(e=>e.event==='domain').length,0);assert.equal(stderr,'');
 } finally {clearTimeout(deadline);if(child.exitCode===null)child.kill('SIGKILL');await rm(directory,{recursive:true,force:true});}
});
