import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { UnderstandingSubmission } from '../api/understanding';
import WritebackProgress, { writebackSteps } from './WritebackProgress';
const submission: UnderstandingSubmission = {
  submission_id: 'a'.repeat(64), note_sha256: 'b'.repeat(64), user_text: '本人原话', card_id: 'card', card_version: 3,
  delivery_state: 'acknowledged', remote_state: 'available', outcome_id: 'outcome', task_id: 'task', error: null,
  task: { task_id: 'task', kind: 'generate', state: 'succeeded' },
  change: { change_id: 'change', state: 'needs_ai_review', entry_id: 'entry', base_revision: 1, committed_revision: null,
    title: '候选', path: null, original_markdown: '', candidate_markdown: '候选', evidence_refs: [] },
};
function steps(overrides: Partial<UnderstandingSubmission> = {}) {
  return writebackSteps(null, { submission: { ...submission, ...overrides }, unavailable: false });
}
describe('P08 三阶段真实状态投影（unit/SSR，非真实模型 E2E）', () => {
  it('无提交与旧草稿不编造复核详情', () => {
    const snapshot = { unavailable: false };
    expect(writebackSteps(null, snapshot)[0]).toBe('尚未提交');
    const draft = { draft_id: 'draft', card_id: 'card', markdown: '内容', status: 'draft' };
    expect(writebackSteps(draft, snapshot)[0]).toContain('尚未提交');
    expect(writebackSteps({ ...draft, status: 'submitted' }, snapshot)).toEqual(['已提交', '待核对复核结果', '尚未确认写入']);
    expect(writebackSteps({ ...draft, status: 'published' }, snapshot)).toEqual(['旧记录已提交', '未提供独立复核详情', '旧记录已写入，Writer 状态未提供']);
    expect(writebackSteps({ ...draft, status: 'published', delivery_error: 'lost' }, snapshot)[2]).toBe('写入状态未确认');
  });
  it('旧 published 不能在唯一 Writer 标题下冒充统一服务写入收据', () => {
    const draft = { draft_id: 'legacy', card_id: 'card', markdown: '旧正文', status: 'published' };
    const html = renderToStaticMarkup(createElement(WritebackProgress, { draft, snapshot: { unavailable: false } }));
    expect(html).toContain('唯一 Writer 提交');
    expect(html).toContain('旧记录已写入，Writer 状态未提供');
    expect(html).not.toContain('已确认写入');
    const authoritative = { ...submission, change: { ...submission.change!, state: 'committed', committed_revision: 4 } };
    const confirmed = renderToStaticMarkup(createElement(WritebackProgress, { draft, snapshot: { submission: authoritative, unavailable: false } }));
    expect(confirmed).toContain('已确认写入 v4');
    expect(confirmed).not.toContain('Writer 状态未提供');
  });
  it('本机待发与拒收不能显示已受理或复核成功', () => {
    expect(steps({ delivery_state: 'pending' })).toEqual(['已保存到本机待发', '尚未确认受理', '尚未确认写入']);
    expect(steps({ delivery_state: 'rejected' })[0]).toBe('提交未被受理');
  });
  it('生成任务 succeeded 不能代替候选复核或 Writer 成功', () => {
    expect(steps({ change: null })).toEqual(['服务已受理', '等待生成或读取候选', '尚未确认写入']);
    expect(steps()).toEqual(['服务已受理', '等待独立复核', '尚未确认写入']);
  });
  it.each([['failed', '候选生成失败'], ['cancelled', '已取消'], ['waiting_auth', '等待授权'], ['retry_wait', '等待服务重试']])('候选生成 %s 不伪装等待复核', (state, label) => {
    expect(steps({ change: null, task: { ...submission.task!, state } })[1]).toContain(label);
  });
  it.each([
    ['needs_user', '等待重要确认'], ['approved', '复核已通过'], ['rejected', '候选未通过复核'],
    ['no_change', '本次未产生知识变更'], ['future_state', '复核状态未确认'],
  ])('准确展示 %s，不推断写入', (state, label) => {
    const result = steps({ change: { ...submission.change!, state } });
    expect(result[1]).toBe(label);
    expect(result[2]).toBe(state === 'approved' ? '等待正式写入' : '尚未确认写入');
  });
  it.each(['committed', 'indexed'])('仅 %s 加有效版本及可用远端可确认写入', state => {
    const change = { ...submission.change!, state, committed_revision: 4 };
    expect(steps({ change })[2]).toBe('已确认写入 v4');
    expect(steps({ change: { ...change, committed_revision: null } })[2]).toBe('尚未确认写入');
    expect(steps({ change: { ...change, committed_revision: 0 } })[2]).toBe('尚未确认写入');
    expect(steps({ change, remote_state: 'unknown' })).toEqual(['提交状态待核对', '复核状态未确认', '写入状态未确认']);
    expect(writebackSteps(null, { submission: { ...submission, change }, unavailable: true })[2]).toBe('写入状态未确认');
  });
  it('有序三阶段头注明所选原话版本且不承诺公开', () => {
    const html = renderToStaticMarkup(createElement(WritebackProgress, { draft: null, snapshot: { submission, unavailable: false } }));
    expect(html).toContain('<ol');
    expect(html.match(/<li>/g)).toHaveLength(3);
    for (const label of ['提交成果', 'AI 复核', '唯一 Writer 提交', '不代表公开', 'v3']) expect(html).toContain(label);
  });
});
