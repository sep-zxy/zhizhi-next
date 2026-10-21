import { z } from 'zod';
import { apiFetch } from './client';
import { formalEntrySchema, verifyFormalEntry } from './entry-context';
import { submissionStateSchema, understandingHash } from './understanding';

const uuid = z.string().uuid();
const version = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const entryId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/);
const contextBase = z.object({ context_id: uuid, source_kind: z.literal('wiki_entry'), context_version: version, created_at: z.string(), updated_at: z.string(), state: z.literal('reading') });
const summarySchema = contextBase.extend({ entry_id: entryId, revision: version, content_hash: hash, title: z.string(), note_sha256: hash });
const contextSchema = contextBase.extend({ entry: formalEntrySchema, note_text: z.string().max(20000), note_sha256: hash });
const submissionSchema = submissionStateSchema.extend({ context_id: uuid, context_version: version });
export type WikiLearningContext = z.infer<typeof contextSchema>;
export type WikiLearningSummary = z.infer<typeof summarySchema>;
export type WikiLearningSubmission = z.infer<typeof submissionSchema>;
export type CreateWikiLearning = { request_id: string; entry_id: string; revision: number; expected_content_hash: string };
export type SaveWikiNote = { request_id: string; expected_context_version: number; note_text: string };
const root = '/api/growth/local/knowledge/entry-learning-contexts';
const contextPath = (id: string) => root + '/' + uuid.parse(id);
export const wikiLearningUrl = (id: string) => '/growth?' + new URLSearchParams({ view: 'wiki-learning', context_id: id });
export const validWikiContextId = (id: string | null): id is string => uuid.safeParse(id).success;
export const wikiLearningFailure = '自由学习状态未获确认。原话输入保留，请重新读取核对；不会自动重试、创建工程卡或推定写入成功。';
async function checkedContext(raw: unknown, expectedId?: string): Promise<WikiLearningContext> {
  const value = contextSchema.parse(raw);
  if ((expectedId && value.context_id !== expectedId) || await understandingHash(value.note_text) !== value.note_sha256) throw new Error('自由学习身份或原话摘要不匹配');
  await verifyFormalEntry(value.entry, value.entry.entry_id, value.entry.revision);
  return value;
}
export async function createWikiLearning(input: CreateWikiLearning) {
  const body = z.object({ request_id: uuid, entry_id: entryId, revision: version, expected_content_hash: hash }).strict().parse(input);
  const value = await checkedContext(await apiFetch<unknown>(root, { method: 'POST', retryAuth: false, body: JSON.stringify(body) }));
  if (value.entry.entry_id !== body.entry_id || value.entry.revision !== body.revision || value.entry.content_hash !== body.expected_content_hash) throw new Error('创建结果替换了来源版本');
  return value;
}
export async function listWikiLearning(offset = 0) {
  if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('无效分页');
  return z.object({ items: z.array(summarySchema), has_more: z.boolean() }).parse(await apiFetch<unknown>(root + '?limit=20&offset=' + offset));
}
export async function readWikiLearning(id: string) {
  return checkedContext(await apiFetch<unknown>(contextPath(id)), id);
}
export async function saveWikiNote(id: string, input: SaveWikiNote) {
  const body = z.object({ request_id: uuid, expected_context_version: version, note_text: z.string().max(20000) }).strict().parse(input);
  const value = await checkedContext(await apiFetch<unknown>(contextPath(id) + '/note', { method: 'POST', retryAuth: false, body: JSON.stringify(body) }), id);
  if (value.note_text !== body.note_text || value.context_version < body.expected_context_version) throw new Error('保存回执未确认原话或版本');
  return value;
}
function checkedSubmission(raw: unknown, id: string): WikiLearningSubmission {
  const value = submissionSchema.parse(raw);
  if (value.context_id !== id) throw new Error('自由理解提交身份不匹配');
  return value;
}
export async function listWikiSubmissions(id: string) {
  const value = z.object({ items: z.array(submissionSchema), has_more: z.boolean() }).parse(await apiFetch<unknown>(contextPath(id) + '/understanding/submissions'));
  value.items.forEach(item => checkedSubmission(item, id));
  return value;
}
export async function readWikiSubmission(id: string, submissionId: string) {
  hash.parse(submissionId);
  const value = checkedSubmission(await apiFetch<unknown>(contextPath(id) + '/understanding/submissions/' + submissionId), id);
  if (value.submission_id !== submissionId) throw new Error('自由理解提交身份不匹配');
  return value;
}
export async function submitWikiUnderstanding(id: string, contextVersion: number, text: string) {
  version.parse(contextVersion);
  if (!text.trim() || text.length > 20000) throw new Error('请先保存本人理解');
  const noteHash = await understandingHash(text);
  const value = checkedSubmission(await apiFetch<unknown>(contextPath(id) + '/understanding/submissions', { method: 'POST', retryAuth: false, body: JSON.stringify({ context_version: contextVersion, note_sha256: noteHash }) }), id);
  if (value.context_version !== contextVersion || value.note_sha256 !== noteHash || value.user_text !== text) throw new Error('自由理解提交快照不匹配');
  return value;
}
