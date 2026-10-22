/** Owner-only temporary material supplementation. Never a model tool or automatic keep. */
import { createHash, randomUUID } from 'node:crypto';
import type { ExtensionAPI, ExtensionCommandContext } from '@earendil-works/pi-coding-agent';
import { DomainClient, safeId } from './client.ts';
export function installMaterialCommand(pi: ExtensionAPI, clientFor: (ctx: ExtensionCommandContext)=>DomainClient, display:(value:string)=>string): void {
  const busy=new Set<string>(), keys=new Map<string,string>();
  pi.registerCommand('zhizhi-supplement',{description:'本人编辑临时资料补充文本；按预览版本保存，不保留、不调用模型',handler:async(args,ctx)=>{
    if(ctx.mode!=='tui'||!ctx.hasUI) throw new Error('MATERIAL_SUPPLEMENT_REQUIRES_LOCAL_TUI');
    const id=args.trim(), encoded=safeId(id), client=clientFor(ctx);
    if(busy.has(id)) throw new Error('MATERIAL_SUPPLEMENT_IN_PROGRESS');
    busy.add(id);
    try {
      const saved=(await client.request('intake.read','GET',`/intakes/${encoded}`)).data;
      const revision=saved.preview_revision;
      if(saved.intake_id!==id || typeof revision!=='number' || !Number.isSafeInteger(revision) || revision<0 || revision>=Number.MAX_SAFE_INTEGER) throw new Error('MATERIAL_PREVIEW_UNVERIFIED');
      if(saved.source_id || ['discarded','expired'].includes(String(saved.state))) throw new Error('MATERIAL_NOT_TEMPORARY');
      const text=await ctx.ui.editor('本人补充来源文本（取消不保存；不会自动收藏）','');
      if(text===undefined) return;
      if(!text.trim() || text.length>100000 || Buffer.byteLength(text,'utf8')>300000 || Buffer.from(text,'utf8').toString('utf8')!==text) throw new Error('MATERIAL_TEXT_INVALID');
      if(!await ctx.ui.confirm('保存这段本人补充文本？',display(`Intake ${id}；预览版本 ${revision}\n${text}\n仅替换临时阅读副本，未验证来源完整度；不保留原始材料、不写正式知识、不公开、不调用模型。`))) return;
      const payload={expected_preview_revision:revision,text};
      const fingerprint=createHash('sha256').update(JSON.stringify({id,payload})).digest('hex');
      if(!keys.has(fingerprint)) {if(keys.size>=100) throw new Error('MATERIAL_RECEIPT_CACHE_FULL');keys.set(fingerprint,`tui-${randomUUID()}`);}
      const receipt=(await client.request('intake.supplement','POST',`/intakes/${encoded}/supplement`,payload,keys.get(fingerprint)!)).data;
      const preview=receipt.preview as Record<string,unknown>|undefined;
      const document=preview?.document as Record<string,unknown>|undefined;
      if(receipt.intake_id!==id || receipt.preview_revision!==revision+1 || receipt.state!=='partial' || receipt.source_id || preview?.manual_supplement!==true || document?.body_text!==text) throw new Error('MATERIAL_SUPPLEMENT_RECEIPT_UNVERIFIED');
      ctx.ui.notify(display(`补料回执已核验：${id} 预览版本 ${revision+1}，仍为临时材料。未收藏、未写入知识、未公开。`),'info');
    } finally {busy.delete(id);}
  }});
}
