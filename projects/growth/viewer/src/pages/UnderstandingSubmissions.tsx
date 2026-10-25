import { useEffect, useRef, useState } from 'react';
import { diffLines } from 'diff';
import type { WritebackSnapshot } from './WritebackProgress';
import type { KnowledgeDetail } from '../api/knowledge';
import {
  listUnderstandingSubmissions, readUnderstandingSubmission, submitUnderstanding,
  understandingChangeLabel, understandingFailureSummary, type UnderstandingSubmission,
} from '../api/understanding';

export default function UnderstandingSubmissions({ detail, unified, onProgress }: { detail: KnowledgeDetail; unified: boolean; onProgress?: (value: WritebackSnapshot) => void }) {
  const [items, setItems] = useState<UnderstandingSubmission[]>([]);
  const [selected, setSelected] = useState('');
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [tab, setTab] = useState<'diff' | 'content' | 'sources'>('diff');
  const lock = useRef(false);
  const live = useRef(true);
  const epoch = useRef(0);
  const cardId = detail.card.card_id;
  const current = items.find(item => item.submission_id === selected) ?? items[0];
  const same = items.find(item => item.card_version === detail.card.card_version && item.user_text === detail.learning.note_text);

  useEffect(() => {
    onProgress?.({ submission: current, unavailable: unified && (!ready || !!error) });
  }, [current, ready, error, unified, onProgress]);

  useEffect(() => {
    let active = true; epoch.current++; live.current = true; lock.current = false; setBusy(false); setReady(false); setItems([]); setSelected(''); setError('');
    if (unified) void listUnderstandingSubmissions(cardId).then(value => {
      if (active) { setItems(value.items); setHasMore(value.has_more); setReady(true); }
    }).catch(() => { if (active) setError(understandingFailureSummary()); });
    return () => { active = false; live.current = false; epoch.current++; };
  }, [cardId, unified]);
  async function act(action: (isCurrent: () => boolean) => Promise<void>) {
    if (lock.current) return;
    const requestEpoch = epoch.current;
    const isCurrent = () => live.current && epoch.current === requestEpoch;
    lock.current = true; setBusy(true); setError('');
    try { await action(isCurrent); }
    catch { if (isCurrent()) { setError(understandingFailureSummary()); setReady(false); } }
    finally { if (isCurrent()) { lock.current = false; setBusy(false); } }
  }
  async function reload(isCurrent: () => boolean) {
    // A failed POST must be reconciled with the list, not retried as a side effect of refresh.
    if (!ready || !current) {
      const value = await listUnderstandingSubmissions(cardId);
      if (isCurrent()) { setItems(value.items); setHasMore(value.has_more); setReady(true); }
    } else {
      const value = await readUnderstandingSubmission(cardId, current.submission_id);
      if (isCurrent()) setItems(previous => previous.map(item => item.submission_id === value.submission_id ? value : item));
    }
  }
  return <section className="zz-panel" aria-label="开放理解提交与候选">
    <h2>从本人理解生成候选</h2>
    <p>提交已保存的原话后，知识服务会生成并独立复核候选。普通写入按既有部署策略处理，重大决定仍需在个人 Wiki 确认。这不只是预览，也不自动公开。</p>
    <p className="zz-muted">当前保存版本 v{detail.card.card_version}。不会以 AI 讲解代替原话，不要求完成两道题，也不修改掌握状态。</p>
    {!unified && <p className="zz-notice">统一知识服务尚未连接，理解提交暂不可用；不会回退为直接发布。</p>}
    {unified && !ready && !error && <p role="status">正在恢复理解提交记录…</p>}
    {error && <p role="alert" className="zz-notice error">{error}</p>}
    {!detail.learning.note_text.trim() && <p className="zz-notice">请先在“我的理解”中保存本人原话。</p>}
    {same && <p className="zz-muted">该版本原话已有提交，未重复发送；修改并保存本人草稿后，才可提交新的理解。</p>}
    <div className="zz-actions">
      <button className="zz-button" disabled={!unified || !ready || busy || !detail.learning.note_text.trim() || !!same} onClick={() => void act(async isCurrent => {
        const value = await submitUnderstanding({ cardId, cardVersion: detail.card.card_version, text: detail.learning.note_text });
        if (isCurrent()) { setItems(previous => [value, ...previous.filter(item => item.submission_id !== value.submission_id)]); setSelected(value.submission_id); }
      })}>提交理解，生成并复核候选</button>
      <button className="zz-button secondary" disabled={!unified || busy} onClick={() => void act(reload)}>刷新理解提交状态</button>
    </div>
    {items.length > 1 && <label>查看提交记录<select aria-label="查看理解提交记录" value={current?.submission_id ?? ''} onChange={event => { setSelected(event.target.value); setTab('diff'); }}>{items.map(item => <option key={item.submission_id} value={item.submission_id}>v{item.card_version} · {item.submission_id}</option>)}</select></label>}
    {hasMore && <p className="zz-muted">当前显示部分提交记录，未将未加载的历史视为不存在。</p>}
    {current && <>
      <p role="status" className="zz-notice">{understandingChangeLabel(current)}</p>
      <p className="zz-muted">submission_id: {current.submission_id}<br/>outcome_id: {current.outcome_id ?? '尚未确认'}<br/>task_id: {current.task_id ?? '尚未确认'}</p>
      {current.delivery_state === 'pending' && current.error?.retryable === true && <div className="zz-actions"><button className="zz-button secondary" disabled={!ready || busy} onClick={() => void act(async isCurrent => {
        const value = await submitUnderstanding({ cardId, cardVersion: current.card_version, text: current.user_text });
        if (isCurrent()) setItems(previous => previous.map(item => item.submission_id === value.submission_id ? value : item));
      })}>重试递送这份原话</button><span className="zz-muted">仅递送这份已保存的提交，不以当前笔记替换，也不创建新的理解。</span></div>}
      <details><summary>本次提交的本人原话 · v{current.card_version}</summary><pre className="zz-prose">{current.user_text}</pre></details>
      {(current.user_text !== detail.learning.note_text || current.card_version !== detail.card.card_version) && <p className="zz-notice">本次候选对应此前提交的原话，不会覆盖当前已保存的理解。</p>}
      <p>生成任务：{current.task ? ({ queued: '排队中', running: '处理中', succeeded: '任务结束，须另核对候选与写入', failed: '失败', cancelled: '已取消', waiting_auth: '等待授权', retry_wait: '等待服务重试' } as Record<string, string>)[current.task.state] ?? '状态未确认' : '尚未读取到任务状态'}</p>
      {current.error && <p className="zz-notice">部分处理未获确认。{current.error.retryable ? '可刷新核对，刷新不会重新提交原话。' : '请核对服务配置与当前版本。'}</p>}
      {current.change ? <>
        <h3>{current.change.title}</h3>
        <p className="zz-muted">change_id: {current.change.change_id}<br/>目标：{current.change.path ?? current.change.entry_id} · 基线 v{current.change.base_revision}{current.change.committed_revision !== null && <> · 写入版本 v{current.change.committed_revision}</>}</p>
        <div className="zz-actions" role="group" aria-label="理解候选内容视图">{(['diff', 'content', 'sources'] as const).map(value => <button key={value} className={'zz-button' + (tab === value ? '' : ' secondary')} aria-pressed={tab === value} onClick={() => setTab(value)}>{value === 'diff' ? '差异预览' : value === 'content' ? '候选内容' : '引用与证据'}</button>)}</div>
        {tab === 'diff' && <div className="zz-diff" aria-label="本人理解候选与基线差异">{diffLines(current.change.original_markdown, current.change.candidate_markdown).map((part, i) => <pre key={i} className={part.added ? 'added' : part.removed ? 'removed' : ''}>{part.added ? '+ ' : part.removed ? '− ' : '  '}{part.value}</pre>)}</div>}
        {tab === 'content' && <pre className="zz-prose" aria-label="只读理解候选">{current.change.candidate_markdown}</pre>}
        {tab === 'sources' && <div aria-label="理解候选授权引用">{current.change.evidence_refs.length ? current.change.evidence_refs.map((ref, i) => <p key={i}>{ref.source_id && <>来源 ID：{ref.source_id} </>}{ref.learning_outcome_id && <>理解成果 ID：{ref.learning_outcome_id} </>}{ref.external_document_id && <>外部文档 ID：{ref.external_document_id} </>}{ref.revision !== undefined && <>版本：{ref.revision}</>}{!ref.source_id && !ref.learning_outcome_id && !ref.external_document_id && '未返回可识别的引用 ID'}</p>) : <p>未返回可展示的授权引用，不推定证据已验证。</p>}</div>}
        <p className="zz-muted">此 ChangeSet 只读。需要修订时，返回“我的理解”修改并保存原话，再显式提交新的理解。</p>
      </> : <p>尚无可验证的候选差异，不以当前正文或示例内容代替。</p>}
      <p className="zz-muted">公开状态：此接口未提供，不推定已公开。</p>
    </>}
  </section>;
}
