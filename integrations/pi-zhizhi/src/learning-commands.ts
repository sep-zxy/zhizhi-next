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
