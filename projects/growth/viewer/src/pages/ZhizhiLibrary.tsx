import { Link, useSearchParams } from 'react-router-dom';
import { BookOpen, FileText, Lightbulb, FlaskConical, CircleHelp, FolderGit2 } from 'lucide-react';
import type { KnowledgeCard } from '../api/knowledge';
import { learningUrl, readingLabel } from './zhizhi-model';
import WikiLearningLibrary from './WikiLearningLibrary';

/** View projections only. A revisit flag is not an AI assessment of proficiency. */
export default function ZhizhiLibrary({cards, current, projectId, projects}: {
  cards: KnowledgeCard[]; current?: KnowledgeCard; projectId: string;
  projects: Array<{project_id:string;name:string}>;
}) {
  const [params,setParams]=useSearchParams();
  const tab=params.get('library_tab')==='topics'?'topics':params.get('library_tab')==='revisit'?'revisit':'plan';
  const revisit=cards.filter(card=>['confused','revisit'].includes(card.followup_mark));
  function select(value:string){setParams(previous=>{const next=new URLSearchParams(previous);next.set('library_tab',value);return next;});}
  function search(value:string){setParams(previous=>{const next=new URLSearchParams(previous);value?next.set('q',value):next.delete('q');return next;});}
  const rows=tab==='revisit'?revisit:cards;
  return <>
    <div className="zz-tabs" role="group" aria-label="学习库视图">
      {(['plan','topics','revisit'] as const).map(value=><button type="button" key={value} aria-pressed={tab===value} onClick={()=>select(value)}>{value==='plan'?'我的计划':value==='topics'?'主题与项目':'待回看'}</button>)}
    </div>
    <div className="zz-notice">学习状态只记录证据，不给“掌握程度”打分。自己的解释与 AI 讲解分开保存。</div>
    <div className="zz-library-grid"><div>
      {tab==='plan'&&current&&<section className="zz-panel"><h2><BookOpen/>继续学习</h2><article className="zz-resume"><FileText size={42}/><div><h3>{current.title}</h3><p>{current.front_question}</p><span className="zz-badge">{readingLabel(current)}</span><span className="zz-badge warning">本人理解以保存记录为准</span><div className="zz-actions"><Link className="zz-button" to={learningUrl('session',projectId,current.card_id)}>继续学习</Link><Link className="zz-button secondary" to={learningUrl('projects',projectId)}>查看工程来源</Link></div></div></article></section>}
      {tab==='topics'?<section className="zz-panel"><h2><FolderGit2/>主题与项目</h2><p className="zz-muted">按已授权项目组织材料，主题归属由你维护，不根据节点数量推断能力。</p><Link className="zz-button secondary" to={learningUrl('topic-management',projectId)}>管理学习主题</Link><div className="zz-project-tiles">{projects.map(project=><Link key={project.project_id} to={learningUrl('library',project.project_id)}><FolderGit2/><strong>{project.name}</strong><span>{cards.filter(card=>card.project_ids?.includes(project.project_id)).length} 份当前范围材料</span></Link>)}</div>{!projects.length&&<p className="zz-empty">暂无已授权项目。仍可从个人 Wiki 的正式版本开始学习。</p>}</section>:<section className="zz-panel"><h2>{tab==='revisit'?'我标记的待回看':'我的学习材料'}</h2><label className="zz-search-label">搜索学习材料<input value={params.get('q')??''} onChange={event=>search(event.target.value)} placeholder="搜索问题、机制与边界"/></label>{rows.map(card=><article className="zz-list-row" key={card.card_id}><FileText/><div><Link to={learningUrl('session',projectId,card.card_id)}>{card.title}</Link><p>{card.front_question}</p><small>{readingLabel(card)} · {card.source_commit_shas?.length??0} 条提交证据 · v{card.card_version}</small></div><Link className="zz-button secondary" to={learningUrl('session',projectId,card.card_id)}>继续学习</Link></article>)}{!rows.length&&<div className="zz-empty"><BookOpen/><h3>{tab==='revisit'?'暂无你标记的待回看问题':'还没有符合条件的材料'}</h3><p>调整搜索或项目范围，也可以从真实提交选择学习目标。</p><Link to={learningUrl('projects',projectId)}>从工程项目开始 →</Link></div>}</section>}
      <WikiLearningLibrary/>
    </div><aside aria-label="学习库辅助信息">
      <section className="zz-panel"><h2><CircleHelp/>待回看</h2>{revisit.slice(0,3).map(card=><Link className="zz-next" key={card.card_id} to={learningUrl('session',projectId,card.card_id)}><span><span className="zz-badge warning">我标记的问题</span><strong>{card.front_question||card.title}</strong></span><span aria-hidden="true">→</span></Link>)}{!revisit.length&&<p className="zz-muted">暂无待回看标记。会话中的“我仍不理解”和“标记待回看”由你主动记录。</p>}</section>
      <section className="zz-panel"><h2>学习记录的三种证据</h2>{[[BookOpen,'阅读 · 看过材料','保留阅读记录与精确来源，不等于理解。'],[Lightbulb,'表达理解 · 留下自己的解释','本人原话单独保存，AI 不能代写或覆盖。'],[FlaskConical,'实践证据 · 附上验证记录','只有可核对的实验与工程记录才算证据。']] .map(([Icon,title,body])=>{const Symbol=Icon as typeof BookOpen;return <div className="zz-evidence-kind" key={String(title)}><Symbol/><div><strong>{String(title)}</strong><p>{String(body)}</p></div></div>;})}<p className="zz-notice">三者不是分数，也不能互相替代。</p></section>
    </aside></div>
  </>;
}
