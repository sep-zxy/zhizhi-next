import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('./client', () => ({ apiFetch }));
import { createWikiLearning, listWikiLearning, readWikiLearning, saveWikiNote, listWikiSubmissions, readWikiSubmission, submitWikiUnderstanding, wikiLearningUrl, validWikiContextId } from './wiki-learning';
import { understandingChangeLabel } from './understanding';
const digest = (text: string) => createHash('sha256').update(text, 'utf8').digest('hex');
const id = '44444444-4444-4444-8444-444444444444';
const other = '55555555-5555-4555-8555-555555555555';
const base = '/api/growth/local/knowledge/entry-learning-contexts';
const entry = { entry_id: 'formal.entry-1', revision: 2, title: '正式材料', body: '正式来源正文', content_hash: digest('正式来源正文'), evidence_refs: [], data_classification: 'personal', classification_labels: ['private'] };
const text = '  本人的解释\r\n保留精确空格  ';
const context = { context_id: id, source_kind: 'wiki_entry', context_version: 3, entry, note_text: text, note_sha256: digest(text), created_at: '2026-10-21', updated_at: '2026-10-21', state: 'reading' };
const create = { request_id: other, entry_id: entry.entry_id, revision: 2, expected_content_hash: entry.content_hash };
const submission = { submission_id: 'a'.repeat(64), context_id: id, context_version: 3, note_sha256: digest(text), user_text: text, delivery_state: 'pending', outcome_id: null, task_id: null, remote_state: 'not_submitted', task: null, change: null, error: null };

describe('正式来源自由学习，不借用工程卡身份', () => {
  beforeEach(() => apiFetch.mockReset());
  it('独立深链接仅接有效 context UUID', () => {
    expect(wikiLearningUrl(id)).toBe('/growth?view=wiki-learning&context_id=' + id);
    expect(validWikiContextId(id)).toBe(true); expect(validWikiContextId('card-1')).toBe(false);
  });
  it('显式创建只发送精确来源引用与幂等键，不发送正文或工程卡', async () => {
    apiFetch.mockResolvedValueOnce(context);
    expect(await createWikiLearning(create)).toEqual(context);
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith(base, { method: 'POST', retryAuth: false, body: JSON.stringify(create) });
  });
  it.each([{ entry: { ...entry, revision: 4 } }, { entry: { ...entry, entry_id: 'other' } }, { entry: { ...entry, content_hash: '0'.repeat(64) } }, { note_sha256: '0'.repeat(64) }, { source_kind: 'engineering_card' }])('不接受替换的来源、原话摘要或身份 %j', async mutation => {
    apiFetch.mockResolvedValueOnce({ ...context, ...mutation });
    await expect(createWikiLearning(create)).rejects.toThrow();
  });
  it('列表只保留最小摘要，分页不读取或传递本人原话/正式正文', async () => {
    apiFetch.mockResolvedValueOnce({ items: [{ ...context, entry_id: entry.entry_id, revision: 2, content_hash: entry.content_hash, title: entry.title }], has_more: true });
    const result = await listWikiLearning(20);
    expect(result.items[0]).not.toHaveProperty('entry'); expect(result.items[0]).not.toHaveProperty('note_text');
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith(base + '?limit=20&offset=20');
  });
  it('GET 恢复只读精确上下文，拒绝另一身份，不递送待发事项', async () => {
    apiFetch.mockResolvedValueOnce(context).mockResolvedValueOnce({ ...context, context_id: other });
    expect(await readWikiLearning(id)).toEqual(context); await expect(readWikiLearning(id)).rejects.toThrow();
    expect(apiFetch.mock.calls).toEqual([[base + '/' + id], [base + '/' + id]]);
  });
  it('相同原话保存可返回原版本，CAS 请求不假定固定加一也不 trim', async () => {
    apiFetch.mockResolvedValueOnce(context);
    const body = { request_id: other, expected_context_version: 3, note_text: text };
    expect((await saveWikiNote(id, body)).context_version).toBe(3);
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith(base + '/' + id + '/note', { method: 'POST', retryAuth: false, body: JSON.stringify(body) });
  });
  it.each([{ context_version: 2 }, { note_text: text.trim(), note_sha256: digest(text.trim()) }])('保存回执不能换原话或倒退版本 %j', async mutation => {
    apiFetch.mockResolvedValueOnce({ ...context, ...mutation });
    await expect(saveWikiNote(id, { request_id: other, expected_context_version: 3, note_text: text })).rejects.toThrow();
  });
  it('输入上限在网络前校验，不生成替代材料', async () => {
    await expect(saveWikiNote(id, { request_id: other, expected_context_version: 3, note_text: 'x'.repeat(20001) })).rejects.toThrow();
    expect(apiFetch).not.toHaveBeenCalled();
  });
  it.each([401, 403, 409, 503])('创建失败 %i 不自动重放', async code => {
    apiFetch.mockRejectedValueOnce(new Error(String(code)));
    await expect(createWikiLearning(create)).rejects.toThrow(String(code)); expect(apiFetch).toHaveBeenCalledTimes(1);
  });
  it('提交精确已保存原话的 context 版本及哈希，不发送伪造 card_id', async () => {
    apiFetch.mockResolvedValueOnce(submission);
    const value = await submitWikiUnderstanding(id, 3, text);
    expect(value).not.toHaveProperty('card_id');
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith(base + '/' + id + '/understanding/submissions', { method: 'POST', retryAuth: false, body: JSON.stringify({ context_version: 3, note_sha256: digest(text) }) });
    expect(understandingChangeLabel(value)).toContain('尚未确认服务受理');
  });
  it('旧版 pending 重递仍使用不可变旧快照，不用当前原话', async () => {
    apiFetch.mockResolvedValueOnce({ ...submission, context_version: 1 });
    const value = await submitWikiUnderstanding(id, 1, text);
    expect(value.context_version).toBe(1); expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ context_version: 1, note_sha256: digest(text) });
  });
  it('提交响应失联后只 GET 恢复，单项必须同一个提交身份', async () => {
    apiFetch.mockRejectedValueOnce(new Error('lost')).mockResolvedValueOnce({ items: [submission], has_more: false }).mockResolvedValueOnce({ ...submission, submission_id: 'b'.repeat(64) });
    await expect(submitWikiUnderstanding(id, 3, text)).rejects.toThrow('lost');
    expect((await listWikiSubmissions(id)).items).toHaveLength(1);
    await expect(readWikiSubmission(id, submission.submission_id)).rejects.toThrow();
    expect(apiFetch.mock.calls.slice(1)).toEqual([[base + '/' + id + '/understanding/submissions'], [base + '/' + id + '/understanding/submissions/' + submission.submission_id]]);
  });
  it('自由理解不得接受另一 context 的提交记录', async () => {
    apiFetch.mockResolvedValueOnce({ items: [{ ...submission, context_id: other }], has_more: false });
    await expect(listWikiSubmissions(id)).rejects.toThrow();
  });
});
