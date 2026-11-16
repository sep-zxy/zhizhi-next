import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Network } from 'lucide-react';
import {
  getLearningRelations,
  relationKinds,
  relationLabels,
  type LearningRelations,
  type RelationKind,
} from '../api/learning-relations';
export default function ZhizhiRelations({
  entryId,
  onEntryChange,
}: {
  entryId: string;
  onEntryChange: (value: string) => void;
}) {
  const [input, setInput] = useState(entryId);
  const [depth, setDepth] = useState(1);
  const [kinds, setKinds] = useState<RelationKind[]>([...relationKinds]);
  const [data, setData] = useState<LearningRelations | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => setInput(entryId), [entryId]);
  useEffect(() => {
    setData(null);
    setError(false);
    setSelected('');
    if (!entryId || !kinds.length) {
      setBusy(false);
      return;
    }
    const controller = new AbortController();
    setBusy(true);
    void getLearningRelations(entryId, depth, kinds, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result);
          setSelected(result.root_entry_id);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [entryId, depth, kinds, retry]);
  const selectedNode = data?.nodes.find((node) => node.entry_id === selected);
  const nodes = data?.nodes ?? [];
  const positions = new Map(
    nodes.map((node, index) => [
      node.entry_id,
      node.entry_id === data?.root_entry_id
        ? { x: 300, y: 200 }
        : {
            x:
              300 +
              220 * Math.cos((index * 2 * Math.PI) / Math.max(nodes.length, 1)),
            y:
              200 +
              145 * Math.sin((index * 2 * Math.PI) / Math.max(nodes.length, 1)),
          },
    ]),
  );
  return (
    <section className="zz-panel" aria-label="正式知识关系图">
      <h2>
        <Network />
        知识关系图
      </h2>
      <p className="zz-muted">
        只读取已授权正式条目的关系投影。节点和证据按返回版本展示，不从多跳推导新事实。
      </p>
      <form
        className="zz-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          if (input.trim() === entryId) setRetry((value) => value + 1);
          else onEntryChange(input.trim());
        }}
      >
        <label>
          正式条目 ID
          <input
            aria-label="关系根条目"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="从个人 Wiki 复制正式条目 ID"
          />
        </label>
        <button
          className="zz-button secondary"
          disabled={!input.trim() || busy}
        >
          查看关系
        </button>
        <label>
          展开深度
          <select
            value={depth}
            onChange={(event) => setDepth(Number(event.target.value))}
          >
            {[1, 2, 3].map((value) => (
              <option key={value} value={value}>
                {value} 层
              </option>
            ))}
          </select>
        </label>
      </form>
      <div className="zz-legend" aria-label="关系类型筛选">
        {relationKinds.map((kind) => (
          <label className={'zz-check relation-' + kind} key={kind}>
            <input
              type="checkbox"
              checked={kinds.includes(kind)}
              onChange={(event) =>
                setKinds((current) =>
                  event.target.checked
                    ? [...current, kind]
                    : current.filter((value) => value !== kind),
                )
              }
            />
            {relationLabels[kind]}
          </label>
        ))}
      </div>
      {!entryId && (
        <p className="zz-empty">
          先选择一个正式条目。不用工程卡 ID 冒充知识条目，也不绘制示例关系。
        </p>
      )}
      {!kinds.length && <p className="zz-notice">请选择至少一种关系类型。</p>}
      {busy && <p role="status">正在核对关系与目标版本…</p>}
      {error && (
        <div className="zz-notice error" role="alert">
          关系未能读取或未通过版本核对。可能是服务未接入、权限或版本变化；未显示缓存关系或上游敏感错误。
          <button
            className="zz-button secondary"
            onClick={() => setRetry((value) => value + 1)}
          >
            重试只读查询
          </button>
        </div>
      )}
      {data && (
        <>
          <p className="zz-muted">
            投影 {data.projection_revision} · {nodes.length} 个可见条目 ·{' '}
            {data.edges.length} 条关系{data.truncated ? ' · 范围已截断' : ''}
          </p>
          {nodes.length <= 12 ? (
            <svg
              className="zz-relation-map"
              viewBox="0 0 600 400"
              role="img"
              aria-label="关系概览，详细条目与证据见下方列表"
            >
              {data.edges.map((edge) => {
                const from = positions.get(edge.source_entry_id);
                const to = positions.get(edge.target_entry_id);
                if (!from || !to) return null;
                return (
                  <line
                    key={edge.edge_id}
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    className={'relation-' + edge.kind}
                  >
                    <title>
                      {relationLabels[edge.kind]}：{edge.source_entry_id} →{' '}
                      {edge.target_entry_id}
                    </title>
                  </line>
                );
              })}
              {nodes.map((node) => {
                const position = positions.get(node.entry_id);
                if (!position) return null;
                return (
                  <g
                    key={node.entry_id}
                    transform={`translate(${position.x},${position.y})`}
                  >
                    <rect x="-74" y="-26" width="148" height="52" rx="8" />
                    <text textAnchor="middle" y="-2">
                      {node.title.length > 12
                        ? node.title.slice(0, 12) + '…'
                        : node.title}
                    </text>
                    <text textAnchor="middle" y="17" className="revision">
                      v{node.revision}
                    </text>
                  </g>
                );
              })}
            </svg>
          ) : (
            <p className="zz-notice">
              当前超过 12
              个节点，改用完整列表避免图形重叠；未隐藏服务返回的证据。
            </p>
          )}
          <div className="zz-relation-nodes" aria-label="可见关系条目">
            {nodes.map((node) => (
              <button
                className={
                  'zz-button' + (selected === node.entry_id ? '' : ' secondary')
                }
                key={node.entry_id}
                aria-pressed={selected === node.entry_id}
                onClick={() => setSelected(node.entry_id)}
              >
                {node.title} · v{node.revision}
              </button>
            ))}
          </div>
          {selectedNode && (
            <>
              <h3>{selectedNode.title}</h3>
              <Link
                to={
                  '/growth?view=entry-context&entry_id=' +
                  encodeURIComponent(selectedNode.entry_id) +
                  '&revision=' +
                  selectedNode.revision
                }
              >
                核对精确正式版本 →
              </Link>
              {data.edges
                .filter(
                  (edge) =>
                    edge.source_entry_id === selected ||
                    edge.target_entry_id === selected,
                )
                .map((edge) => (
                  <article className="zz-evidence" key={edge.edge_id}>
                    <span
                      className={
                        'zz-badge ' +
                        (edge.kind === 'candidate' ? 'warning' : '')
                      }
                    >
                      {relationLabels[edge.kind]}
                    </span>
                    <p>
                      {edge.source_entry_id} v{edge.source_revision} →{' '}
                      {edge.target_entry_id} v{edge.target_revision}
                    </p>
                    <p>{edge.statement || '正文导航链接，不宣称事实关系。'}</p>
                    {edge.kind === 'candidate' && (
                      <p className="zz-muted">
                        待验证候选；可能缺少证据、复核或精确目标版本。
                      </p>
                    )}
                    {edge.evidence_refs?.length ? (
                      <details>
                        <summary>查看来源定位与复核依据</summary>
                        <pre className="zz-code">
                          {JSON.stringify(
                            {
                              evidence_refs: edge.evidence_refs,
                              verification: edge.verification,
                              review_provenance: edge.review_provenance,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    ) : (
                      <p className="zz-muted">无独立事实证据。</p>
                    )}
                  </article>
                ))}
            </>
          )}
          {!data.edges.length && (
            <p className="zz-empty">当前范围暂无关系。这不代表你的知识薄弱。</p>
          )}
        </>
      )}
      <p className="zz-muted">
        关系不等于掌握程度。导航、事实证据与 AI 候选始终分开。
      </p>
    </section>
  );
}
