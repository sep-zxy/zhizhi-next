import { z } from 'zod';
import { apiFetch } from './client';

const taskSchema = z.object({
  task_id: z.string(), kind: z.string(), state: z.string(), revision: z.number().optional(),
});
const changeSchema = z.object({
  change_id: z.string(), state: z.string(), entry_id: z.string(), base_revision: z.number().int().nonnegative(),
  committed_revision: z.number().int().nullable(), title: z.string(), path: z.string().nullable(),
  original_markdown: z.string(), candidate_markdown: z.string(), evidence_refs: z.array(z.object({ source_id: z.string().optional(), learning_outcome_id: z.string().optional(), external_document_id: z.string().optional(), revision: z.number().int().optional() })),
});
const submissionSchema = z.object({
  submission_id: z.string().regex(/^[a-f0-9]{64}$/), card_id: z.string().min(1), card_version: z.number().int().positive(),
  note_sha256: z.string().regex(/^[a-f0-9]{64}$/), user_text: z.string(),
  delivery_state: z.enum(['pending', 'acknowledged', 'rejected']),
  outcome_id: z.string().nullable(), task_id: z.string().nullable(),
  remote_state: z.enum(['available', 'unknown', 'not_submitted']),
  task: taskSchema.nullable(), change: changeSchema.nullable(),
  error: z.object({ code: z.string(), retryable: z.boolean() }).nullable(),
});
export type UnderstandingSubmission = z.infer<typeof submissionSchema>;
export type SavedUnderstanding = { cardId: string; cardVersion: number; text: string };
const pathFor = (cardId: string) => '/api/growth/local/knowledge/cards/' + encodeURIComponent(cardId) + '/understanding/submissions';

/** Hash the saved UTF-8 bytes exactly: whitespace and line endings are facts too. */
export async function understandingHash(text: string): Promise<string> {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), value => value.toString(16).padStart(2, '0')).join('');
}
function verifyCard(raw: unknown, cardId: string): UnderstandingSubmission {
  const value = submissionSchema.parse(raw);
  if (value.card_id !== cardId) throw new Error('理解提交身份不匹配');
  return value;
}
export async function listUnderstandingSubmissions(cardId: string) {
  const raw = await apiFetch<unknown>(pathFor(cardId));
  const value = z.object({ items: z.array(submissionSchema), has_more: z.boolean() }).parse(raw);
  value.items.forEach(item => verifyCard(item, cardId));
  return value;
}
export async function readUnderstandingSubmission(cardId: string, submissionId: string) {
  const value = verifyCard(await apiFetch<unknown>(pathFor(cardId) + '/' + encodeURIComponent(submissionId)), cardId);
  if (value.submission_id !== submissionId) throw new Error('理解提交身份不匹配');
  return value;
}
export async function submitUnderstanding(snapshot: SavedUnderstanding) {
  if (!snapshot.text.trim() || !Number.isInteger(snapshot.cardVersion) || snapshot.cardVersion < 1) throw new Error('请先保存有效的本人原话');
  const hash = await understandingHash(snapshot.text);
  // A lost response is recovered by GET, never by automatic POST replay (including auth refresh).
  const value = verifyCard(await apiFetch<unknown>(pathFor(snapshot.cardId), {
    method: 'POST', retryAuth: false,
    body: JSON.stringify({ card_version: snapshot.cardVersion, note_sha256: hash }),
  }), snapshot.cardId);
  if (value.card_version !== snapshot.cardVersion || value.note_sha256 !== hash || value.user_text !== snapshot.text) throw new Error('理解提交快照不匹配');
  return value;
}
export function understandingChangeLabel(value: UnderstandingSubmission): string {
  if (value.remote_state === 'unknown') return '远端状态未确认；以下为已保存的记录，不代表最新处理结果。';
  if (value.delivery_state === 'pending') return '已保存到本机待发，尚未确认服务受理。';
  if (value.delivery_state === 'rejected') return '提交未被受理；请核对连接与原话版本。';
  const change = value.change;
  if (!change) return '已受理，等待生成或读取候选；尚未确认正式写入。';
  if (['committed', 'indexed'].includes(change.state) && (change.committed_revision ?? 0) > 0) return '已确认正式写入，不代表公开。';
  return ({ needs_user: '候选需要重要确认，请前往个人 Wiki 核对。', needs_ai_review: '候选等待独立复核，尚未确认正式写入。', approved: '候选复核已通过，等待正式写入。', rejected: '候选未通过复核，未确认写入。', no_change: '本次未产生知识变更，不等于正式写入。' } as Record<string, string>)[change.state] ?? '候选处理状态未确认，不推定正式写入。';
}
export function understandingFailureSummary(): string {
  return '操作未获确认。原话与已有记录保留；请刷新核对提交记录和版本，不会自动重新提交。';
}
