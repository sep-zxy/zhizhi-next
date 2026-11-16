import { z } from 'zod';
import { apiFetch } from './client';
import { understandingHash as hashText } from './understanding';

const uuid = z.string().uuid();
const entryIdSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/);
const version = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const digest = z.string().regex(/^[a-f0-9]{64}$/);
const evidenceSchema = z.object({
  source_id: z.string().optional(), learning_outcome_id: z.string().optional(),
  external_document_id: z.string().optional(), revision: z.number().int().optional(), source_revision: z.number().int().positive().optional(),
});
const entrySchema = z.object({
  entry_id: entryIdSchema, revision: version, title: z.string(), body: z.string(), content_hash: digest,
  evidence_refs: z.array(evidenceSchema), data_classification: z.string(), classification_labels: z.array(z.string()),
});
const cardSchema = z.object({ card_id: uuid, card_version: version, title: z.string() });
const linkSchema = z.object({
  context_id: uuid, entry_id: entryIdSchema, revision: version, content_hash: digest,
  card_id: uuid, card_version: version, created_at: z.string(), state: z.literal('linked'),
});
const attachSchema = z.object({
  request_id: uuid, entry_id: entryIdSchema, revision: version, expected_content_hash: digest,
  target_card_id: uuid, expected_card_version: version,
}).strict();
export type EntryReference = { entryId: string; revision: number };
export type EntryContextPreview = { entry: z.infer<typeof entrySchema>; cards: z.infer<typeof cardSchema>[] };
export type EntryContextLink = z.infer<typeof linkSchema>;
export type LinkedEntryContext = EntryContextLink & { entry: z.infer<typeof entrySchema> };
export type AttachEntryContext = z.infer<typeof attachSchema>;
const root = '/api/growth/local/knowledge';

export function entryReference(params: URLSearchParams): EntryReference | null {
  const id = params.get('entry_id'); const revision = params.get('revision');
  if (params.getAll('entry_id').length !== 1 || params.getAll('revision').length !== 1
    || !entryIdSchema.safeParse(id).success || !revision || !/^[1-9][0-9]*$/.test(revision)
    || !version.safeParse(Number(revision)).success) return null;
  return { entryId: id!, revision: Number(revision) };
}
export function entryContextUrl(entryId: string, revision: number): string {
  return '/growth?' + new URLSearchParams({ view: 'entry-context', entry_id: entryId, revision: String(revision) });
}
export async function verifyFormalEntry(entry: EntryContextPreview['entry'], entryId: string, revision: number) {
  if (entry.entry_id !== entryId || entry.revision !== revision || entry.content_hash !== await hashText(entry.body)) throw new Error('正式版本身份或正文摘要不匹配');
}
export async function previewEntryContext(reference: EntryReference): Promise<EntryContextPreview> {
  entryIdSchema.parse(reference.entryId); version.parse(reference.revision);
  const query = new URLSearchParams({ entry_id: reference.entryId, revision: String(reference.revision) });
  const value = z.object({ entry: entrySchema, cards: z.array(cardSchema) }).parse(await apiFetch<unknown>(root + '/entry-context?' + query));
  await verifyFormalEntry(value.entry, reference.entryId, reference.revision);
  return value;
}
export async function attachEntryContext(input: AttachEntryContext): Promise<EntryContextLink> {
  const body = attachSchema.parse(input);
  const value = linkSchema.parse(await apiFetch<unknown>(root + '/entry-contexts', { method: 'POST', retryAuth: false, body: JSON.stringify(body) }));
  if (value.entry_id !== body.entry_id || value.revision !== body.revision || value.content_hash !== body.expected_content_hash
    || value.card_id !== body.target_card_id || value.card_version !== body.expected_card_version) throw new Error('关联回执与确认范围不匹配');
  return value;
}
export async function readEntryContexts(cardId: string): Promise<{ items: LinkedEntryContext[]; has_more: false }> {
  uuid.parse(cardId);
  const value = z.object({ items: z.array(linkSchema.extend({ entry: entrySchema })), has_more: z.literal(false) }).parse(await apiFetch<unknown>(root + '/cards/' + encodeURIComponent(cardId) + '/entry-contexts'));
  await Promise.all(value.items.map(async item => {
    if (item.card_id !== cardId || item.content_hash !== item.entry.content_hash) throw new Error('正式引用与卡片身份不匹配');
    await verifyFormalEntry(item.entry, item.entry_id, item.revision);
  }));
  return value;
}
export const entryContextFailure = '未能确认精确版本或关联状态。请重新读取并核对权限与版本；不会自动重试或换成最新版；不显示缓存正文。';

export { entrySchema as formalEntrySchema };
