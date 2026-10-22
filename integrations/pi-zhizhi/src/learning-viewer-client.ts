/** Explicit local viewer binding; never reuse domain credentials or bootstrap an identity. */
import { createHash } from 'node:crypto';
const ROOT = '/api/growth/local/knowledge/entry-learning-contexts';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HASH = /^[0-9a-f]{64}$/;
const ENTRY = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const matches = (pattern: RegExp, value: unknown): value is string => typeof value === 'string' && pattern.exec(value)?.[0] === value;
const record = (value: unknown): value is Record<string, any> => !!value && typeof value === 'object' && !Array.isArray(value);
const version = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
export const noteHash = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex');
export type LearningReference = { entry_id: string; revision: number; expected_content_hash: string };
export type LearningContext = { context_id: string; context_version: number; note_text: string; note_sha256: string;
  entry: { entry_id: string; revision: number; body: string; title: string; content_hash: string } };
export type LearningSubmission = { context_id: string; context_version: number; submission_id: string;
  note_sha256: string; user_text: string; delivery_state: string; remote_state: string; task_id: string | null };
export function referenceValid(value: LearningReference): boolean {
  return matches(ENTRY, value.entry_id) && version(value.revision) && matches(HASH, value.expected_content_hash);
}
function context(raw: unknown, expectedId?: string): LearningContext {
  if (!record(raw) || !matches(UUID, raw.context_id) || (expectedId && raw.context_id !== expectedId) ||
      !version(raw.context_version) || raw.source_kind !== 'wiki_entry' || raw.state !== 'reading' ||
      typeof raw.note_text !== 'string' || raw.note_text.length > 20000 || !matches(HASH, raw.note_sha256) ||
      noteHash(raw.note_text) !== raw.note_sha256 || !record(raw.entry) ||
      !matches(ENTRY, raw.entry.entry_id) || !version(raw.entry.revision) || typeof raw.entry.body !== 'string' ||
      typeof raw.entry.title !== 'string' || !matches(HASH, raw.entry.content_hash) ||
      noteHash(raw.entry.body) !== raw.entry.content_hash) throw new Error('VIEWER_CONTEXT_UNVERIFIED');
  return raw as LearningContext;
}
export class LearningViewerClient {
  private readonly origin: string;
  constructor(url: string, private readonly token: string, private readonly fetcher: typeof fetch = fetch) {
    const target = new URL(url);
    if (!['http:', 'https:'].includes(target.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname) ||
        !target.port || target.username || target.password || target.search || target.hash || target.pathname !== '/' ||
        !token || /[\r\n]/.test(token)) throw new Error('VIEWER_EXPLICIT_LOCAL_BINDING_REQUIRED');
    this.origin = target.origin;
  }
  private id(id: string) { if (!matches(UUID,id)) throw new Error('VIEWER_CONTEXT_ID_INVALID'); return id; }
  private async request(path: string, body?: Record<string, unknown>): Promise<unknown> {
    // All callers construct paths from validated UUIDs; no arbitrary routes or redirects.
    let response: Response;
    try {
      response = await this.fetcher(this.origin + path, { method: body ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json', 'X-AhaDiff-Token': this.token },
        body: body ? JSON.stringify(body) : undefined, redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(30000) });
    } catch { throw new Error(body ? 'VIEWER_WRITE_UNCONFIRMED_READ_BEFORE_RETRY' : 'VIEWER_READ_UNAVAILABLE'); }
    const reader = response.body?.getReader(); if (!reader) throw new Error(body ? 'VIEWER_WRITE_UNCONFIRMED_READ_BEFORE_RETRY' : 'VIEWER_RESPONSE_INVALID');
    const parts: Uint8Array[] = []; let size = 0;
    try {
      for (;;) { const chunk = await reader.read(); if (chunk.done) break;
        size += chunk.value.length; if (size > 1048576) { await reader.cancel(); throw new Error(); } parts.push(chunk.value); }
    } catch { throw new Error(body ? 'VIEWER_WRITE_UNCONFIRMED_READ_BEFORE_RETRY' : 'VIEWER_RESPONSE_INVALID'); }
    let raw: unknown;
    try { raw = JSON.parse(Buffer.concat(parts).toString('utf8')); } catch { throw new Error(body ? 'VIEWER_WRITE_UNCONFIRMED_READ_BEFORE_RETRY' : 'VIEWER_RESPONSE_INVALID'); }
    if (!response.ok) {
      const code = record(raw) && record(raw.error) && matches(/^[A-Z][A-Z0-9_]{0,99}$/,raw.error.code) ? raw.error.code : 'VIEWER_REQUEST_FAILED';
      throw new Error(`${code} (HTTP ${response.status}); read current state before retry`);
    }
    return raw;
  }
  async create(reference: LearningReference, requestId: string): Promise<LearningContext> {
    if (!referenceValid(reference) || !matches(UUID,requestId)) throw new Error('VIEWER_REFERENCE_INVALID');
    const value = context(await this.request(ROOT, { ...reference, request_id: requestId }));
    if(value.entry.entry_id !== reference.entry_id || value.entry.revision !== reference.revision || value.entry.content_hash !== reference.expected_content_hash)
      throw new Error('VIEWER_REFERENCE_CHANGED');
    return value;
  }
  async read(id: string): Promise<LearningContext> { return context(await this.request(`${ROOT}/${this.id(id)}`),id); }
  async save(saved: LearningContext, text: string, requestId: string): Promise<LearningContext> {
    const expectedVersion = saved.context_version + (text === saved.note_text ? 0 : 1);
    if (!version(saved.context_version) || !version(expectedVersion) || !matches(UUID,requestId) || text.length > 20000) throw new Error('VIEWER_NOTE_INVALID');
    const value = context(await this.request(`${ROOT}/${this.id(saved.context_id)}/note`, {
      request_id:requestId, expected_context_version:saved.context_version, note_text:text,
    }),saved.context_id);
    if(value.note_text !== text || value.context_version !== expectedVersion ||
      value.entry.entry_id !== saved.entry.entry_id || value.entry.revision !== saved.entry.revision || value.entry.content_hash !== saved.entry.content_hash)
      throw new Error('VIEWER_NOTE_UNCONFIRMED');
    return value;
  }
  async submit(saved: LearningContext): Promise<LearningSubmission> {
    if(!saved.note_text.trim() || !version(saved.context_version) || noteHash(saved.note_text)!==saved.note_sha256) throw new Error('VIEWER_SAVED_NOTE_REQUIRED');
    const raw=await this.request(`${ROOT}/${this.id(saved.context_id)}/understanding/submissions`, {
      context_version:saved.context_version, note_sha256:saved.note_sha256,
    });
    if(!record(raw) || raw.context_id!==saved.context_id || raw.context_version!==saved.context_version ||
      raw.note_sha256!==saved.note_sha256 || raw.user_text!==saved.note_text || !matches(HASH,raw.submission_id) ||
      !['pending','acknowledged','rejected'].includes(raw.delivery_state) || !['available','unknown','not_submitted'].includes(raw.remote_state) ||
      !(raw.task_id===null || typeof raw.task_id==='string')) throw new Error('VIEWER_SUBMISSION_UNVERIFIED');
    return raw as LearningSubmission;
  }
}
