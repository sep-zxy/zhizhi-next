import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { entryContextUrl } from '../api/entry-context';
import { readWikiLearning, saveWikiNote, validWikiContextId, wikiLearningFailure, type SaveWikiNote, type WikiLearningContext } from '../api/wiki-learning';
import WikiUnderstandingSubmissions from './WikiUnderstandingSubmissions';
import { learningUrl } from './zhizhi-model';

export function WikiLearningPrivateArea({ context, children }: { context: WikiLearningContext | null; children: ReactNode }) {
  return context ? <>{children}</> : <p>尚未确认本次来源与学习权限。本人输入仍保留，但授权成功前不显示缓存原话或候选。</p>;
}

type Draft = { text: string; version: number };
export default function WikiLearningPage({ contextId }: { contextId: string }) {
  const [context, setContext] = useState<WikiLearningContext | null>(null); const [text, setText] = useState('');
  const [error, setError] = useState(''); const [status, setStatus] = useState(''); const [busy, setBusy] = useState(false); const [needsReview, setNeedsReview] = useState(false); const [approved, setApproved] = useState(false);
  const epoch = useRef(0); const lock = useRef(false); const dirty = useRef(false); const baseVersion = useRef(0); const textRef = useRef(''); const request = useRef<SaveWikiNote | null>(null);
  const key = 'zhizhi:wiki-understanding:' + contextId;
  function cache(value: string, version: number) { try { localStorage.setItem(key, JSON.stringify({ text: value, version })); } catch { setStatus('本机草稿存储不可用，请保持页面并保存原话。'); } }
  function apply(value: WikiLearningContext) {
    setContext(value);
    if (!dirty.current || value.note_text === textRef.current) {
      setText(value.note_text); textRef.current = value.note_text; dirty.current = false; baseVersion.current = value.context_version; setNeedsReview(false); setApproved(false); cache(value.note_text, value.context_version);
    } else { setNeedsReview(baseVersion.current !== value.context_version); setApproved(false); }
  }
  useEffect(() => {
    const run = ++epoch.current; lock.current = false; dirty.current = false; baseVersion.current = 0; textRef.current = ''; request.current = null; setText(''); setContext(null); setError(''); setStatus(''); setApproved(false); setNeedsReview(false);
    if (!validWikiContextId(contextId)) return;
    try { const local = JSON.parse(localStorage.getItem(key) ?? 'null') as Draft | null; if (local && typeof local.text === 'string' && local.text.length <= 20000 && Number.isSafeInteger(local.version) && local.version > 0) { textRef.current = local.text; setText(local.text); dirty.current = true; baseVersion.current = local.version; } } catch { /* A local draft is optional, never a formal source. */ }
    setBusy(true);
    void readWikiLearning(contextId).then(value => { if (run === epoch.current) apply(value); }).catch(() => { if (run === epoch.current) setError(wikiLearningFailure); }).finally(() => { if (run === epoch.current) setBusy(false); });
    return () => { epoch.current++; };
  }, [contextId, key]);
  async function act(action: (active: () => boolean) => Promise<void>) {
    if (lock.current) return; const run = epoch.current; const active = () => epoch.current === run; lock.current = true; setBusy(true); setError(''); setStatus('');
    try { await action(active); } catch { if (active()) { setContext(null); setError(wikiLearningFailure); setApproved(false); } }
    finally { if (active()) { setBusy(false); lock.current = false; } }
  }
  if (!validWikiContextId(contextId)) return <p role="alert">自由学习链接缺少有效 context_id，不会借用工程卡身份。</p>;
  return <><div className="zz-toolbar"><Link to={learningUrl('library')}>← 返回学习库</Link><span className="zz-badge">独立 Wiki 来源学习</span></div>
    {error && <p role="alert" className="zz-notice error">{error}</p>}{status && <p role="status">{status}</p>}
    <button className="zz-button secondary" disabled={busy} onClick={() => void act(async active => { setContext(null); const value = await readWikiLearning(contextId); if (active()) { apply(value); setStatus(value.note_text === textRef.current ? '已重新核对服务保存的原话。' : '服务端版本已读取，你尚未保存的输入仍保留。'); } })}>重新读取自由学习</button>
    {busy && <p role="status">正在读取或保存自由学习…</p>}
    <div className="zz-grid"><section className="zz-panel" aria-label="自由学习原材料"><h2>正式原材料</h2><p className="zz-muted">只读精确版本，不是本人原话，不自动作为模型输入。</p>{context ? <><h3>{context.entry.title}</h3><p>正式版本 v{context.entry.revision} · {context.entry.data_classification} · {context.entry.classification_labels.join('、') || '无附加标签'}</p><pre className="zz-prose" aria-label="自由学习正式原文">{context.entry.body}</pre><details><summary>来源授权引用标识</summary>{context.entry.evidence_refs.map((ref, index) => <p key={index}>{Object.entries(ref).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>)}</details><Link to={entryContextUrl(context.entry.entry_id, context.entry.revision)}>核对此来源版本</Link></> : <p>尚未重新验证来源权限，不显示缓存正文。</p>}</section>
      <section className="zz-panel"><h2>我的独立解释</h2><p>不需要工程卡，也不把阅读或两道题换算为掌握。</p><WikiLearningPrivateArea context={context}><label>自由学习本人原话<textarea aria-label="自由学习本人原话" maxLength={20000} disabled={busy} value={text} onChange={event => { const value = event.target.value; setText(value); textRef.current = value; dirty.current = true; setApproved(false); request.current = null; cache(value, baseVersion.current); }}/></label>
        {context && <details><summary>服务已保存的本人原话 · v{context.context_version}</summary><pre className="zz-prose">{context.note_text || '尚未保存本人原话。'}</pre></details>}
        {needsReview && context && <label className="zz-notice"><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)}/>已核对服务端原话，以当前版本保存我的输入</label>}
        <button className="zz-button" disabled={busy || !context || text === context.note_text || (needsReview && !approved)} onClick={() => void act(async active => {
          if (!context) return; const previous = request.current;
          const body = { request_id: previous?.note_text === text && previous.expected_context_version === context.context_version ? previous.request_id : crypto.randomUUID(), expected_context_version: context.context_version, note_text: text };
          request.current = body; const value = await saveWikiNote(contextId, body);
          if (active()) { apply(value); setStatus('本人原话已保存，尚未提交生成、复核或公开。'); }
        })}>保存自由学习原话</button></WikiLearningPrivateArea>
      </section></div>
    {context && <WikiUnderstandingSubmissions key={contextId} context={context} onAccessUnconfirmed={() => { setContext(null); setError(wikiLearningFailure); }}/>}
  </>;
}
