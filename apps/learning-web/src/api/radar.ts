import { apiFetch } from './client';

const root = '/api/growth/local/knowledge/radar';
export interface RadarSuggestion {
  suggestion_id: string;
  proposed_exercise: string;
  reason: string;
  expected_artifact?: string;
  suggestion_state: string;
}
export interface SnoozedRadarPage {
  suggestions: RadarSuggestion[];
  next_cursor: string|null;
}
export interface RadarPractice {
  suggestion_id: string;
  suggestion_state: string;
  learning_id?: string;
  user_text?: string;
  state?: string;
  practice?: {entry_id: string; title: string; body: string; question: string};
}
export interface RadarSubmission {
  state: 'submitted' | 'pending';
  suggestion_id: string;
  published: false;
  outcome_id?: string;
  task_id?: string;
}

export async function getRadarSuggestions(): Promise<RadarSuggestion[]> {
  const result = await apiFetch<{suggestions: RadarSuggestion[]}>(root + '/runs', {
    method: 'POST', body: JSON.stringify({limit: 3}),
  });
  if (!Array.isArray(result.suggestions)) throw new Error('学习建议响应无效。');
  return result.suggestions;
}
export async function getSnoozedRadarSuggestions(before?: string): Promise<SnoozedRadarPage> {
  const query = before ? '?' + new URLSearchParams({before}) : '';
  const result = await apiFetch<SnoozedRadarPage>(root + '/snoozed' + query, {cache: 'no-store'});
  if (!Array.isArray(result.suggestions) ||
      (result.next_cursor !== null && typeof result.next_cursor !== 'string')) {
    throw new Error('稍后再学列表响应无效。');
  }
  return result;
}
export function readRadarPractice(id: string): Promise<RadarPractice> {
  return apiFetch(root + '/' + encodeURIComponent(id));
}
export function decideRadar(id: string, decision: 'accept'|'reject'|'snooze'): Promise<RadarPractice> {
  return apiFetch(root + '/' + encodeURIComponent(id) + '/decision', {
    method: 'POST', body: JSON.stringify({decision}),
  });
}

const pending = new Map<string, Promise<RadarSubmission>>();
export function submitRadarPractice(id: string, userText: string): Promise<RadarSubmission> {
  if (!userText.trim()) return Promise.reject(new Error('请先写下你自己的理解。'));
  const key = JSON.stringify([id, userText]);
  const existing = pending.get(key);
  if (existing) return existing;
  const request = apiFetch<RadarSubmission>(root + '/' + encodeURIComponent(id) + '/complete', {
    method: 'POST', body: JSON.stringify({user_text: userText}),
  }).then(result => {
    if (!['pending', 'submitted'].includes(result.state) || result.published !== false) {
      throw new Error('服务未确认学习记录状态，请保留原话后重试。');
    }
    return result;
  }).finally(() => pending.delete(key));
  pending.set(key, request);
  return request;
}

export function radarSubmissionLabel(state: RadarSubmission['state']): string {
  return state === 'pending' ? '已保存在本机，等待连接恢复后提交。'
    : '学习原话已提交，知识条目仍待生成与复核。';
}
