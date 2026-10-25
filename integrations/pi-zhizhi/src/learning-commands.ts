/** Learning writes originate only from actual TUI input, never from model tool arguments. */
import { randomUUID } from 'node:crypto';
import type { ExtensionAPI, ExtensionCommandContext } from '@earendil-works/pi-coding-agent';
import { LearningViewerClient, noteHash, referenceValid } from './learning-viewer-client.ts';
export function installLearningCommands(pi: ExtensionAPI, display: (value:string)=>string): boolean {
  const url=process.env.ZHIZHI_LEARNING_VIEWER_URL, token=process.env.ZHIZHI_LEARNING_VIEWER_TOKEN;
  if(!url || !token) return false;
  // Validate binding immediately, but never probe the network or auto-fetch credentials.
  let client: LearningViewerClient;
  try { client=new LearningViewerClient(url,token); } catch { return false; }
  const busy=new Set<string>(), keys=new Map<string,string>();
  const requestId=(key:string)=>{
    if(!keys.has(key)) { if(keys.size>=100) throw new Error('VIEWER_REQUEST_CACHE_FULL_READ_BEFORE_RETRY'); keys.set(key,randomUUID()); }
    return keys.get(key)!;
  };
  const run=async(key:string,ctx:ExtensionCommandContext, action:()=>Promise<void>)=>{
    if(ctx.mode!=='tui'||!ctx.hasUI) throw new Error('USER_LEARNING_REQUIRES_LOCAL_TUI');
    if(busy.has(key)) throw new Error('VIEWER_OPERATION_IN_PROGRESS');
    busy.add(key); try { await action(); } finally { busy.delete(key); }
  };
  pi.registerCommand('zhizhi-learn-plan-status', {description:'只读核对计划投递状态（plan_hash）；不重发、不创建练习',
    handler:async(args,ctx)=>run('plan-status',ctx,async()=>{
      const value=await client.planStatus(args.trim());
      ctx.ui.notify(display(`${JSON.stringify(value)}\n这是本机投递回执；pending 不代表远端未接受。核对后才显式重试同一计划，不自动重发。`),'info');
    })});
  pi.registerCommand('zhizhi-learn-plan', {description:'精确正式版本的架构计划预览；本人确认后才建练习：--entry ID --revision N --hash SHA256 --focus architecture',
    handler:async(args,ctx)=>run('plan',ctx,async()=>{
      const parts=args.trim().split(/\s+/); const options=new Map<string,string>();
      for(let i=0;i<parts.length;i+=2) {
        if(!['--entry','--revision','--hash','--focus'].includes(parts[i]) || !parts[i+1] || options.has(parts[i])) throw new Error('EXPECTED_EXACT_SOURCE_ARCHITECTURE_PLAN');
        options.set(parts[i],parts[i+1]);
      }
      const reference={entry_id:options.get('--entry')??'', revision:Number(options.get('--revision')), expected_content_hash:options.get('--hash')??''};
      if(options.size!==4 || options.get('--focus')!=='architecture' || !referenceValid(reference)) throw new Error('EXPECTED_EXACT_SOURCE_ARCHITECTURE_PLAN');
      const plan=await client.previewPlan(reference);
      const preview=display(`${plan.title}\n${JSON.stringify(plan.source_reference)}\n计划 ${plan.plan_hash}\n${plan.question}\n确定性练习模板，不是本人回答；不调用模型、不写本人观点、不表示掌握。取消不创建练习。若接受回执不确定，先用 /zhizhi-learn-plan-status ${plan.plan_hash} 核对，不自动重发。`);
      if(!await ctx.ui.confirm('接受这份架构练习计划？',preview)) return;
      let value;
      try { value=await client.acceptPlan(plan); }
      catch (error) {
        ctx.ui.notify(display(`计划接受尚未核验；请先 /zhizhi-learn-plan-status ${plan.plan_hash} 核对。未自动重发；pending 不代表远端未接受。`),'warning');
        throw error;
      }
      ctx.ui.notify(display(`计划对应练习 ${value.learning_id}；状态 ${value.suggestion_state}；建议 ${value.suggestion_id}。在既有学习雷达页面作答：${new URL(url).origin}/#/growth?view=radar&radar=${encodeURIComponent(value.suggestion_id)}；仅本人原话可提交，不代表正式写入或公开。`),'info');
    })});
  pi.registerCommand('zhizhi-learn', {description:'本人确认精确条目版本并创建自由学习上下文：entry_id revision hash',
    handler:async(args,ctx)=>run('create',ctx,async()=>{
      const parts=args.trim().split(/\s+/); const reference={entry_id:parts[0],revision:Number(parts[1]),expected_content_hash:parts[2]};
      if(parts.length!==3 || !referenceValid(reference)) throw new Error('EXPECTED_ENTRY_REVISION_HASH');
      if(!await ctx.ui.confirm('创建这份正式版本的学习上下文？',display(JSON.stringify(reference)+'\n仅私有学习，不提交理解、不公开。'))) return;
      const value=await client.create(reference,requestId('create:'+JSON.stringify(reference)));
      ctx.ui.notify(display(`学习上下文 ${value.context_id}；版本 ${value.context_version}。用 /zhizhi-learn-note 输入本人原话。`),'info');
    })});
  pi.registerCommand('zhizhi-learn-open', {description:'查看已保存学习上下文，不调用模型',
    handler:async(args,ctx)=>run(args.trim(),ctx,async()=>{
      const value=await client.read(args.trim());
      ctx.ui.notify(display(JSON.stringify(value)),'info');
    })});
  pi.registerCommand('zhizhi-learn-note', {description:'本人编辑私有学习原话，按上下文版本保存，不自动提交',
    handler:async(args,ctx)=>run(args.trim(),ctx,async()=>{
      const saved=await client.read(args.trim());
      const text=await ctx.ui.editor('本人理解（不是AI回答；取消不保存）',saved.note_text);
      if(text===undefined) return;
      if(text.length>20000) throw new Error('VIEWER_NOTE_TOO_LARGE');
      if(!await ctx.ui.confirm('保存这段本人原话？',display(`Context ${saved.context_id} v${saved.context_version}\n${text}\n仅私有草稿；不会提交正式知识或公开。`))) return;
      const value=await client.save(saved,text,requestId(`note:${saved.context_id}:${saved.context_version}:${noteHash(text)}`));
      ctx.ui.notify(display(`本人原话已核验保存：${value.context_id} v${value.context_version}；尚未提交理解。`),'info');
    })});
  pi.registerCommand('zhizhi-learn-submit', {description:'本人确认已保存原话版本进入独立复核流程，不代表正式写入',
    handler:async(args,ctx)=>run(args.trim(),ctx,async()=>{
      const saved=await client.read(args.trim());
      if(!saved.note_text.trim()) throw new Error('VIEWER_SAVED_NOTE_REQUIRED');
      if(!await ctx.ui.confirm('提交这份已保存的本人理解？',display(`Context ${saved.context_id} v${saved.context_version}\nSHA256 ${saved.note_sha256}\n${saved.note_text}\n进入已有生成/独立复核流程，仍受已有模型授权与额度约束；原话私有，不代表正式写入或公开。`))) return;
      const value=await client.submit(saved);
      ctx.ui.notify(display(`提交回执 ${value.submission_id}；delivery=${value.delivery_state}；remote=${value.remote_state}；task=${value.task_id??'未返回'}。不推定正式提交或公开；结果不确定请到学习页面核对，不自动重发。`),'info');
    })});
  return true;
}
