import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { listWikiLearning, wikiLearningUrl, type WikiLearningSummary } from '../api/wiki-learning';

export default function WikiLearningLibrary() {
  const [items, setItems] = useState<WikiLearningSummary[]>([]);
  const [hasMore, setHasMore] = useState(false); const [busy, setBusy] = useState(true); const [error, setError] = useState(false);
  const epoch = useRef(0); const lock = useRef(false);
  async function load(append = false) {
    if (lock.current) return; lock.current = true;
    const run = ++epoch.current; setBusy(true); setError(false); if (!append) setItems([]);
    try { const value = await listWikiLearning(append ? items.length : 0); if (run === epoch.current) { setItems(previous => append ? [...previous, ...value.items] : value.items); setHasMore(value.has_more); } }
    catch { if (run === epoch.current) setError(true); }
    finally { if (run === epoch.current) { lock.current = false; setBusy(false); } }
  }
  useEffect(() => { lock.current = false; void load(); return () => { epoch.current++; lock.current = false; }; }, []);
  return <section className="zz-panel" aria-label="来自正式知识的自由学习"><h2>来自正式知识的自由学习</h2>
    <p className="zz-muted">独立来源记录，不要求先有工程卡。原材料、本人解释和候选状态分别保存。</p>
    <button className="zz-button secondary" disabled={busy} onClick={() => void load()}>刷新自由学习记录</button>
    {busy && <p role="status">正在读取自由学习记录…</p>}{error && <p role="alert">自由学习列表未能读取，不会用示例或工程卡代替。</p>}
    {items.map(item => <article className="zz-list-row" key={item.context_id}><div><strong>{item.title}</strong><p>来源正式版本 v{item.revision} · 本人原话版本 v{item.context_version}</p></div><Link className="zz-button secondary" to={wikiLearningUrl(item.context_id)}>继续自由学习</Link></article>)}
    {!busy && !error && !items.length && <p>还没有自由学习记录。可从个人 Wiki 的明确正式版本开始，不会自动创建。</p>}
    {hasMore && <button className="zz-button secondary" disabled={busy} onClick={() => void load(true)}>读取更多自由学习</button>}
  </section>;
}
