import { useEffect, useRef, useState } from 'react';
import { diffLines } from 'diff';
import { understandingChangeLabel, understandingFailureSummary } from '../api/understanding';
import { listWikiSubmissions, readWikiSubmission, submitWikiUnderstanding, type WikiLearningContext, type WikiLearningSubmission } from '../api/wiki-learning';

export default function WikiUnderstandingSubmissions({ context, onAccessUnconfirmed }: { context: WikiLearningContext; onAccessUnconfirmed: () => void }) {
  const [items, setItems] = useState<WikiLearningSubmission[]>([]); const [selected, setSelected] = useState('');
  const [ready, setReady] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [hasMore, setHasMore] = useState(false);
  const [tab, setTab] = useState<'diff'|'content'|'sources'>('diff'); const epoch = useRef(0); const lock = useRef(false);
  const id = context.context_id; const current = items.find(item => item.submission_id === selected) ?? items[0];
  const same = items.some(item => item.context_version === context.context_version && item.user_text === context.note_text);
  useEffect(() => {
    const run = ++epoch.current; lock.current = false; setItems([]); setSelected(''); setReady(false); setError(''); setBusy(false);
    void listWikiSubmissions(id).then(value => { if (run === epoch.current) { setItems(value.items); setHasMore(value.has_more); setReady(true); } }).catch(() => { if (run === epoch.current) { setError(understandingFailureSummary()); onAccessUnconfirmed(); } });
    return () => { epoch.current++; };
  }, [id]);
  async function act(action: (active: () => boolean) => Promise<void>) {
    if (lock.current) return; const run = epoch.current; const active = () => run === epoch.current;
    lock.current = true; setBusy(true); setError('');
    try { await action(active); } catch { if (active()) { setError(understandingFailureSummary()); setReady(false); onAccessUnconfirmed(); } }
    finally { if (active()) { setBusy(false); lock.current = false; } }
  }
  async function submit(version: number, text: string, active: () => boolean) {
    const value = await submitWikiUnderstanding(id, version, text);
    if (active()) { setItems(previous => [value, ...previous.filter(item => item.submission_id !== value.submission_id)]); setSelected(value.submission_id); }
  }
  const change = current?.change?.entry_id === context.entry.entry_id ? null : current?.change;
  return <section className="zz-panel" aria-label="自由理解提交与候选"><h2>提交独立的本人理解</h2>
    <p>提交已保存的本人解释，生成并独立复核候选。候选是独立的本人理解条目，不覆盖作为来源的正式历史版本；处理与公开分别确认。</p>
    {!ready && !error && <p role="status">正在读取自由理解提交…</p>}{error && <p role="alert">{error}</p>}
    <div className="zz-actions"><button className="zz-button" disabled={!ready || busy || same || !context.note_text.trim()} onClick={() => void act(active => submit(context.context_version, context.note_text, active))}>提交自由理解，生成并复核候选</button>
      <button className="zz-button secondary" disabled={busy} onClick={() => void act(async active => {
        if (!ready || !current) { const value = await listWikiSubmissions(id); if (active()) { setItems(value.items); setHasMore(value.has_more); setReady(true); } }
        else { const value = await readWikiSubmission(id, current.submission_id); if (active()) setItems(previous => previous.map(item => item.submission_id === value.submission_id ? value : item)); }
      })}>刷新自由理解提交</button></div>
    {same && <p>这份已保存原话已有提交，不会重复发送。</p>}{hasMore && <p>当前仅显示部分提交记录。</p>}
    {items.length > 1 && <label>查看自由理解提交<select aria-label="查看自由理解提交" value={current?.submission_id} onChange={event => setSelected(event.target.value)}>{items.map(item => <option key={item.submission_id} value={item.submission_id}>v{item.context_version} · {item.submission_id}</option>)}</select></label>}
    {current && <>
      {current.change?.entry_id === context.entry.entry_id ? <p role="alert">候选目标与来源条目相同，未确认独立理解写入；请核对服务结果。</p> : <p role="status">{understandingChangeLabel(current)}</p>}
      <p className="zz-muted">context_id: {current.context_id}<br/>outcome_id: {current.outcome_id ?? '尚未确认'}<br/>task_id: {current.task_id ?? '尚未确认'}</p>
      <p>递送状态：{current.delivery_state === 'pending' ? '待发' : current.delivery_state === 'acknowledged' ? '服务已受理' : '未受理'} · 生成任务：{current.task ? ({ queued: '排队中', running: '处理中', succeeded: '任务结束，写入另行核对', failed: '失败', cancelled: '已取消' } as Record<string, string>)[current.task.state] ?? '状态未识别，不推定完成' : '尚未取得任务状态'}</p>
      <p className="zz-muted">公开状态：此接口未提供，不推定已公开。</p>
      <details><summary>本次提交的本人解释 · v{current.context_version}</summary><pre className="zz-prose">{current.user_text}</pre></details>
      {(current.user_text !== context.note_text || current.context_version !== context.context_version) && <p>这份提交保留此前原话，不会被当前编辑替换。</p>}
      {current.delivery_state === 'pending' && current.error?.retryable === true && <button className="zz-button secondary" disabled={!ready || busy} onClick={() => void act(active => submit(current.context_version, current.user_text, active))}>重试递送这份自由理解</button>}
      {change ? <><h3>{change.title}</h3><p className="zz-muted">目标：{change.path ?? change.entry_id} · 基线 v{change.base_revision} · change_id: {change.change_id}</p>
        <div className="zz-actions" role="group" aria-label="自由理解候选视图">{(['diff','content','sources'] as const).map(value => <button className="zz-button secondary" key={value} aria-pressed={tab === value} onClick={() => setTab(value)}>{value === 'diff' ? '差异预览' : value === 'content' ? '候选内容' : '引用与证据'}</button>)}</div>
        {tab === 'diff' && <div className="zz-diff" aria-label="自由理解候选差异">{diffLines(change.original_markdown, change.candidate_markdown).map((part, index) => <pre key={index} className={part.added ? 'added' : part.removed ? 'removed' : ''}>{part.added ? '+ ' : part.removed ? '− ' : '  '}{part.value}</pre>)}</div>}
        {tab === 'content' && <pre className="zz-prose" aria-label="自由理解只读候选">{change.candidate_markdown}</pre>}
        {tab === 'sources' && <div aria-label="自由理解授权引用">{change.evidence_refs.map((ref, index) => <p key={index}>{Object.entries(ref).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>)}</div>}
      </> : <p>尚无可核验的独立理解候选差异。</p>}
      <p className="zz-muted">候选只读；修改本人原话后可显式另交。正式写入不代表公开。</p>
    </>}
  </section>;
}
