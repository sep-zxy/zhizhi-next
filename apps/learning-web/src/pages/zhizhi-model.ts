import type { GrowthLocalState, GrowthProject } from '../api/growth';
import type { KnowledgeCandidate, KnowledgeCard, KnowledgeIndex, WikiDraft } from '../api/knowledge';
export type ZhizhiView = 'home' | 'projects' | 'library' | 'session' | 'understanding' | 'writeback' | 'radar' | 'candidates' | 'exercise' | 'cli' | 'topic-management' | 'entry-context' | 'wiki-learning';
const aliases: Record<string, ZhizhiView> = {today:'home',overview:'home',cards:'library',learning:'candidates',pending:'candidates',wiki:'writeback',review:'library',discuss:'library',topics:'topic-management',settings:'topic-management'};
export function zhizhiView(value: string | null): ZhizhiView {
  const names: ZhizhiView[] = ['home','projects','library','session','understanding','writeback','radar','candidates','exercise','cli','topic-management','entry-context','wiki-learning'];
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

const lastCardKey = 'zhizhi:last-opened-card';
export function lastOpenedLearningCard(): string {
  try { return localStorage.getItem(lastCardKey) ?? ''; } catch { return ''; }
}
export function rememberLearningCard(cardId: string): void {
  // A navigation hint only. Server card permissions, version and conversation remain authoritative.
  try { localStorage.setItem(lastCardKey, cardId); } catch { /* Private storage can be unavailable. */ }
}
export function resumeLearningCard(cards: KnowledgeCard[], lastCardId: string): KnowledgeCard | undefined {
  return cards.find(card => card.card_id === lastCardId)
    ?? cards.find(card => card.learning_stage && !['front', 'completed'].includes(card.learning_stage))
    ?? cards[0];
}

export function projectCommitLearning(cards: KnowledgeCard[], projectId: string, commitSha: string): KnowledgeCard[] {
  if (!projectId || !commitSha) return [];
  return cards.filter(card => card.project_ids?.includes(projectId) && card.source_commit_shas?.includes(commitSha));
}

type CandidateCommitSource = { title?: string } & (
  | { project: GrowthProject; bindingId: string; reason?: undefined }
  | { project?: undefined; bindingId?: undefined; reason: string }
);

export function candidateCommitSource(
  candidate: KnowledgeCandidate,
  sha: string,
  { projects, bindings }: Pick<GrowthLocalState, 'projects' | 'bindings'>,
): CandidateCommitSource {
  const sources = [...(candidate.sources ?? []), ...(candidate.source_refs ?? [])]
    .filter(source => source.commit_sha === sha);
  const title = sources.find(source => source.commit_title)?.commit_title;
  const candidateProjects = [...new Set(candidate.project_ids?.filter(Boolean))];
  const sourceProjects = [...new Set(sources.flatMap(source => source.project_id ? [source.project_id] : []))];
  const projectIds = sourceProjects.length ? sourceProjects : candidateProjects;
  const [projectId] = projectIds;
  if (projectIds.length !== 1 || (candidateProjects.length && !candidateProjects.includes(projectId))) {
    return { title, reason: '无法确定此提交所属的项目。' };
  }
  const project = projects.find(item => item.project_id === projectId);
  if (!project?.local_processing) {
    return { title, reason: '此提交所属的项目当前未授权读取代码。' };
  }

  const sourceBindings = [...new Set(sources.flatMap(source => source.binding_id ? [source.binding_id] : []))];
  const matchingBindings = bindings.filter(binding =>
    binding.project_id === projectId && (!sourceBindings.length || sourceBindings.includes(binding.binding_id)),
  );
  if (sourceBindings.length > 1 || matchingBindings.length > 1) {
    return { title, reason: '无法确定此提交所属的仓库。' };
  }
  if (!matchingBindings.length) {
    return { title, reason: '此提交所属的仓库当前未绑定或未授权。' };
  }
  return { title, project, bindingId: matchingBindings[0].binding_id };
}
