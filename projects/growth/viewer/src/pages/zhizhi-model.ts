import type { KnowledgeCard, KnowledgeIndex, WikiDraft } from '../api/knowledge';
export type ZhizhiView = 'home' | 'projects' | 'library' | 'session' | 'understanding' | 'writeback' | 'radar' | 'candidates' | 'exercise' | 'cli' | 'topic-management';
const aliases: Record<string, ZhizhiView> = {today:'home',overview:'home',cards:'library',learning:'candidates',pending:'candidates',wiki:'writeback',review:'library',discuss:'library',topics:'topic-management',settings:'topic-management'};
export function zhizhiView(value: string | null): ZhizhiView {
  const names: ZhizhiView[] = ['home','projects','library','session','understanding','writeback','radar','candidates','exercise','cli','topic-management'];
  return aliases[value ?? ''] ?? (names.includes(value as ZhizhiView) ? value as ZhizhiView : 'home');
}
export function learningCards(index: KnowledgeIndex | null, project: string, query = ''): KnowledgeCard[] {
  return (index?.cards ?? []).filter(card => (!project || card.project_ids?.includes(project)) && (!query.trim() || `${card.title} ${card.front_question}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
}
export function readingLabel(card: KnowledgeCard): string {
  return !card.learning_stage || card.learning_stage === 'front' ? '待阅读' : card.learning_stage === 'completed' ? '已有练习记录' : '已开始阅读';
}
export function writebackLabel(draft: WikiDraft | null): string {
  if (draft?.delivery_error) return `等待处理 · ${draft.delivery_error}`;
  if (draft?.status === 'published') return '已写入知识库 · 不代表公开';
  if (draft?.status === 'submitted') return '已提交 · 待复核与正式写入';
  return draft ? '候选草稿 · 尚未提交' : '尚未生成候选草稿';
}
export function learningUrl(view: ZhizhiView, project = '', card = ''): string {
  const query = new URLSearchParams({view});
  if (project) query.set('project', project);
  if (card) query.set('card', card);
  return '/growth?' + query;
}
