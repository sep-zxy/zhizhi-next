import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { entryContextUrl, readEntryContexts, type LinkedEntryContext } from '../api/entry-context';

export default function EntryContextsPanel({ cardId }: { cardId: string }) {
  const [items, setItems] = useState<LinkedEntryContext[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const epoch = useRef(0);
  async function refresh() {
    const run = ++epoch.current; setItems([]); setLoading(true); setError(false);
    try { const value = await readEntryContexts(cardId); if (run === epoch.current) setItems(value.items); }
    catch { if (run === epoch.current) setError(true); }
    finally { if (run === epoch.current) setLoading(false); }
  }
  useEffect(() => { void refresh(); return () => { epoch.current++; }; }, [cardId]);
  return <section className="zz-panel" aria-label="关联的正式知识版本">
    <h2>关联的正式知识版本</h2>
    <p className="zz-muted">仅为阅读引用，不是本人原话。每次读取重新验证权限与精确版本，不自动换成最新版或发送模型。</p>
    <button className="zz-button secondary" disabled={loading} onClick={() => void refresh()}>重新验证正式引用</button>
    {loading && <p role="status">正在重新验证正式引用…</p>}
    {error && <p role="alert" className="zz-notice error">未能重新验证正式知识引用；不显示缓存正文，请核对服务连接与权限。</p>}
    {!loading && !error && !items.length && <p>尚未关联正式知识版本。</p>}
    {items.map(item => <details className="zz-evidence" key={item.context_id}><summary>{item.entry.title} · 正式版本 v{item.revision}</summary><p className="zz-muted">关联时学习卡版本 v{item.card_version} · 资料分类：{item.entry.data_classification}</p><pre className="zz-prose" aria-label={'关联的正式正文 v' + item.revision}>{item.entry.body}</pre><Link to={entryContextUrl(item.entry_id, item.revision)}>核对此正式版本</Link></details>)}
  </section>;
}
