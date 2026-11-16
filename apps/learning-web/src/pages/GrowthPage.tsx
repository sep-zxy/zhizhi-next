import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  BookOpen,
  FileText,
  GitBranch,
  ArrowRight,
  Lightbulb,
  RefreshCw,
  PlusCircle,
} from 'lucide-react';
import GrowthShell from '../growth/GrowthShell';
import { getGrowthLocalState, type GrowthLocalState } from '../api/growth';
import { getKnowledge, type KnowledgeIndex } from '../api/knowledge';
import {
  learningCards,
  learningUrl,
  readingLabel,
  zhizhiView,
  lastOpenedLearningCard,
  resumeLearningCard,
} from './zhizhi-model';
import ZhizhiSession from './ZhizhiSession';
import ZhizhiProjects from './ZhizhiProjects';
import ZhizhiWriteback from './ZhizhiWriteback';
import ZhizhiTopics from './ZhizhiTopics';
import WikiLearningPage from './WikiLearningPage';
import ZhizhiLibrary from './ZhizhiLibrary';
import EntryContextPage from './EntryContextPage';
import { entryReference } from '../api/entry-context';
import KnowledgeStudy from './KnowledgeStudy';
import KnowledgeWorkspace from './KnowledgeWorkspace';
import RadarPracticePanel from './RadarPracticePanel';
import ZhizhiRelations from './ZhizhiRelations';
import './KnowledgeWorkspace.css';
import './ZhizhiWorkspace.css';

