import { isBackendApiRoute } from '../fixtures/api-routes';
import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import type { UnderstandingSubmission } from '../../src/api/understanding';

test.use({launchOptions:{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}});
const submissionId='1'.repeat(64);
const otherCardId='22222222-2222-4222-8222-222222222222';
const cardId='11111111-1111-4111-8111-111111111111';
const root='/api/growth/local/knowledge';
const card={card_id:cardId,concept_id:'concept-1',title:'事务边界学习',front_question:'何时提交，失败如何恢复？',back_answer:'AI 示例讲解，不能代替本人观点。',back_explanation:'事务负责原子性。',card_version:1,followup_mark:'none',learning_stage:'back',project_ids:['p1'],source_commit_shas:['abc123']};
const baseState={projects:[{project_id:'p1',name:'隔离测试项目',local_processing:true,model_allowed:false,cloud_allowed:false,created_at:'2026-01-01',last_publish_error:null}],bindings:[{binding_id:'b1',project_id:'p1',canonical_local_path:'/fixture',provider_names:[],created_at:'2026-01-01'}],features:[{feature_id:'f1',project_id:'p1',label:'限定范围',base_ref:'main'}],snapshots:[],analysis_runs:[],opportunities:[],topic_proposals:[],growth_tasks:[],local_topics:[],local_topic_projects:[],local_explorations:[]};
async function serve(page:Page,options:{failIndex?:boolean;empty?:boolean;failSave?:boolean;writeback?:boolean;streamInterrupted?:boolean;bridge?:'unavailable'|'candidate'|'conflict'|'lost'|'retry'|'delayed';noteText?:string;secondCard?:boolean}={}) {
  let note=options.noteText??'本人已保存的原话';let saves=0;let publishes=0;const requests:string[]=[];const decisions:unknown[]=[];const completions:unknown[]=[];const chats:Record<string,unknown>[]=[];
  const submissionBodies:unknown[]=[];let submission:UnderstandingSubmission|null=null;let committed=false;let releaseSubmission=()=>{};
  const submissionsPath=root+'/cards/'+cardId+'/understanding/submissions';
  const conversation=[{role:'user',content_text:'此前我问过事务边界'},{role:'assistant',content_text:'此前会话中的辅助解释'}];
  const methods:Record<string,string[]>={
    '/api/auth/token':['POST'],'/api/locale':['GET'],'/api/growth/local':['GET'],[root]:['GET'],
    [root+'/cards/'+otherCardId]:['GET'],[root+'/cards/'+cardId]:['GET'],[root+'/cards/'+cardId+'/note']:['PUT'],[root+'/cards/'+cardId+'/mark']:['PUT'],
    [root+'/cards/'+cardId+'/chat/stream']:['POST'],[root+'/wiki/drafts/d1']:['PUT'],[root+'/wiki/drafts/d1/publish']:['POST'],
    '/api/growth/local/bindings/b1/commits':['GET'],[root+'/commits/abc123']:['GET'],
    [root+'/cards/'+otherCardId+'/understanding/submissions']:['GET'],[submissionsPath]:['GET','POST'],[submissionsPath+'/'+submissionId]:['GET'],
    [root+'/radar/runs']:['POST'],[root+'/radar/r1/decision']:['POST'],[root+'/radar/r1']:['GET'],[root+'/radar/r1/complete']:['POST'],
  };
  const draft=options.writeback?{draft_id:'d1',card_id:cardId,markdown:'新的边界说明',original_markdown:'旧的边界说明',status:'draft'}:null;
  await page.route(isBackendApiRoute,async route=>{
    const request=route.request();const url=new URL(request.url());requests.push(url.pathname+url.search);
    expect(methods[url.pathname], 'Fixture must declare '+url.pathname).toBeDefined();
    expect(methods[url.pathname], 'HTTP contract for '+url.pathname).toContain(request.method());
    const json=(body:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if(url.pathname==='/api/auth/token')return json({token:'fixture-token'});
    if(url.pathname==='/api/locale')return json({locale:'zh-CN'});
    if(url.pathname==='/api/growth/local')return json(options.empty?{...baseState,projects:[],bindings:[],features:[]}:options.streamInterrupted?{...baseState,projects:[{...baseState.projects[0],model_allowed:true}],bindings:[{...baseState.bindings[0],provider_names:['fixture-model']}]}:baseState);
    if(url.pathname===root)return options.failIndex?json({error:'fixture unavailable'},503):json({knowledge_mode:'unified',cards:options.empty?[]:options.secondCard?[{...card,card_id:otherCardId,title:'列表排在前面的另一张卡'},card]:[card],candidates:[],wiki_articles:[],vault_path:''});
    if(url.pathname===root+'/cards/'+otherCardId)return json({card:{...card,card_id:otherCardId,title:'列表排在前面的另一张卡'},learning:{stage:'back',note_text:'另一张卡的原话',answers:{}},sources:[],questions:[],wiki_draft:null,conversation:[]});
    if(url.pathname===root+'/cards/'+cardId)return json({card,learning:{stage:'back',note_text:note,answers:{}},sources:[{source_ref_id:'source1',relative_path:'service.ts',commit_sha:'abc123',project_id:'p1',excerpt:'事务证据'}],questions:[],wiki_draft:draft,conversation});
    if(url.pathname===root+'/cards/'+otherCardId+'/understanding/submissions')return json({items:[],has_more:false});
    if(url.pathname===submissionsPath){
      if(options.bridge==='unavailable')return json({error:'sentinel-private-upstream-secret'},404);
      if(request.method()==='GET')return json({items:submission?[submission]:[],has_more:false});
      const body=request.postDataJSON();submissionBodies.push(body);
      const submittedText=submission && submission.note_sha256===body.note_sha256?submission.user_text:note;
      expect(body).toEqual({card_version:1,note_sha256:createHash('sha256').update(submittedText,'utf8').digest('hex')});
      if(options.bridge==='conflict')return json({error:'stale_saved_note'},409);
      submission={submission_id:submissionId,card_id:cardId,card_version:1,note_sha256:body.note_sha256,user_text:submittedText,delivery_state:'pending',outcome_id:null,task_id:null,remote_state:'not_submitted',task:null,change:null,error:null};
      if(options.bridge==='lost')return route.abort('failed');
      if(options.bridge==='retry')submission=submissionBodies.length===1?{...submission,remote_state:'unknown',error:{code:'NETWORK_ERROR',retryable:true}}:{...submission,delivery_state:'acknowledged',remote_state:'available',outcome_id:'outcome-1',task_id:'generation-1',task:{task_id:'generation-1',kind:'knowledge.generate',state:'queued'}};
      if(options.bridge==='delayed')await new Promise<void>(resolve=>{releaseSubmission=resolve;});else await new Promise(resolve=>setTimeout(resolve,120));return json(submission,202);
    }
    if(url.pathname===submissionsPath+'/'+submissionId){
      expect(submission).not.toBeNull();
      if(options.bridge==='candidate')submission={...submission!,delivery_state:'acknowledged',outcome_id:'outcome-1',task_id:'generation-1',remote_state:'available',task:{task_id:'generation-1',kind:'knowledge.generate',state:'succeeded'},change:{change_id:'understanding-change-1',state:committed?'committed':'needs_ai_review',entry_id:'entry-1',base_revision:3,committed_revision:committed?4:null,title:'从原话整理的事务边界候选',path:'architecture/transactions.md',original_markdown:'旧版只说明原子性',candidate_markdown:'新版加入重试边界与本人实践',evidence_refs:[{source_id:'source1',learning_outcome_id:'outcome-1',revision:1}]}};
      return json(submission);
    }
    if(url.pathname===root+'/cards/'+cardId+'/note'){saves++;if(options.failSave)return json({error:'保存未确认'},503);note=request.postDataJSON().text;await new Promise(resolve=>setTimeout(resolve,120));return json({note_text:note});}
    if(url.pathname===root+'/cards/'+cardId+'/chat/stream'){
      expect(options.streamInterrupted).toBe(true);chats.push(request.postDataJSON());
      const body=chats.length===1?'event: delta\ndata: '+JSON.stringify('仅收到一半的 AI 回复')+'\n\n':'event: done\ndata: '+JSON.stringify({conversation:[...conversation,{role:'user',content_text:chats.at(-1)?.message},{role:'assistant',content_text:'重试确认的完整回复'}]})+'\n\n';
      return route.fulfill({status:200,contentType:'text/event-stream',body});
    }
    if(url.pathname===root+'/cards/'+cardId+'/mark')return json({mark:request.postDataJSON().mark});
    if(url.pathname===root+'/wiki/drafts/d1'&&request.method()==='PUT')return json({saved:true});
    if(url.pathname===root+'/wiki/drafts/d1/publish'){publishes++;await new Promise(resolve=>setTimeout(resolve,120));return json({state:'submitted',published:false,change_id:'change-1',task_id:'task-1'},202);}
    if(url.pathname==='/api/growth/local/bindings/b1/commits')return json({commits:[{sha:'abc123',title:'明确事务边界',paths:['service.ts'],authored_at:'2026-01-01'}],base_sha:'base',next_cursor:null,authors:[]});
    if(url.pathname===root+'/commits/abc123')return json({title:'明确事务边界',patch:'-旧实现\n+新的事务边界'});
    if(url.pathname===root+'/radar/runs')return json({suggestions:[{suggestion_id:'r1',proposed_exercise:'解释幂等边界',reason:'真实待验证问题',suggestion_state:'proposed'}]});
    if(url.pathname===root+'/radar/r1/decision'){decisions.push(request.postDataJSON());expect(request.postDataJSON()).toEqual({decision:'accept'});return json({suggestion_id:'r1',suggestion_state:'accepted',learning_id:'learning1'});}
    if(url.pathname===root+'/radar/r1')return json({suggestion_id:'r1',suggestion_state:'accepted',practice:{entry_id:'entry1',title:'幂等边界',body:'已有知识证据',question:'重试何时安全？'}});
    if(url.pathname===root+'/radar/r1/complete'){completions.push(request.postDataJSON());return json({state:'pending',published:false,suggestion_id:'r1'});}
    return json({error:'fixture route not implemented: '+url.pathname},404);
  });
  return {saves:()=>saves,publishes:()=>publishes,requests,decisions,completions,chats,submissionBodies,releaseSubmission:()=>releaseSubmission(),confirmWrite:()=>{committed=true;}};
}
test('专域首页与空态不制造设计示例数据',async({page})=>{await serve(page,{empty:true});await page.goto('/#/growth?view=home');await expect(page.getByRole('heading',{name:'继续你的工程学习'})).toBeVisible();await expect(page.getByText('从自己的项目或笔记开始')).toBeVisible();const nav=page.locator('#growth-navigation');await expect(nav.getByRole('link')).toHaveCount(6);await expect(nav).not.toContainText('收件箱');await expect(nav).not.toContainText('重要确认');await expect(page.getByText('SQLite 单写与 Git CAS')).toHaveCount(0);});
test('API 错误不是空成功',async({page})=>{await serve(page,{failIndex:true});await page.goto('/#/growth?view=library');await expect(page.getByRole('alert')).toContainText('读取未完成');await expect(page.getByText('还没有符合条件的材料')).toHaveCount(0);});
test('学习主题设置保留旧深链接，学习库也有明确入口',async({page})=>{await serve(page);await page.goto('/#/growth?view=settings');await expect(page.getByRole('heading',{name:'学习主题管理',exact:true})).toBeVisible();await expect(page.locator('#growth-cloud-url')).toBeVisible();await page.getByRole('link',{name:'返回学习库'}).click();await page.getByRole('link',{name:'学习主题管理',exact:true}).click();await expect(page.getByRole('heading',{name:'学习主题管理',exact:true})).toBeVisible();});
test('保存本人原话，双击只一次，AI 讲解不代写',async({page})=>{const calls=await serve(page);await page.goto('/#/growth?view=session&card='+cardId);const answer=page.getByRole('textbox',{name:'我的理解',exact:true});await expect(answer).toHaveValue('本人已保存的原话');await answer.fill('这是我自己的架构取舍');await page.getByRole('button',{name:'保存我的理解',exact:true}).dblclick();await expect(page.getByText('本人原话已保存到本机学习记录，未自动回填或公开。')).toBeVisible();expect(calls.saves()).toBe(1);await expect(answer).toHaveValue('这是我自己的架构取舍');await expect(page.getByRole('button',{name:'发送追问'})).toBeDisabled();});
test('保存失败与导航返回都保留本人草稿',async({page})=>{await serve(page,{failSave:true});await page.goto('/#/growth?view=session&card='+cardId);await page.getByRole('textbox',{name:'我的理解',exact:true}).fill('需要保留的本机草稿');await page.getByRole('button',{name:'保存我的理解',exact:true}).click();await expect(page.getByRole('alert')).toContainText('输入仍保留');await page.getByRole('link',{name:'返回学习库'}).click();await page.goBack();await expect(page.getByRole('textbox',{name:'我的理解',exact:true})).toHaveValue('需要保留的本机草稿');});
test('开放理解服务不可用时明确阻止提交，不伪造已完成或回显秘密',async({page})=>{const calls=await serve(page,{bridge:'unavailable'});await page.goto('/#/growth?view=writeback&card='+cardId);await expect(page.getByRole('button',{name:'生成知识候选',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'提交理解，生成并复核候选',exact:true})).toBeDisabled();await expect(page.getByRole('alert')).toContainText('操作未获确认');await expect(page.locator('body')).not.toContainText('sentinel-private-upstream-secret');expect(calls.submissionBodies).toEqual([]);});
test('正式候选提交只显示待复核，不冒称已公开',async({page})=>{const calls=await serve(page,{writeback:true});await page.goto('/#/growth?view=writeback&card='+cardId);await expect(page.getByLabel('正式知识与候选差异')).toContainText('新的边界说明');await page.getByRole('button',{name:'提交学习成果'}).dblclick();await expect(page.getByText('已提交复核，尚未确认正式写入。')).toBeVisible();expect(calls.publishes()).toBe(1);await expect(page.getByText('change_id: change-1')).toBeVisible();});
test('项目读取限定范围，不发起全历史扫描或模型发送',async({page})=>{const calls=await serve(page);await page.goto('/#/growth?view=projects&project=p1&commit=abc123');await expect(page.getByText('新的事务边界',{exact:false})).toBeVisible();expect(calls.requests.some(url=>url.includes('scan=true'))).toBe(false);expect(calls.requests.some(url=>url.endsWith('/analysis'))).toBe(false);await expect(page.getByRole('button',{name:'职责演进图（接口待接入）'})).toBeDisabled();});
test('雷达接受后只提交真实原话，离线显示待发',async({page})=>{const calls=await serve(page);await page.goto('/#/growth?view=radar');await page.getByRole('button',{name:'查看学习建议'}).click();await page.getByRole('button',{name:'开始练习'}).click();await page.getByRole('textbox',{name:'用自己的话说明架构取舍或应用思路'}).fill('我会用请求标识保证重试幂等');await page.getByRole('button',{name:'提交学习原话'}).click();await expect(page.getByText('已保存在本机，等待连接恢复后提交。')).toBeVisible();expect(calls.decisions).toEqual([{decision:'accept'}]);expect(calls.completions).toEqual([{user_text:'我会用请求标识保证重试幂等'}]);});

test('首页与学习库继续同一卡片的会话和本人草稿',async({page})=>{
  const calls=await serve(page,{secondCard:true});await page.goto('/#/growth?view=home');
  await page.getByRole('link',{name:'事务边界学习',exact:true}).click();
  await expect(page).toHaveURL(new RegExp('view=session.*card='+cardId));
  await expect(page.getByText('此前我问过事务边界',{exact:true})).toBeVisible();
  const answer=page.getByRole('textbox',{name:'我的理解',exact:true});await answer.fill('尚未保存但仍属于本人的解释');
  await page.getByRole('link',{name:'返回学习库'}).click();
  await page.locator('article').filter({has:page.getByRole('link',{name:'事务边界学习',exact:true})}).getByRole('link',{name:'继续学习',exact:true}).click();
  await expect(page).toHaveURL(new RegExp('view=session.*card='+cardId));
  await expect(answer).toHaveValue('尚未保存但仍属于本人的解释');
  await expect(page.getByText('此前会话中的辅助解释',{exact:true})).toBeVisible();
  await page.locator('#growth-navigation').getByRole('link',{name:'首页',exact:true}).click();
  await page.getByRole('link',{name:'继续学习',exact:true}).click();
  await expect(page).toHaveURL(new RegExp('view=session.*card='+cardId));
  await expect(answer).toHaveValue('尚未保存但仍属于本人的解释');expect(calls.saves()).toBe(0);
});
test('流提前中断保留原话与提问，重试同一请求不伪造完整回复',async({page})=>{
  const calls=await serve(page,{streamInterrupted:true});await page.goto('/#/growth?view=session&card='+cardId);
  const answer=page.getByRole('textbox',{name:'我的理解',exact:true});await answer.fill('本人观点不能被 AI 片段覆盖');
  const question=page.getByRole('textbox',{name:'继续提问',exact:true});await question.fill('失败重试如何保持原子性？');
  await page.getByRole('button',{name:'发送追问'}).click();
  await expect(page.getByRole('alert')).toContainText('回复连接提前结束');
  await expect(page.getByText('AI 回复片段 · 未确认完成',{exact:true})).toBeVisible();
  await expect(page.getByText('仅收到一半的 AI 回复',{exact:true})).toBeVisible();
  await expect(answer).toHaveValue('本人观点不能被 AI 片段覆盖');
  await expect(question).toHaveValue('失败重试如何保持原子性？');
  await page.getByRole('button',{name:'发送追问'}).click();
  await expect(page.getByText('重试确认的完整回复',{exact:true})).toBeVisible();
  await expect(page.getByText('AI 回复片段 · 未确认完成',{exact:true})).toHaveCount(0);
  await expect(question).toHaveValue('');await expect(answer).toHaveValue('本人观点不能被 AI 片段覆盖');
  expect(calls.chats).toHaveLength(2);expect(calls.chats[0]).toEqual({request_id:expect.any(String),provider_name:'fixture-model',message:'失败重试如何保持原子性？',stage:'back'});
  expect(calls.chats[1]).toEqual(calls.chats[0]);expect(calls.saves()).toBe(0);
});

test('本人原话提交到任务与只读候选差异，刷新恢复不重复提交',async({page})=>{
  const note='  本人的完整原话\n保留边界与空格  ';const calls=await serve(page,{bridge:'candidate',noteText:note});
  await page.goto('/#/growth?view=writeback&card='+cardId);
  const submit=page.getByRole('button',{name:'提交理解，生成并复核候选',exact:true});await expect(submit).toBeEnabled();await submit.dblclick();
  await expect(page.getByText('已保存到本机待发，尚未确认服务受理。',{exact:true})).toBeVisible();
  expect(calls.submissionBodies).toHaveLength(1);await expect(submit).toBeDisabled();
  await expect(page.getByText('已确认正式写入，不代表公开。',{exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'刷新理解提交状态',exact:true}).click();
  await expect(page.getByText('候选等待独立复核，尚未确认正式写入。',{exact:true})).toBeVisible();
  const diff=page.getByLabel('本人理解候选与基线差异');await expect(diff).toContainText('旧版只说明原子性');await expect(diff).toContainText('新版加入重试边界与本人实践');
  const tabs=page.getByRole('group',{name:'理解候选内容视图'});await tabs.getByRole('button',{name:'候选内容',exact:true}).click();
  await expect(page.getByLabel('只读理解候选')).toHaveText('新版加入重试边界与本人实践');await expect(page.getByRole('textbox',{name:'候选草稿'})).toHaveCount(0);
  await tabs.getByRole('button',{name:'引用与证据',exact:true}).click();await expect(page.getByLabel('理解候选授权引用')).toContainText('来源 ID：source1');await expect(page.getByLabel('理解候选授权引用')).toContainText('理解成果 ID：outcome-1');
  calls.confirmWrite();await page.getByRole('button',{name:'刷新理解提交状态',exact:true}).click();
  await expect(page.getByText('已确认正式写入，不代表公开。',{exact:true})).toBeVisible();
  await expect(page.getByText('公开状态：此接口未提供，不推定已公开。',{exact:true})).toBeVisible();
  await page.reload();await expect(submit).toBeDisabled();await expect(page.getByText('已确认正式写入，不代表公开。',{exact:true})).toBeVisible();
  expect(calls.submissionBodies).toEqual([{card_version:1,note_sha256:createHash('sha256').update(note,'utf8').digest('hex')}]);
  expect(calls.requests.some(path=>path.includes('/radar/')||path.endsWith('/wiki/draft')||path.endsWith('/complete'))).toBe(false);
});
test('保存版本冲突阻止理解提交，先回读而非自动重试',async({page})=>{
  const calls=await serve(page,{bridge:'conflict'});await page.goto('/#/growth?view=writeback&card='+cardId);
  const submit=page.getByRole('button',{name:'提交理解，生成并复核候选',exact:true});await expect(submit).toBeEnabled();await submit.click();
  await expect(page.getByRole('alert')).toContainText('不会自动重新提交');await expect(submit).toBeDisabled();
  await expect(page.getByText('本人已保存的原话',{exact:true})).toBeVisible();expect(calls.submissionBodies).toHaveLength(1);
  await page.getByRole('button',{name:'刷新理解提交状态',exact:true}).click();await expect(submit).toBeEnabled();expect(calls.submissionBodies).toHaveLength(1);
  await expect(page.getByLabel('本人理解候选与基线差异')).toHaveCount(0);
});
test('提交响应丢失后通过 GET 恢复待发事实，不重复 POST',async({page})=>{
  const calls=await serve(page,{bridge:'lost'});await page.goto('/#/growth?view=writeback&card='+cardId);
  const submit=page.getByRole('button',{name:'提交理解，生成并复核候选',exact:true});await expect(submit).toBeEnabled();await submit.click();
  await expect(page.getByRole('alert')).toContainText('操作未获确认');await expect(submit).toBeDisabled();
  await page.getByRole('button',{name:'刷新理解提交状态',exact:true}).click();
  await expect(page.getByText('已保存到本机待发，尚未确认服务受理。',{exact:true})).toBeVisible();await expect(submit).toBeDisabled();
  expect(calls.submissionBodies).toHaveLength(1);await expect(page.getByText('已确认正式写入，不代表公开。',{exact:true})).toHaveCount(0);
});

test('显式重试只递送此前待发原话，不拿后来修改的理解替换',async({page})=>{
  const calls=await serve(page,{bridge:'retry'});await page.goto('/#/growth?view=writeback&card='+cardId);
  const submit=page.getByRole('button',{name:'提交理解，生成并复核候选',exact:true});await expect(submit).toBeEnabled();await submit.click();
  await expect(page.getByRole('button',{name:'重试递送这份原话',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'补充我的理解',exact:true}).click();
  await page.getByRole('textbox',{name:'我的理解',exact:true}).fill('后来保存的理解，不能覆盖此前提交');
  await page.getByRole('button',{name:'保存我的理解',exact:true}).click();
  await expect(page.getByText('本人原话已保存到本机学习记录，未自动回填或公开。')).toBeVisible();
  await page.getByRole('link',{name:'查看成果回填',exact:true}).click();
  await expect(page.getByText('本次候选对应此前提交的原话，不会覆盖当前已保存的理解。',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'重试递送这份原话',exact:true}).click();
  await expect(page.getByText('已受理，等待生成或读取候选；尚未确认正式写入。',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'重试递送这份原话',exact:true})).toHaveCount(0);
  await expect(page.getByText('后来保存的理解，不能覆盖此前提交',{exact:true})).toBeVisible();
  expect(calls.submissionBodies).toHaveLength(2);expect(calls.submissionBodies[1]).toEqual(calls.submissionBodies[0]);
  expect(calls.submissionBodies[0]).toEqual({card_version:1,note_sha256:createHash('sha256').update('本人已保存的原话','utf8').digest('hex')});expect(calls.saves()).toBe(1);
});

test('理解提交迟到响应不能覆盖后来打开的另一张卡',async({page})=>{
  const calls=await serve(page,{bridge:'delayed',secondCard:true});await page.goto('/#/growth?view=writeback&card='+cardId);
  const submit=page.getByRole('button',{name:'提交理解，生成并复核候选',exact:true});await expect(submit).toBeEnabled();await submit.click();
  await expect.poll(()=>calls.submissionBodies.length).toBe(1);
  await page.locator('#growth-navigation').getByRole('link',{name:'学习库',exact:true}).click();
  await page.getByRole('link',{name:'列表排在前面的另一张卡',exact:true}).click();
  await page.getByRole('link',{name:'查看成果回填',exact:true}).click();
  await expect(page.getByText('另一张卡的原话',{exact:true})).toBeVisible();await expect(submit).toBeEnabled();
  const response=page.waitForResponse(value=>value.url().endsWith('/cards/'+cardId+'/understanding/submissions')&&value.request().method()==='POST');
  calls.releaseSubmission();const delivered=await response;await delivered.finished();
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page).toHaveURL(new RegExp('view=writeback.*card='+otherCardId));
  await expect(page.getByText('另一张卡的原话',{exact:true})).toBeVisible();
  await expect(page.getByText('submission_id: '+submissionId,{exact:false})).toHaveCount(0);
  await expect(submit).toBeEnabled();expect(calls.submissionBodies).toHaveLength(1);
});
