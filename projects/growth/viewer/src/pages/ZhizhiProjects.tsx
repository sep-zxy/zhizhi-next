import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FolderGit2, ShieldCheck, GitBranch } from 'lucide-react';
import {
  captureGrowthSnapshot,
  getGrowthCommits,
  getGrowthModelSources,
  previewGrowthModelRequest,
  startGrowthModelAnalysis,
  type GrowthLocalState,
  type GrowthCommit,
  type CapturedGrowthSnapshot,
  type GrowthModelSources,
  type GrowthModelPreview,
} from '../api/growth';
import { knowledgeGet, type KnowledgeCard } from '../api/knowledge';
import { getTask } from '../api/tasks';
import { learningUrl, projectCommitLearning } from './zhizhi-model';
export default function ZhizhiProjects({
  state,
  projectId,
  onChanged,
  cards = [],
}: {
  state: GrowthLocalState;
  projectId: string;
  onChanged: () => Promise<void>;
  cards?: KnowledgeCard[];
}) {
  const [params] = useSearchParams();
  const project = state.projects.find((item) => item.project_id === projectId);
  const bindings = state.bindings.filter(
    (item) => item.project_id === projectId,
  );
  const features = state.features.filter(
    (item) => item.project_id === projectId,
  );
  const [bindingId, setBindingId] = useState(bindings[0]?.binding_id ?? '');
  const [featureId, setFeatureId] = useState(features[0]?.feature_id ?? '');
  const [commits, setCommits] = useState<GrowthCommit[]>([]);
  const [selected, setSelected] = useState(params.get('commit') ?? '');
  const [evidence, setEvidence] = useState<{
    title: string;
    patch: string;
  } | null>(null);
  const [snapshot, setSnapshot] = useState<CapturedGrowthSnapshot | null>(null);
  const [sources, setSources] = useState<GrowthModelSources | null>(null);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [provider, setProvider] = useState('');
  const [preview, setPreview] = useState<GrowthModelPreview | null>(null);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const [taskId, setTaskId] = useState('');
  const [taskState, setTaskState] = useState('');
  const live = useRef(true);
  const lock = useRef(false);
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    let current = true;
    setCommits([]);
    setEvidence(null);
    setSnapshot(null);
    setSources(null);
    setPreview(null);
    setTaskId('');
    setTaskState('');
    setError('');
    if (bindingId && featureId) {
      setBusy(true);
      void getGrowthCommits(bindingId, featureId)
        .then((result) => {
          if (current) setCommits(result.commits);
        })
        .catch((reason) => current && setError(String(reason)))
        .finally(() => current && setBusy(false));
    }
    return () => {
      current = false;
    };
  }, [bindingId, featureId]);
  useEffect(() => {
    let current = true;
    setEvidence(null);
    setSnapshot(null);
    setSources(null);
    setPreview(null);
    setApproved(false);
    if (selected && bindingId)
      void knowledgeGet<{ title: string; patch: string }>(
        '/commits/' +
          encodeURIComponent(selected) +
          '?binding_id=' +
          encodeURIComponent(bindingId),
      )
        .then((value) => current && setEvidence(value))
        .catch((reason) => current && setError(String(reason)));
    return () => {
      current = false;
    };
  }, [selected, bindingId]);
  useEffect(() => {
    if (!taskId) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await getTask(taskId);
        if (!current) return;
        setTaskState(value.status);
        if (['completed', 'failed', 'cancelled'].includes(value.status)) {
          await onChanged();
          return;
        }
      } catch (reason) {
        if (current) setError('任务状态暂不可读：' + String(reason));
      }
      if (current) timer = setTimeout(() => void poll(), 3000);
    };
    void poll();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [taskId, onChanged]);
  async function act(fn: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setStatus('');
    try {
      await fn();
    } catch (reason) {
      if (live.current) setError(String(reason));
    } finally {
      lock.current = false;
      if (live.current) setBusy(false);
    }
  }
  if (!project)
    return (
      <section className="zz-panel">
        <h2>
          <FolderGit2 />
          选择已授权的工程项目
        </h2>
        <p>只读取你选定的项目和提交，不上传整仓、不扫描全部历史。</p>
        {state.projects.map((item) => (
          <Link
            className="zz-next"
            key={item.project_id}
            to={learningUrl('projects', item.project_id)}
          >
            {item.name} →
          </Link>
        ))}
        {!state.projects.length && (
          <div className="zz-empty">
            <p>
              尚无已授权项目。当前页面只读取已有项目绑定，限定范围的新项目接入尚未完成，不会自动导入或扫描仓库。
            </p>
            <button className="zz-button secondary" disabled>
              接入工程项目（待接入）
            </button>
          </div>
        )}
      </section>
    );
  const commit = commits.find((item) => item.sha === selected);
  const linkedCards = projectCommitLearning(cards, projectId, selected);
  return (
    <>
      <section className="zz-panel">
        <h2>
          <ShieldCheck />
          授权分析范围
        </h2>
        <div className="zz-toolbar">
          <span className="zz-badge">{project.name}</span>
          <label>
            仓库范围
            <select
              disabled={busy}
              value={bindingId}
              onChange={(event) => {
                setBindingId(event.target.value);
                setSelected('');
              }}
            >
              {bindings.map((item) => (
                <option key={item.binding_id} value={item.binding_id}>
                  {item.canonical_local_path}
                </option>
              ))}
            </select>
          </label>
          <label>
            功能分支范围
            <select
              disabled={busy}
              value={featureId}
              onChange={(event) => {
                setFeatureId(event.target.value);
                setSelected('');
              }}
            >
              {features.map((item) => (
                <option key={item.feature_id} value={item.feature_id}>
                  {item.label} · {item.base_ref}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="zz-muted">
          只在已绑定范围读取最近最多 50
          条已有提交记录。不会自动全历史扫描，不上传整个仓库。提交不代表测试通过。
        </p>
        {(!bindings.length || !features.length) && (
          <div className="zz-notice">
            项目缺少仓库绑定或功能范围，暂不可生成学习证据。
          </div>
        )}
      </section>
      {error && (
        <p role="alert" className="zz-notice error">
          {error}
        </p>
      )}
      {status && (
        <p role="status" className="zz-status">
          {status}
        </p>
      )}
      <div className="zz-grid">
        <section className="zz-panel">
          <h2>
            <GitBranch />
            选择具体提交
          </h2>
          {busy && !commits.length && <p role="status">正在读取授权范围…</p>}
          {commits.map((item) => (
            <button
              className="zz-commit"
              disabled={busy || !!taskId}
              aria-pressed={selected === item.sha}
              key={item.sha}
              onClick={() => {
                setSelected(item.sha);
                setError('');
                setStatus('');
              }}
            >
              <strong>{item.title}</strong>
              <small>
                {item.sha.slice(0, 12)} · {item.paths.length} 个文件 ·{' '}
                {item.authored_at}
              </small>
            </button>
          ))}
          {!busy && !commits.length && (
            <p className="zz-muted">
              没有可读取提交，或项目范围尚未配置。不会自动扩大扫描范围。
            </p>
          )}
          <h3>模块职责与架构取舍</h3>
          <p className="zz-muted">
            依据真实提交与已生成学习候选分析，不展示设计图中的示例架构。
          </p>
          <button className="zz-button secondary" disabled>
            职责演进图（接口待接入）
          </button>
          {selected && (
            <>
              <h3>这次提交关联的学习</h3>
              {linkedCards.map((card) => (
                <Link
                  className="zz-next"
                  key={card.card_id}
                  to={learningUrl('session', projectId, card.card_id)}
                >
                  <span>
                    <strong>{card.title}</strong>
                    <small>
                      精确提交 {selected.slice(0, 12)} · 卡片 v
                      {card.card_version}
                    </small>
                  </span>
                  →
                </Link>
              ))}
              {!linkedCards.length && (
                <p className="zz-muted">
                  未找到与此项目及完整提交 SHA
                  同时匹配的学习卡。不会靠标题或缩写猜测关联。
                </p>
              )}
            </>
          )}
        </section>
        <section className="zz-panel">
          <h2>来源片段与学习候选</h2>
          {!selected ? (
            <p>先选择一条具体提交。</p>
          ) : (
            <>
              <span className="zz-badge">commit {selected.slice(0, 12)}</span>
              <h3>{evidence?.title ?? commit?.title ?? '提交证据'}</h3>
              {evidence ? (
                <pre className="zz-code">
                  {evidence.patch || '该提交没有可显示的文本差异。'}
                </pre>
              ) : (
                <p role="status">
                  {error ? '来源尚未读取成功。' : '正在读取精确提交证据…'}
                </p>
              )}
              {!snapshot && (
                <button
                  className="zz-button"
                  disabled={
                    busy ||
                    !evidence ||
                    !project.local_processing ||
                    !featureId ||
                    !bindingId
                  }
                  onClick={() =>
                    void act(async () => {
                      const value = await captureGrowthSnapshot(
                        featureId,
                        bindingId,
                        [],
                        selected,
                      );
                      if (live.current) {
                        setSnapshot(value);
                        setStatus('所选提交已保存本机证据快照，尚未发送模型。');
                      }
                      const available = await getGrowthModelSources(
                        value.snapshot_id,
                        bindingId,
                      );
                      if (live.current) {
                        setSources(available);
                        setSourceIds([]);
                        setProvider(
                          available.provider_names.length === 1
                            ? available.provider_names[0]
                            : '',
                        );
                      }
                    })
                  }
                >
                  为这条提交准备学习证据
                </button>
              )}
              {!project.local_processing && (
                <p className="zz-muted">此项目尚未允许本地处理，操作已禁用。</p>
              )}
              {snapshot && (
                <>
                  <p className="zz-muted">
                    snapshot {snapshot.snapshot_id} ·{' '}
                    {snapshot.changed_paths.length} 个变化文件
                  </p>
                  {!sources && (
                    <button
                      className="zz-button secondary"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          const value = await getGrowthModelSources(
                            snapshot.snapshot_id,
                            bindingId,
                          );
                          if (live.current) setSources(value);
                        })
                      }
                    >
                      重试读取快照来源
                    </button>
                  )}
                  {sources && (
                    <>
                      <label>
                        已配置的模型
                        <select
                          disabled={busy || !!taskId}
                          value={provider}
                          onChange={(event) => {
                            setProvider(event.target.value);
                            setPreview(null);
                            setApproved(false);
                          }}
                        >
                          <option value="">选择模型</option>
                          {sources.provider_names.map((name) => (
                            <option key={name}>{name}</option>
                          ))}
                        </select>
                      </label>
                      <h3>明确选择必要文件</h3>
                      {sources.sources.map((source) => (
                        <label className="zz-check" key={source.source_ref_id}>
                          <input
                            type="checkbox"
                            disabled={busy || !!taskId}
                            checked={sourceIds.includes(source.source_ref_id)}
                            onChange={(event) => {
                              setSourceIds((current) =>
                                event.target.checked
                                  ? [...current, source.source_ref_id]
                                  : current.filter(
                                      (id) => id !== source.source_ref_id,
                                    ),
                              );
                              setPreview(null);
                              setApproved(false);
                            }}
                          />
                          {source.relative_path}
                        </label>
                      ))}
                      <button
                        className="zz-button secondary"
                        disabled={
                          busy ||
                          !!taskId ||
                          !project.model_allowed ||
                          !provider ||
                          !sourceIds.length
                        }
                        onClick={() =>
                          void act(async () => {
                            const value = await previewGrowthModelRequest(
                              snapshot.snapshot_id,
                              bindingId,
                              provider,
                              sourceIds,
                            );
                            if (live.current) {
                              setPreview(value);
                              setApproved(false);
                              requestId.current = crypto.randomUUID();
                            }
                          })
                        }
                      >
                        预览发送内容
                      </button>
                      {!project.model_allowed && (
                        <p className="zz-muted">
                          本项目未授权模型处理；只保留本机证据。
                        </p>
                      )}
                    </>
                  )}
                  {preview && (
                    <>
                      <p>
                        接收方：{preview.provider_host} · {preview.model_name}
                      </p>
                      <details open>
                        <summary>核对精确发送内容</summary>
                        <pre className="zz-code">{preview.payload_text}</pre>
                      </details>
                      <label className="zz-check">
                        <input
                          type="checkbox"
                          checked={approved}
                          disabled={busy || !!taskId}
                          onChange={(event) =>
                            setApproved(event.target.checked)
                          }
                        />
                        仅允许将以上范围发送到所列模型，生成工程学习候选
                      </label>
                      <button
                        className="zz-button"
                        disabled={busy || !approved || !!taskId}
                        onClick={() =>
                          void act(async () => {
                            const result = await startGrowthModelAnalysis(
                              snapshot.snapshot_id,
                              bindingId,
                              provider,
                              preview,
                              requestId.current,
                            );
                            if (live.current) {
                              setTaskId(result.task_id ?? '');
                              setTaskState(result.status ?? '');
                              setStatus(
                                result.task_id
                                  ? '已提交候选生成任务，等待真实处理结果。'
                                  : '服务返回分析 ' +
                                      result.analysis_id +
                                      '，未收到可跟踪任务；请刷新核对，不能认定完成。',
                              );
                            }
                          })
                        }
                      >
                        确认范围并生成学习候选
                      </button>
                    </>
                  )}
                </>
              )}
              {taskId && (
                <div className="zz-notice">
                  <p>task {taskId}</p>
                  <p>服务状态：{taskState || '等待查询'}</p>
                  {taskState === 'completed' && (
                    <Link to={learningUrl('candidates', projectId)}>
                      查看真实学习候选 →
                    </Link>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