const TITLES = {
  home: '继续你的工程学习',
  projects: '工程项目与提交证据',
  library: '学习库',
  session: '学习会话',
  understanding: '我的理解',
  writeback: '把理解变成可复用成果',
  radar: '关系与学习雷达',
  candidates: '从工程线索选择学习目标',
  exercise: '可选练习',
  cli: 'CLI 主入口',
  'topic-management': '学习主题管理',
  'entry-context': '核对正式知识引用',
  'wiki-learning': '从正式知识开始自由学习',
};
export default function GrowthPage() {
  const [params, setParams] = useSearchParams();
  const view = zhizhiView(params.get('view'));
  const projectId = params.get('project') ?? '';
  const cardId = params.get('card') ?? '';
  const [state, setState] = useState<GrowthLocalState | null>(null);
  const [index, setIndex] = useState<KnowledgeIndex | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const run = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++run.current;
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      getGrowthLocalState(),
      getKnowledge(),
    ]);
    if (current !== run.current) return;
    const errors: string[] = [];
    if (results[0].status === 'fulfilled') setState(results[0].value);
    else {
      setState(null);
      errors.push('工程状态：' + String(results[0].reason));
    }
    if (results[1].status === 'fulfilled') setIndex(results[1].value);
    else {
      setIndex(null);
      errors.push('学习记录：' + String(results[1].reason));
    }
    setError(errors.join('；'));
    setLoading(false);
  }, []);
  useEffect(() => {
    void refresh();
    return () => {
      run.current++;
    };
  }, [refresh]);
  const cards = learningCards(index, projectId, params.get('q') ?? '');
  const candidates = (index?.candidates ?? []).filter(
    (candidate) => !projectId || candidate.project_ids.includes(projectId),
  );
  const lastCardId = lastOpenedLearningCard();
  const current = resumeLearningCard(cards, lastCardId);
  const binding = state?.bindings.find((item) =>
    projectId
      ? item.project_id === projectId
      : cards
          .find((card) => card.card_id === cardId)
          ?.project_ids?.includes(item.project_id),
  );
  function filter(key: string, value: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      value ? next.set(key, value) : next.delete(key);
      if (key === 'project') next.delete('card');
      return next;
    });
  }
  const cardLinks = (
    <div className="zz-list">
      {cards.map((card) => (
        <article className="zz-list-row" key={card.card_id}>
          <FileText aria-hidden="true" />
          <div>
            <Link to={learningUrl('session', projectId, card.card_id)}>
              {card.title}
            </Link>
            <p>{card.front_question}</p>
            <small>
              {readingLabel(card)} · {card.source_commit_shas?.length ?? 0}{' '}
              条提交证据 · v{card.card_version}
            </small>
          </div>
          <Link
            className="zz-button secondary"
            to={learningUrl(
              view === 'understanding' ? 'understanding' : 'session',
              projectId,
              card.card_id,
            )}
          >
            {view === 'understanding' ? '写下我的理解' : '继续学习'}
          </Link>
        </article>
      ))}
    </div>
  );
  return (
    <GrowthShell
      projects={state?.projects ?? []}
      projectId={projectId}
      onProjectChange={(value) => filter('project', value)}
      connectionState={loading ? 'loading' : error ? 'error' : 'ready'}
    >
      <div className="zz-workspace">
        <header className="zz-heading">
          <div>
            <h1>{TITLES[view]}</h1>
            <p>从真实工程出发，理解机制、边界与技术取舍。</p>
          </div>
          <div className="zz-heading-actions">
            {view === 'library' && !cardId && (
              <Link className="zz-button" to={learningUrl('projects', projectId)}>
                <PlusCircle size={16} aria-hidden="true" />
                新建学习任务
              </Link>
            )}
            <button
              className="zz-button secondary"
              disabled={loading}
              onClick={() => void refresh()}
            >
              <RefreshCw size={16} />
              刷新
            </button>
          </div>
        </header>
        {error && (
          <div className="zz-notice error" role="alert">
            读取未完成：{error}。不会用示例内容替代真实记录。
          </div>
        )}
        {loading && <p role="status">正在读取学习记录…</p>}
        {['home', 'library'].includes(view) && (
          <div className="zz-toolbar">
            <Link to={learningUrl('topic-management', projectId)}>
              学习主题管理
            </Link>
            <span className="zz-muted">
              维护学习原话与主题归属，不进入通用知识审核。
            </span>
          </div>
        )}
        {view === 'topic-management' && <ZhizhiTopics />}
        {view === 'entry-context' && (
          <EntryContextPage
            key={params.get('entry_id') + ':' + params.get('revision')}
            reference={entryReference(params)}
          />
        )}
        {view === 'wiki-learning' && (
          <WikiLearningPage
            key={params.get('context_id') ?? ''}
            contextId={
              params.getAll('context_id').length === 1
                ? (params.get('context_id') ?? '')
                : ''
            }
          />
        )}
        {!loading && index && view === 'library' && !cardId && (
          <ZhizhiLibrary
            cards={cards}
            current={current}
            projectId={projectId}
            projects={state?.projects ?? []}
          />
        )}
        {!loading && index && view === 'home' && (
          <>
            <div className="zz-grid">
              <section className="zz-panel">
                <h2>
                  <BookOpen />
                  {current?.card_id === lastCardId
                    ? '继续上次学习'
                    : '继续工程学习'}
                </h2>
                {current ? (
                  <>
                    <h3>{current.title}</h3>
                    <p>{current.front_question}</p>
                    <p className="zz-muted">
                      {readingLabel(current)} · 回答与 AI 讲解分开保存
                    </p>
                    <Link
                      className="zz-button"
                      to={learningUrl('session', projectId, current.card_id)}
                    >
                      继续学习 <ArrowRight size={16} />
                    </Link>
                  </>
                ) : (
                  <div className="zz-empty">
                    <h3>从自己的项目或笔记开始</h3>
                    <p>这里还没有学习材料，不会自动生成“我的理解”。</p>
                    <Link
                      className="zz-button"
                      to={learningUrl('projects', projectId)}
                    >
                      选择工程证据
                    </Link>
                  </div>
                )}
              </section>
              <section className="zz-panel">
                <h2>
                  <Lightbulb />
                  我的下一步
                </h2>
                <Link
                  className="zz-next"
                  to={learningUrl('library', projectId)}
                >
                  选择一个架构问题 <ArrowRight size={16} />
                </Link>
                <Link
                  className="zz-next"
                  to={learningUrl('understanding', projectId)}
                >
                  用自己的话解释机制与边界 <ArrowRight size={16} />
                </Link>
                <Link
                  className="zz-next"
                  to={learningUrl('writeback', projectId)}
                >
                  核对学习成果与回填状态 <ArrowRight size={16} />
                </Link>
              </section>
            </div>
            <section className="zz-panel">
              <h2>
                <GitBranch />
                工程中的学习线索
              </h2>
              {candidates.slice(0, 3).map((item) => (
                <div className="zz-list-row" key={item.candidate_id}>
                  <FileText />
                  <div>
                    <strong>{item.title}</strong>
                    <p>{item.reason}</p>
                  </div>
                  <Link
                    className="zz-button secondary"
                    to={learningUrl('candidates', projectId)}
                  >
                    核对证据
                  </Link>
                </div>
              ))}
              {!candidates.length && (
                <p className="zz-muted">
                  暂无候选。选择已授权项目中的具体提交后再开始。
                </p>
              )}
            </section>
            <div className="zz-grid">
              <section className="zz-panel">
                <h2>最近学习材料</h2>
                {cards.slice(0, 3).map((card) => (
                  <Link
                    className="zz-next"
                    key={card.card_id}
                    to={learningUrl('session', projectId, card.card_id)}
                  >
                    {card.title}
                    <ArrowRight size={16} />
                  </Link>
                ))}
              </section>
              <section className="zz-panel">
                <h2>成果回填</h2>
                <p>
                  {
                    cards.filter((card) => card.wiki_draft_status === 'draft')
                      .length
                  }{' '}
                  份候选草稿
                </p>
                <p className="zz-muted">
                  提交不等于正式写入，正式写入也不代表已公开。
                </p>
                <Link to={learningUrl('writeback', projectId)}>
                  查看相关成果 →
                </Link>
              </section>
            </div>
          </>
        )}
        {!loading && index && view === 'understanding' && !cardId && (
          <>
            <div className="zz-toolbar">
              <label>
                搜索学习材料
                <input
                  value={params.get('q') ?? ''}
                  onChange={(event) => filter('q', event.target.value)}
                  placeholder="搜索问题、机制与边界"
                />
              </label>
              <Link
                className="zz-button secondary"
                to={learningUrl('projects', projectId)}
              >
                从工程项目开始
              </Link>
            </div>
            <div className="zz-notice">
              阅读、自己的表达与实践证据分别记录，不换算成掌握分数。
            </div>
            <section className="zz-panel">
              {cardLinks}
              {!cards.length && (
                <div className="zz-empty">
                  <BookOpen />
                  <h2>还没有符合条件的材料</h2>
                  <p>调整搜索或项目范围，也可以从真实提交选择学习目标。</p>
                </div>
              )}
            </section>
          </>
        )}
        {!loading && index && view === 'session' && !cardId && (
          <section className="zz-panel">
            <h2>选择要继续的学习会话</h2>
            {cardLinks}
            {!cards.length && (
              <div className="zz-empty">
                暂无学习会话。
                <Link to={learningUrl('library', projectId)}>从学习库开始</Link>
              </div>
            )}
          </section>
        )}
        {index &&
          cardId &&
          ['session', 'understanding', 'library'].includes(view) && (
            <ZhizhiSession
              key={cardId}
              cardId={cardId}
              projectId={projectId}
              providerName={binding?.provider_names[0] ?? ''}
              onChanged={refresh}
            />
          )}
        {view === 'writeback' && index && (
          <ZhizhiWriteback
            key={cardId || 'list'}
            index={index}
            projectId={projectId}
            cardId={cardId}
            onChanged={refresh}
          />
        )}
        {view === 'projects' && state && (
          <ZhizhiProjects
            key={projectId}
            state={state}
            projectId={projectId}
            cards={index?.cards ?? []}
            onChanged={refresh}
          />
        )}
        {view === 'candidates' && state && (
          <>
            <div className="zz-notice">
              只核对工程学习候选。通用知识审核请前往个人 Wiki。
            </div>
            <KnowledgeWorkspace
              view="learning"
              projectId={projectId}
              state={state}
              onChanged={refresh}
            />
          </>
        )}
        {view === 'exercise' && cardId && (
          <KnowledgeStudy
            cardId={cardId}
            bindingId={binding?.binding_id ?? ''}
            providerName={binding?.provider_names[0] ?? ''}
            onChanged={refresh}
          />
        )}
        {view === 'radar' && (
          <div className="zz-grid">
            <ZhizhiRelations
              entryId={params.get('entry_id') ?? ''}
              onEntryChange={(value) => filter('entry_id', value)}
            />
            <RadarPracticePanel
              activeId={params.get('radar')}
              onSelect={(id) => filter('radar', id ?? '')}
            />
          </div>
        )}
        {view === 'cli' && (
          <section className="zz-panel">
            <h2>统一 CLI</h2>
            <p>
              CLI 与 Web 使用同一知识、学习和任务身份。请使用安装包内既有 Pi
              入口；此页面不会自动启动 shell 或安装工具。
            </p>
            <p className="zz-muted">跨应用启动桥尚未接入。</p>
            <button className="zz-button secondary" disabled>
              启动 CLI（待接入）
            </button>
          </section>
        )}
        <footer className="zz-footer">
          知枝 · 工程学习专域。通用收件、全库管理与系统配置请前往个人 Wiki。
        </footer>
      </div>
    </GrowthShell>
  );
}
