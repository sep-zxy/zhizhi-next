import { describe, expect, it, vi } from 'vitest';
import { learningCards, learningUrl, readingLabel, writebackLabel, zhizhiView, resumeLearningCard, rememberLearningCard, lastOpenedLearningCard } from './zhizhi-model';
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

describe('续学指针只选择当前可见的真实卡片',()=>{
  it('优先最后成功打开的卡，而不是列表第一个进行中卡',()=>{
    const first={...card,card_id:'first',learning_stage:'back' as const};const recent={...card,card_id:'recent',learning_stage:'back' as const};
    expect(resumeLearningCard([first,recent],'recent')).toBe(recent);
    expect(resumeLearningCard([first],'not-visible')).toBe(first);
    expect(resumeLearningCard([],'recent')).toBeUndefined();
  });
  it('存储不可用不会影响读取和学习，也不虚构最后会话',()=>{
    const getItem=vi.fn(()=>{throw new Error('disabled');});const setItem=vi.fn(()=>{throw new Error('disabled');});
    vi.stubGlobal('localStorage',{getItem,setItem});
    try {expect(lastOpenedLearningCard()).toBe('');expect(()=>rememberLearningCard('real-card')).not.toThrow();expect(setItem).toHaveBeenCalledWith('zhizhi:last-opened-card','real-card');}
    finally {vi.unstubAllGlobals();}
  });
});
