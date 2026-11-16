import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  attachEntryContext, entryContextFailure, previewEntryContext, readEntryContexts,
  type AttachEntryContext, type EntryContextLink, type EntryContextPreview, type EntryReference,
} from '../api/entry-context';
import { createWikiLearning, listWikiLearning, wikiLearningUrl, type CreateWikiLearning, type WikiLearningSummary } from '../api/wiki-learning';
import { learningUrl } from './zhizhi-model';

export default function EntryContextPage({ reference }: { reference: EntryReference | null }) {
  const navigate = useNavigate();
  const [freeLearning, setFreeLearning] = useState<WikiLearningSummary | null>(null);
  const createRequest = useRef<CreateWikiLearning | null>(null);
  const [preview, setPreview] = useState<EntryContextPreview | null>(null);
  const [cardId, setCardId] = useState('');
  const [approved, setApproved] = useState(false);
  const [linked, setLinked] = useState<EntryContextLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const epoch = useRef(0);
  const locked = useRef(false);
  const request = useRef<AttachEntryContext | null>(null);
  const entryId = reference?.entryId; const revision = reference?.revision;
  const selected = preview?.cards.find(card => card.card_id === cardId);

  useEffect(() => {
    const run = ++epoch.current;
    setFreeLearning(null); createRequest.current = null; setPreview(null); setLinked(null); setCardId(''); setApproved(false); setError(''); request.current = null; locked.current = false;
    if (!entryId || !revision) return;
    setBusy(true);
    void previewEntryContext({ entryId, revision }).then(value => { if (run === epoch.current) setPreview(value); })
      .catch(() => { if (run === epoch.current) setError(entryContextFailure); })
      .finally(() => { if (run === epoch.current) setBusy(false); });
    return () => { epoch.current++; };
  }, [entryId, revision]);

  async function act(action: (current: () => boolean) => Promise<void>) {
    if (locked.current) return;
    const run = epoch.current; const current = () => run === epoch.current;
    locked.current = true; setBusy(true); setError('');
    try { await action(current); }
    catch { if (current()) { setPreview(null); setLinked(null); setApproved(false); setError(entryContextFailure); } }
    finally { if (current()) { locked.current = false; setBusy(false); } }
  }
  if (!reference) return <section className="zz-panel"><p role="alert">条目链接缺少唯一有效的 entry_id 与明确版本。不会猜测条目或读取最新版。</p><Link to={learningUrl('library')}>返回学习库</Link></section>;
  return <section className="zz-panel" aria-label="正式条目版本学习引用">
    <div className="zz-toolbar"><Link to={learningUrl('library')}>← 返回学习库</Link><span className="zz-badge">正式版本 v{reference.revision}</span></div>
    <p className="zz-notice">先核对你指定的正式版本，再明确开始独立自由学习，或关联一张已有工程学习卡。不会自动建卡、发送模型、替代本人原话或标记掌握。</p>
    <p className="zz-muted">entry_id: {reference.entryId} · revision: {reference.revision}</p>
    {busy && <p role="status">正在核对精确版本与关联状态…</p>}
    {error && <p role="alert" className="zz-notice error">{error}</p>}
    <button className="zz-button secondary" disabled={busy} onClick={() => void act(async current => {
      setPreview(null); setLinked(null); setApproved(false);
      const fresh = await previewEntryContext(reference);
      let recovered: WikiLearningSummary | null = null;
      if (createRequest.current) {
        let offset = 0;
        for (;;) {
          if (!current()) return;
          const page = await listWikiLearning(offset);
          recovered = page.items.find(item => item.entry_id === fresh.entry.entry_id && item.revision === fresh.entry.revision && item.content_hash === fresh.entry.content_hash) ?? null;
          if (recovered || !page.has_more) break;
          if (!page.items.length) throw new Error('分页未推进');
          offset += page.items.length;
        }
      }
      if (!current()) return;
      const target = fresh.cards.find(card => card.card_id === cardId);
      const existing = target ? (await readEntryContexts(target.card_id)).items.find(item => item.entry_id === reference.entryId && item.revision === reference.revision && item.content_hash === fresh.entry.content_hash && item.card_version === target.card_version) : undefined;
      if (current()) { setFreeLearning(recovered); setPreview(fresh); setLinked(existing ?? null); if (!target) setCardId(''); }
    })}>重新读取精确版本与关联</button>
    {preview && <>
      <h2>{preview.entry.title}</h2>
      <p className="zz-muted">资料分类：{preview.entry.data_classification} · {preview.entry.classification_labels.join('、') || '无附加标签'}</p>
      <pre className="zz-prose" aria-label="正式版本只读正文">{preview.entry.body}</pre>
      <details><summary>授权引用标识</summary>{preview.entry.evidence_refs.map((ref, index) => <p key={index}>{Object.entries(ref).map(([key, value]) => `${key}: ${value}`).join(' · ')}</p>)}{!preview.entry.evidence_refs.length && <p>该版本未返回可展示的引用标识。</p>}</details>
      <h3>不依赖工程卡的自由学习</h3>
      <p>为这个精确来源保存独立本人解释。开始阅读不会生成候选；提交理解是之后的明确动作，不修改来源条目。</p>
      <button className="zz-button" disabled={busy || !!freeLearning} onClick={() => void act(async current => {
        const facts = { entry_id: preview.entry.entry_id, revision: preview.entry.revision, expected_content_hash: preview.entry.content_hash };
        const previous = createRequest.current;
        const same = previous && Object.entries(facts).every(([key, value]) => previous[key as keyof CreateWikiLearning] === value);
        const body = { ...facts, request_id: same ? previous.request_id : crypto.randomUUID() };
        createRequest.current = body;
        const result = await createWikiLearning(body);
        if (current()) navigate(wikiLearningUrl(result.context_id));
      })}>从当前正式版本开始自由学习</button>
      {freeLearning && <p role="status">已通过只读恢复找到这份自由学习记录。<Link to={wikiLearningUrl(freeLearning.context_id)}>继续自由学习</Link></p>}
      <h3>或关联已有工程学习卡</h3>
      {!preview.cards.length ? <p className="zz-notice">暂无可关联的既有学习卡。仍可明确开始上方的独立自由学习，不会伪造或自动创建工程卡。</p> : <>
        <label>关联到已有学习卡<select aria-label="关联到已有学习卡" value={cardId} disabled={busy} onChange={event => { setCardId(event.target.value); setApproved(false); setLinked(null); request.current = null; }}><option value="">请选择，不会替你决定</option>{preview.cards.map(card => <option value={card.card_id} key={card.card_id}>{card.title} · v{card.card_version}</option>)}</select></label>
        <label className="zz-notice"><input type="checkbox" checked={approved} disabled={busy || !selected || !!linked} onChange={event => setApproved(event.target.checked)}/>我已核对这个正式版本与目标学习卡，确认只添加引用</label>
        <button className="zz-button" disabled={busy || !selected || !approved || !!linked} onClick={() => void act(async current => {
          if (!selected) return;
          const facts = { entry_id: preview.entry.entry_id, revision: preview.entry.revision, expected_content_hash: preview.entry.content_hash, target_card_id: selected.card_id, expected_card_version: selected.card_version };
          const previous = request.current;
          const unchanged = previous && Object.entries(facts).every(([key, value]) => previous[key as keyof AttachEntryContext] === value);
          const body = { ...facts, request_id: unchanged ? previous.request_id : crypto.randomUUID() };
          request.current = body;
          const result = await attachEntryContext(body);
          if (current()) { setLinked(result); setApproved(false); }
        })}>确认关联此正式版本</button>
      </>}
    </>}
    {linked && <div role="status" className="zz-notice"><p>指定正式版本已关联。未创建新卡、未提交模型、未替换你的理解。</p><Link className="zz-button secondary" to={learningUrl('session', '', linked.card_id)}>回到这张学习卡</Link></div>}
  </section>;
}
