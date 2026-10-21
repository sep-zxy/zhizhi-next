import { expect, test, type Page } from '@playwright/test';

test.use({launchOptions:{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}});
const cardId='11111111-1111-4111-8111-111111111111';
const root='/api/growth/local/knowledge';
const card={card_id:cardId,concept_id:'concept-1',title:'事务边界学习',front_question:'何时提交，失败如何恢复？',back_answer:'AI 示例讲解，不能代替本人观点。',back_explanation:'事务负责原子性。',card_version:1,followup_mark:'none',learning_stage:'back',project_ids:['p1'],source_commit_shas:['abc123']};
const baseState={projects:[{project_id:'p1',name:'隔离测试项目',local_processing:true,model_allowed:false,cloud_allowed:false,created_at:'2026-01-01',last_publish_error:null}],bindings:[{binding_id:'b1',project_id:'p1',canonical_local_path:'/fixture',provider_names:[],created_at:'2026-01-01'}],features:[{feature_id:'f1',project_id:'p1',label:'限定范围',base_ref:'main'}],snapshots:[],analysis_runs:[],opportunities:[],topic_proposals:[],growth_tasks:[],local_topics:[],local_topic_projects:[],local_explorations:[]};
async function serve(page:Page,options:{failIndex?:boolean;empty?:boolean;failSave?:boolean;writeback?:boolean}={}) {
  let note='本人已保存的原话';let saves=0;let publishes=0;const requests:string[]=[];
  const draft=options.writeback?{draft_id:'d1',card_id:cardId,markdown:'新的边界说明',original_markdown:'旧的边界说明',status:'draft'}:null;
  await page.route('**/api/**',async route=>{
    const request=route.request();const url=new URL(request.url());requests.push(url.pathname+url.search);
    const json=(body:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    if(url.pathname==='/api/auth/token')return json({token:'fixture-token'});
    if(url.pathname==='/api/locale')return json({locale:'zh-CN'});
    if(url.pathname==='/api/growth/local')return json(options.empty?{...baseState,projects:[],bindings:[],features:[]}:baseState);
    if(url.pathname===root)return options.failIndex?json({error:'fixture unavailable'},503):json({knowledge_mode:'unified',cards:options.empty?[]:[card],candidates:[],wiki_articles:[],vault_path:''});
    if(url.pathname===root+'/cards/'+cardId)return json({card,learning:{stage:'back',note_text:note,answers:{}},sources:[{source_ref_id:'source1',relative_path:'service.ts',commit_sha:'abc123',project_id:'p1',excerpt:'事务证据'}],questions:[],wiki_draft:draft,conversation:[]});
    if(url.pathname===root+'/cards/'+cardId+'/note'){saves++;if(options.failSave)return json({error:'保存未确认'},503);note=request.postDataJSON().text;await new Promise(resolve=>setTimeout(resolve,120));return json({note_text:note});}
    if(url.pathname===root+'/cards/'+cardId+'/mark')return json({mark:request.postDataJSON().mark});
    if(url.pathname===root+'/wiki/drafts/d1'&&request.method()==='PUT')return json({saved:true});
    if(url.pathname===root+'/wiki/drafts/d1/publish'){publishes++;await new Promise(resolve=>setTimeout(resolve,120));return json({state:'submitted',published:false,change_id:'change-1',task_id:'task-1'},202);}
    if(url.pathname==='/api/growth/local/bindings/b1/commits')return json({commits:[{sha:'abc123',title:'明确事务边界',paths:['service.ts'],authored_at:'2026-01-01'}],base_sha:'base',next_cursor:null,authors:[]});
    if(url.pathname===root+'/commits/abc123')return json({title:'明确事务边界',patch:'-旧实现\n+新的事务边界'});
    if(url.pathname===root+'/radar/runs')return json({suggestions:[{suggestion_id:'r1',proposed_exercise:'解释幂等边界',reason:'真实待验证问题',suggestion_state:'proposed'}]});
    if(url.pathname===root+'/radar/r1/decision')return json({suggestion_id:'r1',suggestion_state:'accepted',learning_id:'learning1'});
    if(url.pathname===root+'/radar/r1')return json({suggestion_id:'r1',suggestion_state:'accepted',practice:{entry_id:'entry1',title:'幂等边界',body:'已有知识证据',question:'重试何时安全？'}});
    if(url.pathname===root+'/radar/r1/complete')return json({state:'pending',published:false,suggestion_id:'r1'});
    return json({error:'fixture route not implemented: '+url.pathname},404);
  });
  return {saves:()=>saves,publishes:()=>publishes,requests};
}
test('专域首页与空态不制造设计示例数据',async({page})=>{await serve(page,{empty:true});await page.goto('/#/growth?view=home');await expect(page.getByRole('heading',{name:'继续你的工程学习'})).toBeVisible();await expect(page.getByText('从自己的项目或笔记开始')).toBeVisible();const nav=page.locator('#growth-navigation');await expect(nav.getByRole('link')).toHaveCount(6);await expect(nav).not.toContainText('收件箱');await expect(nav).not.toContainText('重要确认');await expect(page.getByText('SQLite 单写与 Git CAS')).toHaveCount(0);});
test('API 错误不是空成功',async({page})=>{await serve(page,{failIndex:true});await page.goto('/#/growth?view=library');await expect(page.getByRole('alert')).toContainText('读取未完成');await expect(page.getByText('还没有符合条件的材料')).toHaveCount(0);});
test('学习主题设置保留旧深链接，学习库也有明确入口',async({page})=>{await serve(page);await page.goto('/#/growth?view=settings');await expect(page.getByRole('heading',{name:'学习主题管理',exact:true})).toBeVisible();await expect(page.locator('#growth-cloud-url')).toBeVisible();await page.getByRole('link',{name:'返回学习库'}).click();await page.getByRole('link',{name:'学习主题管理',exact:true}).click();await expect(page.getByRole('heading',{name:'学习主题管理',exact:true})).toBeVisible();});
test('保存本人原话，双击只一次，AI 讲解不代写',async({page})=>{const calls=await serve(page);await page.goto('/#/growth?view=session&card='+cardId);const answer=page.getByRole('textbox',{name:'我的理解',exact:true});await expect(answer).toHaveValue('本人已保存的原话');await answer.fill('这是我自己的架构取舍');await page.getByRole('button',{name:'保存我的理解',exact:true}).dblclick();await expect(page.getByText('本人原话已保存到本机学习记录，未自动回填或公开。')).toBeVisible();expect(calls.saves()).toBe(1);await expect(answer).toHaveValue('这是我自己的架构取舍');await expect(page.getByRole('button',{name:'发送追问'})).toBeDisabled();});
test('保存失败与导航返回都保留本人草稿',async({page})=>{await serve(page,{failSave:true});await page.goto('/#/growth?view=session&card='+cardId);await page.getByRole('textbox',{name:'我的理解',exact:true}).fill('需要保留的本机草稿');await page.getByRole('button',{name:'保存我的理解',exact:true}).click();await expect(page.getByRole('alert')).toContainText('输入仍保留');await page.getByRole('link',{name:'返回学习库'}).click();await page.goBack();await expect(page.getByRole('textbox',{name:'我的理解',exact:true})).toHaveValue('需要保留的本机草稿');});
test('缺失开放理解回填桥明确禁用，不伪造已完成',async({page})=>{await serve(page);await page.goto('/#/growth?view=writeback&card='+cardId);await expect(page.getByRole('button',{name:'生成知识候选'})).toBeDisabled();await expect(page.getByText('开放理解已可保存；',{exact:false})).toBeVisible();});
test('正式候选提交只显示待复核，不冒称已公开',async({page})=>{const calls=await serve(page,{writeback:true});await page.goto('/#/growth?view=writeback&card='+cardId);await expect(page.getByLabel('正式知识与候选差异')).toContainText('新的边界说明');await page.getByRole('button',{name:'提交学习成果'}).dblclick();await expect(page.getByText('已提交复核，尚未确认正式写入。')).toBeVisible();expect(calls.publishes()).toBe(1);await expect(page.getByText('change_id: change-1')).toBeVisible();});
test('项目读取限定范围，不发起全历史扫描或模型发送',async({page})=>{const calls=await serve(page);await page.goto('/#/growth?view=projects&project=p1&commit=abc123');await expect(page.getByText('新的事务边界',{exact:false})).toBeVisible();expect(calls.requests.some(url=>url.includes('scan=true'))).toBe(false);expect(calls.requests.some(url=>url.endsWith('/analysis'))).toBe(false);await expect(page.getByRole('button',{name:'职责演进图（接口待接入）'})).toBeDisabled();});
test('雷达接受后只提交真实原话，离线显示待发',async({page})=>{await serve(page);await page.goto('/#/growth?view=radar');await page.getByRole('button',{name:'查看学习建议'}).click();await page.getByRole('button',{name:'开始练习'}).click();await page.getByRole('textbox',{name:'用自己的话说明架构取舍或应用思路'}).fill('我会用请求标识保证重试幂等');await page.getByRole('button',{name:'提交学习原话'}).click();await expect(page.getByText('已保存在本机，等待连接恢复后提交。')).toBeVisible();});
