import { beforeEach, describe, expect, it, vi } from 'vitest';
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('./client', () => ({ apiFetch }));
import {
  listUnderstandingSubmissions, readUnderstandingSubmission, submitUnderstanding,
  understandingChangeLabel, understandingFailureSummary, understandingHash, type UnderstandingSubmission,
} from './understanding';
import { createHash } from 'node:crypto';

const text = '  本人的理解\r\n保留换行与空格  ';
const hash = createHash('sha256').update(text, 'utf8').digest('hex');
const submission: UnderstandingSubmission = {
  submission_id: '1'.repeat(64), card_id: 'card-1', card_version: 2, note_sha256: hash, user_text: text,
  delivery_state: 'pending', outcome_id: null, task_id: null, remote_state: 'not_submitted',
  task: null, change: null, error: null,
};
const change = {
  change_id: 'change-1', state: 'needs_ai_review', entry_id: 'entry-1', base_revision: 3,
  committed_revision: null, title: '候选', path: 'architecture.md', original_markdown: '旧版', candidate_markdown: '新版', evidence_refs: [{ source_id: 'evidence-1', learning_outcome_id: 'outcome-1', revision: 1 }],
};
describe('开放理解持久提交契约', () => {
  beforeEach(() => apiFetch.mockReset());
  it('按精确 UTF-8 原文计算哈希，不 trim 或改变换行', async () => {
    expect(await understandingHash(text)).toBe(hash);
    expect(await understandingHash(text.trim())).not.toBe(hash);
    expect(await understandingHash(text.replace('\r\n', '\n'))).not.toBe(hash);
  });
  it('只发送保存版本和原文哈希，不伪造身份、完成状态或评分', async () => {
    apiFetch.mockResolvedValueOnce(submission);
    await expect(submitUnderstanding({ cardId: 'card-1', cardVersion: 2, text })).resolves.toEqual(submission);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith('/api/growth/local/knowledge/cards/card-1/understanding/submissions', {
      method: 'POST', retryAuth: false, body: JSON.stringify({ card_version: 2, note_sha256: hash }),
    });
  });
  it('失败和过期版本不自动重试、不返回虚构任务', async () => {
    apiFetch.mockRejectedValueOnce(new Error('409'));
    await expect(submitUnderstanding({ cardId: 'card-1', cardVersion: 2, text })).rejects.toThrow('409');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
  it.each(['card_id', 'card_version', 'note_sha256', 'user_text'] as const)('拒绝不匹配的提交快照 %s', async field => {
    apiFetch.mockResolvedValueOnce({ ...submission, [field]: field === 'card_version' ? 3 : field === 'note_sha256' ? 'a'.repeat(64) : 'different' });
    await expect(submitUnderstanding({ cardId: 'card-1', cardVersion: 2, text })).rejects.toThrow();
  });
  it('空原话与无效版本不发请求', async () => {
    await expect(submitUnderstanding({ cardId: 'card-1', cardVersion: 2, text: ' ' })).rejects.toThrow();
    await expect(submitUnderstanding({ cardId: 'card-1', cardVersion: 0, text })).rejects.toThrow();
    expect(apiFetch).not.toHaveBeenCalled();
  });
  it('列表与单项只读恢复，拒绝错卡或错提交响应', async () => {
    apiFetch.mockResolvedValueOnce({ items: [submission], has_more: false }).mockResolvedValueOnce(submission);
    expect((await listUnderstandingSubmissions('card-1')).items).toEqual([submission]);
    expect(await readUnderstandingSubmission('card-1', '1'.repeat(64))).toEqual(submission);
    expect(apiFetch.mock.calls.every(call => call.length === 1)).toBe(true);
    apiFetch.mockResolvedValueOnce({ items: [{ ...submission, card_id: 'another' }], has_more: false });
    await expect(listUnderstandingSubmissions('card-1')).rejects.toThrow();
    apiFetch.mockResolvedValueOnce({ ...submission, submission_id: 'other' });
    await expect(readUnderstandingSubmission('card-1', '1'.repeat(64))).rejects.toThrow();
  });
  it('拒绝不完整的 202 和不带精确基线的候选', async () => {
    apiFetch.mockResolvedValueOnce({ state: 'submitted' });
    await expect(submitUnderstanding({ cardId: 'card-1', cardVersion: 2, text })).rejects.toThrow();
    apiFetch.mockResolvedValueOnce({ ...submission, change: { ...change, original_markdown: null } });
    await expect(readUnderstandingSubmission('card-1', '1'.repeat(64))).rejects.toThrow();
  });
  it('任务成功、no_change、未知或缺写入版本都不算正式写入', () => {
    const confirmed = { ...submission, remote_state: 'available' as const, delivery_state: 'acknowledged' as const, task: { task_id: 'task', kind: 'knowledge.generate', state: 'succeeded' } };
    expect(understandingChangeLabel(confirmed)).toContain('尚未确认正式写入');
    expect(understandingChangeLabel({ ...confirmed, change: { ...change, state: 'no_change' } })).toContain('不等于正式写入');
    for (const state of ['approved', 'surprise', 'committed', 'indexed']) expect(understandingChangeLabel({ ...confirmed, change: { ...change, state } })).not.toContain('已确认正式写入');
    expect(understandingChangeLabel({ ...confirmed, change: { ...change, state: 'committed', committed_revision: 4 } })).toBe('已确认正式写入，不代表公开。');
    expect(understandingChangeLabel({ ...confirmed, remote_state: 'not_submitted', change: { ...change, state: 'committed', committed_revision: 4 } })).not.toContain('已确认正式写入');
    expect(understandingChangeLabel({ ...confirmed, remote_state: 'unknown', change: { ...change, state: 'committed', committed_revision: 4 } })).toContain('远端状态未确认');
  });
  it('失败摘要不接受或回显服务异常和秘密', () => {
    expect(understandingFailureSummary()).toBe('操作未获确认。原话与已有记录保留；请刷新核对提交记录和版本，不会自动重新提交。');
  });
});
