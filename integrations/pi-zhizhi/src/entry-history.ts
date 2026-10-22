/** Bounded, read-only history. Historical text is evidence, never permission. */
import { DomainClient, safeId, type Envelope } from './client.ts';
const integer = (value: number, min: number, max: number) => Number.isSafeInteger(value) && value >= min && value <= max;
const hash = (value: unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const fields = (value: Record<string, unknown>, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));
export async function readVersions(client: DomainClient, entryId: string, limit = 20, offset = 0, signal?: AbortSignal): Promise<Envelope> {
  if (!integer(limit, 1, 100) || !integer(offset, 0, 1000)) throw new Error('ENTRY_HISTORY_RANGE_INVALID');
  const result = await client.request('entry.versions', 'GET', `/entries/${safeId(entryId)}/versions?limit=${limit}&offset=${offset}`, undefined, undefined, signal);
  const d = result.data;
  if (!fields(d, ['entry_id','items','has_more','classification_labels']) || d.entry_id !== entryId || typeof d.has_more !== 'boolean' || !Array.isArray(d.items) || d.items.length > limit ||
      !d.items.every(item => object(item) && fields(item, ['revision','title','updated_at','content_hash','classification_labels']) && integer(Number(item.revision),1,Number.MAX_SAFE_INTEGER) && typeof item.revision === 'number' && typeof item.title === 'string' && typeof item.updated_at === 'string' && hash(item.content_hash)) ||
      new Set(d.items.map(item => item.revision)).size !== d.items.length) throw new Error('ENTRY_HISTORY_UNVERIFIED');
  return result;
}
export async function readDiff(client: DomainClient, entryId: string, from: number, to: number, signal?: AbortSignal): Promise<Envelope> {
  if (!integer(from,1,2147483647) || !integer(to,1,2147483647)) throw new Error('ENTRY_HISTORY_RANGE_INVALID');
  const result = await client.request('entry.diff','GET',`/entries/${safeId(entryId)}/diff?from_revision=${from}&to_revision=${to}`,undefined,undefined,signal);
  const d = result.data;
  if (!fields(d,['entry_id','from_revision','to_revision','from_content_hash','to_content_hash','unified_diff','classification_labels']) || d.entry_id !== entryId || d.from_revision !== from || d.to_revision !== to || !hash(d.from_content_hash) || !hash(d.to_content_hash) || typeof d.unified_diff !== 'string' || Buffer.byteLength(d.unified_diff, 'utf8') > 300 * 1024) throw new Error('ENTRY_DIFF_UNVERIFIED');
  return result;
}
