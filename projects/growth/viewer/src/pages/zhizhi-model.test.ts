import { describe, expect, it } from 'vitest';
import { learningCards, learningUrl, readingLabel, writebackLabel, zhizhiView } from './zhizhi-model';
import type { KnowledgeCard, KnowledgeIndex } from '../api/knowledge';
const card: KnowledgeCard={card_id:'c1',concept_id:'n1',title:'事务与边界',front_question:'为什么单写不替代版本校验？',back_answer:'AI 讲解',back_explanation:'',card_version:1,followup_mark:'none',project_ids:['p1']};
const index:KnowledgeIndex={cards:[card,{...card,card_id:'c2',project_ids:['p2']}],candidates:[],wiki_articles:[],vault_path:''};
describe('知枝学习域路由与真实记录投影',()=>{
  it('兼容旧入口但不会路由到通用审核',()=>{expect(zhizhiView('today')).toBe('home');expect(zhizhiView('cards')).toBe('library');expect(zhizhiView('wiki')).toBe('writeback');expect(zhizhiView('pending')).toBe('candidates');expect(zhizhiView('unknown')).toBe('home');});
  it('项目和全文筛选不制造示例记录',()=>{expect(learningCards(index,'p1','版本')).toEqual([card]);expect(learningCards(null,'','')).toEqual([]);expect(learningCards(index,'p3')).toEqual([]);expect(learningCards(index,'p1','Redis')).toEqual([]);});
  it('阅读不推断掌握或本人理解',()=>{expect(readingLabel(card)).toBe('待阅读');expect(readingLabel({...card,learning_stage:'completed'})).toBe('已有练习记录');expect(readingLabel({...card,learning_stage:'back'})).toBe('已开始阅读');});
  it('回填提交、正式写入和公开状态严格区分',()=>{expect(writebackLabel(null)).toBe('尚未生成候选草稿');expect(writebackLabel({draft_id:'d',card_id:'c',markdown:'x',status:'submitted'})).toContain('待复核');expect(writebackLabel({draft_id:'d',card_id:'c',markdown:'x',status:'published'})).toContain('不代表公开');expect(writebackLabel({draft_id:'d',card_id:'c',markdown:'x',status:'submitted',delivery_error:'WAITING_AUTH'})).toContain('WAITING_AUTH');});
  it('URL 编码保留稳定项目与卡片身份',()=>{const url=learningUrl('session','p&1','c#2');expect(url).toBe('/growth?view=session&project=p%261&card=c%232');});
});
