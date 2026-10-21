import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('./client', () => ({ apiFetch }));
import { attachEntryContext, entryContextUrl, entryReference, previewEntryContext, readEntryContexts, type AttachEntryContext } from './entry-context';

const cardId = '11111111-1111-4111-8111-111111111111';
const entry = { entry_id: 'entry.transactions-1', revision: 2, title: '正式历史版本', body: '  正式原文\r\n不代替本人理解  ', content_hash: '', evidence_refs: [{ source_id: 'source-1', source_revision: 1 }], data_classification: 'personal', classification_labels: ['private'] };
entry.content_hash = createHash('sha256').update(entry.body, 'utf8').digest('hex');
const input: AttachEntryContext = { request_id: '22222222-2222-4222-8222-222222222222', entry_id: entry.entry_id, revision: 2, expected_content_hash: entry.content_hash, target_card_id: cardId, expected_card_version: 1 };
const receipt = { context_id: '33333333-3333-4333-8333-333333333333', entry_id: entry.entry_id, revision: 2, content_hash: entry.content_hash, card_id: cardId, card_version: 1, created_at: '2026-10-21', state: 'linked' };

describe('正式条目精确版本学习引用', () => {
  beforeEach(() => apiFetch.mockReset());
  it('接受正式 opaque ID，但必须显式提供唯一正整数版本', () => {
    expect(entryReference(new URLSearchParams('entry_id=entry.transactions-1&revision=2'))).toEqual({ entryId: entry.entry_id, revision: 2 });
    for (const query of ['entry_id=x', 'entry_id=x&revision=latest', 'entry_id=x&revision=0', 'entry_id=x&revision=1.5', 'entry_id=x&revision=9007199254740992', 'entry_id=x&entry_id=y&revision=2', 'entry_id=x&revision=2&revision=3', 'entry_id=..%2Fprivate&revision=2']) expect(entryReference(new URLSearchParams(query))).toBeNull();
    expect(entryContextUrl(entry.entry_id, 2)).toBe('/growth?view=entry-context&entry_id=entry.transactions-1&revision=2');
  });
  it('只读精确历史正文并校验 UTF8 哈希，不改成 latest', async () => {
    apiFetch.mockResolvedValueOnce({ entry, cards: [{ card_id: cardId, card_version: 1, title: '既有卡' }] });
    const result = await previewEntryContext({ entryId: entry.entry_id, revision: 2 });
    expect(result.entry.body).toBe(entry.body);
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith('/api/growth/local/knowledge/entry-context?entry_id=entry.transactions-1&revision=2');
  });
  it.each([{ revision: 3 }, { entry_id: 'different' }, { content_hash: '0'.repeat(64) }])('拒绝服务返回替换版本或正文摘要 %j', async mutation => {
    apiFetch.mockResolvedValueOnce({ entry: { ...entry, ...mutation }, cards: [] });
    await expect(previewEntryContext({ entryId: entry.entry_id, revision: 2 })).rejects.toThrow();
  });
  it('不把内部定位、任意 metadata 带入可显示的授权引用', async () => {
    apiFetch.mockResolvedValueOnce({ entry: { ...entry, evidence_refs: [{ source_id: 's1', source_revision: 1, locator: 'secret-sentinel', metadata: { token: 'secret-sentinel' } }] }, cards: [] });
    expect(JSON.stringify(await previewEntryContext({ entryId: entry.entry_id, revision: 2 }))).not.toContain('secret-sentinel');
  });
  it('显式关联仅提交冻结身份版本与哈希，不自动重放、不传正文或本人笔记', async () => {
    apiFetch.mockResolvedValueOnce(receipt);
    expect(await attachEntryContext(input)).toEqual(receipt);
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith('/api/growth/local/knowledge/entry-contexts', { method: 'POST', retryAuth: false, body: JSON.stringify(input) });
  });
  it('版本冲突不重试，不把失败解释为已关联', async () => {
    apiFetch.mockRejectedValueOnce(new Error('409'));
    await expect(attachEntryContext(input)).rejects.toThrow('409');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
  it.each([{ card_id: '44444444-4444-4444-8444-444444444444' }, { revision: 3 }, { content_hash: 'f'.repeat(64) }, { card_version: 2 }, { state: 'completed' }])('拒绝越过已确认关联范围的回执 %j', async mutation => {
    apiFetch.mockResolvedValueOnce({ ...receipt, ...mutation });
    await expect(attachEntryContext(input)).rejects.toThrow();
  });
  it('卡内恢复重新 GET 授权版本，不用缓存正文掩盖权限失败', async () => {
    apiFetch.mockResolvedValueOnce({ items: [{ ...receipt, entry }], has_more: false }).mockRejectedValueOnce(new Error('403'));
    expect((await readEntryContexts(cardId)).items[0].entry.body).toBe(entry.body);
    await expect(readEntryContexts(cardId)).rejects.toThrow('403');
    expect(apiFetch.mock.calls).toEqual([[`/api/growth/local/knowledge/cards/${cardId}/entry-contexts`], [`/api/growth/local/knowledge/cards/${cardId}/entry-contexts`]]);
  });
  it('拒绝关联与嵌套正文版本不一致，即使都具备合法结构', async () => {
    apiFetch.mockResolvedValueOnce({ items: [{ ...receipt, entry: { ...entry, revision: 3 } }], has_more: false });
    await expect(readEntryContexts(cardId)).rejects.toThrow();
  });
});
