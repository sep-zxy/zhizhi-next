/** Cloud-only synthetic HTTP fixtures: never real model/account E2E. */
import assert from 'node:assert/strict';
import test from 'node:test';
import type { ExtensionAPI, ExtensionCommandContext } from '@earendil-works/pi-coding-agent';
import extension from '../src/index.ts';
import { DomainClient } from '../src/client.ts';

test('read-only inbox and task tools preserve real page/cursor/status envelopes', async () => {
  const tools = new Map<string, any>();
  const prior = { fetch: globalThis.fetch, agent: process.env.ZHIZHI_AGENT_TOKEN };
  const calls: URL[] = [];
  try {
    process.env.ZHIZHI_AGENT_TOKEN = 'fixture-agent';
    globalThis.fetch = (async (url, init) => {
      calls.push(new URL(String(url))); assert.equal(init?.method, 'GET');
      return Response.json({ data: { items: [], has_more: false, state: 'waiting_quota' }, task_id: null,
        trace_id: 'fixture', data_classification: 'personal' });
    }) as typeof fetch;
    extension({ registerTool: (tool: any) => tools.set(tool.name, tool), registerCommand() {}, on() {} } as unknown as ExtensionAPI);
    for (const [name, params] of [
      ['zhizhi_inbox', {state:'awaiting_keep',limit:10,offset:2}],
      ['zhizhi_intake_preview', {intake_id:'in:1'}],
      ['zhizhi_tasks', {state:'waiting_quota'}],
      ['zhizhi_task_events', {task_id:'task:1',after:5}],
    ] as const) {
      const result = await tools.get(name).execute('fixture-call', params);
      assert.equal(result.details.data.state, 'waiting_quota');
      assert.equal(result.details.task_id, null);
    }
    assert.equal(calls[0].searchParams.get('offset'), '2');
    assert.equal(calls[1].pathname, '/api/knowledge/v1/intakes/in%3A1');
    assert.equal(calls[2].searchParams.get('limit'), '20');
    assert.equal(calls[3].searchParams.get('after'), '5');
    assert.ok(![...tools.keys()].some(name => /resume|cancel/.test(name)));
  } finally {
    globalThis.fetch=prior.fetch;
    if(prior.agent===undefined) delete process.env.ZHIZHI_AGENT_TOKEN; else process.env.ZHIZHI_AGENT_TOKEN=prior.agent;
  }
});

test('task mutations remain human TUI only, revision-bound and explicit', async () => {
  const commands = new Map<string, any>();
  const prior = {fetch:globalThis.fetch, user:process.env.ZHIZHI_USER_TOKEN};
  const writes: any[] = []; const prompts: string[]=[];
  try {
    process.env.ZHIZHI_USER_TOKEN='fixture-user';
    globalThis.fetch=(async (url,init)=>{
      if(init?.method==='POST') writes.push({url:String(url),body:JSON.parse(String(init.body)),headers:init.headers});
      return Response.json({data:{task_id:'task-1',revision:7,state:'waiting_quota'},task_id:'task-1',trace_id:'fixture'});
    }) as typeof fetch;
    extension({registerTool(){},registerCommand:(name:string,command:any)=>commands.set(name,command),on(){}} as unknown as ExtensionAPI);
    const ctx={mode:'tui',hasUI:true,ui:{confirm:async(_title:string,prompt:string)=>{prompts.push(prompt);return true;},notify(){}}} as unknown as ExtensionCommandContext;
    for(const action of ['resume','cancel']) {
      const command=commands.get(`zhizhi-task-${action}`);
      await assert.rejects(command.handler('task-1',{...ctx,mode:'rpc'}), /REQUIRES_LOCAL_TUI/);
      await command.handler('task-1',ctx);
    }
    assert.equal(writes.length,2);
    assert.deepEqual(writes.map(write=>write.body),[{expected_revision:7},{expected_revision:7}]);
    assert.match(writes[0].url,/tasks\/task-1\/resume$/);
    assert.match(prompts[0],/现有身份、模型额度、审批和策略门禁/);
    assert.match(prompts[1],/不撤销已经完成/);
    const cancelled={...ctx,ui:{...ctx.ui,confirm:async()=>false}};
    await commands.get('zhizhi-task-resume').handler('task-1',cancelled);
    assert.equal(writes.length,2);
  } finally {
    globalThis.fetch=prior.fetch;
    if(prior.user===undefined) delete process.env.ZHIZHI_USER_TOKEN; else process.env.ZHIZHI_USER_TOKEN=prior.user;
  }
});

test('bounded list/event routes reject unsupported filters and agent task control', async()=>{
  let calls=0;
  const client=new DomainClient('https://knowledge.example','fixture',false,()=>{},(async()=>{calls++;throw Error('unexpected');}) as typeof fetch);
  for(const path of ['/tasks?owner_id=other','/intakes?limit=101','/tasks?offset=-1',
    '/tasks?state=waiting_quota&state=queued','/tasks/task-1/events?after=1e3','/tasks/task-1/events?limit=0',
    '/intakes?offset=1000001','/tasks/task-1/events?token=secret'])
    await assert.rejects(client.request('read','GET',path),/DOMAIN_ROUTE_FORBIDDEN/);
  for(const action of ['resume','cancel'])
    await assert.rejects(client.request('control','POST',`/tasks/task-1/${action}`,{expected_revision:1},'fixture'),/DOMAIN_ROUTE_FORBIDDEN/);
  assert.equal(calls,0);
});
