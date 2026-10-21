import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { BookOpen, FolderGit2, Menu, X, House, GitBranch, FilePenLine, Lightbulb, Radar, ExternalLink, Terminal } from 'lucide-react';

import { useTranslation } from '../i18n/useTranslation';
import './GrowthShell.css';
import { zhizhiView } from '../pages/zhizhi-model';

const NAV = [
  { view: 'home', label: '首页', Icon: House },
  { view: 'projects', label: '工程项目', Icon: FolderGit2 },
  { view: 'library', label: '学习库', Icon: BookOpen },
  { view: 'understanding', label: '我的理解', Icon: Lightbulb },
  { view: 'writeback', label: '成果回填', Icon: FilePenLine },
  { view: 'radar', label: '学习雷达', Icon: Radar },
] as const;

export default function GrowthShell({ children, projects, projectId, onProjectChange, latestScanAt, connectionState = 'loading' }: {
  children: ReactNode; projects?: Array<{ project_id: string; name: string }>;
  connectionState?: 'loading' | 'ready' | 'error'; latestScanAt?: string; pendingCount?: number; projectId?: string; onProjectChange?: (projectId: string) => void;
}) {
  const { t } = useTranslation();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobile, setMobile] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia('(max-width: 768px)').matches);
  const menuButton = useRef<HTMLButtonElement>(null);
  const requested = new URLSearchParams(location.search).get('view') ?? 'overview';
  const canonicalView = zhizhiView(requested);
  const activeView = ['session','exercise','candidates','topic-management'].includes(canonicalView) ? 'library' : canonicalView;
  const currentLabel = location.pathname === '/settings'
    ? '设置' : NAV.find((item) => item.view === activeView)?.label ?? '知识卡';
  const projectQuery = projectId ? '&project=' + encodeURIComponent(projectId) : '';

  useEffect(() => { setMenuOpen(false); }, [location.pathname, location.search]);
  useEffect(() => {
    const query = window.matchMedia('(max-width: 768px)');
    const update = () => setMobile(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
        menuButton.current?.focus();
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [menuOpen]);

  return <div className={`growth-shell${menuOpen ? ' growth-shell--open' : ''}`}>
    <a className="growth-shell__skip" href="#main-content" onClick={(event) => {
      event.preventDefault();
      document.getElementById('main-content')?.focus();
    }}>{t('A11y.skip_to_content')}</a>
    <aside className="growth-shell__sidebar" aria-label={t('Growth.nav_section')}
      aria-hidden={mobile && !menuOpen ? true : undefined}
      inert={mobile && !menuOpen ? true : undefined}>
      <div className="growth-shell__brand">
        <span className="growth-shell__mark" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 128 128" fill="none" stroke="currentColor" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round"><path d="M46 88V47M46 74H66C82 74 88 62 88 47"/><circle cx="46" cy="96" r="9"/><circle cx="46" cy="36" r="9"/><path d="M84 47C72 47 65 40 65 28C77 28 84 35 84 47ZM91 39C91 27 98 20 110 20C110 32 103 39 91 39Z" fill="currentColor" stroke="none"/></svg></span>
        <div><strong>知枝</strong></div>
      </div>
      <nav id="growth-navigation" aria-label={t('Growth.nav_section')}>
        {NAV.map(({ view, label, Icon }) => <Link key={view}
          to={`/growth?view=${view}${projectQuery}`}
          aria-current={location.pathname === '/growth' && activeView === view ? 'page' : undefined}
          className="growth-shell__nav-item">
          <Icon size={19} aria-hidden="true" /><span>{label}</span>
        </Link>)}
      </nav>
      <div className="growth-shell__footer">
        <a href="/wiki/" className="growth-shell__nav-item"><GitBranch size={19}/><span>个人 Wiki</span><ExternalLink size={14}/></a>
        <Link to="/growth?view=cli" className="growth-shell__nav-item"><Terminal size={19}/><span>CLI 主入口</span></Link>
      </div>
    </aside>
    <button type="button" className="growth-shell__backdrop"
      hidden={!mobile || !menuOpen} aria-label={t('A11y.close_menu')}
      onClick={() => { setMenuOpen(false); menuButton.current?.focus(); }} />
    <div className="growth-shell__main">
      <header className="growth-shell__topbar">
        <button type="button" className="growth-shell__menu" ref={menuButton}
          aria-label={menuOpen ? t('A11y.close_menu') : t('A11y.open_menu')}
          aria-controls="growth-navigation" aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}>
          {menuOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
        <span className="growth-shell__breadcrumb">知枝 / {currentLabel}</span>
        {projects && onProjectChange && <label className="growth-shell__project-switcher">
          <FolderGit2 size={16} aria-hidden="true" />
          <span className="sr-only">工作区项目</span>
          <select aria-label="工作区项目" value={projectId ?? ''}
            onChange={(event) => onProjectChange(event.target.value)}>
            <option value="">全部项目</option>
            {projects.map((project) => <option key={project.project_id} value={project.project_id}>
              {project.name}</option>)}
          </select>
        </label>}
        <div className="growth-shell__top-actions">{latestScanAt&&<span className="growth-shell__latest-snapshot" title={new Date(latestScanAt).toLocaleString()}>最后扫描 {new Date(latestScanAt).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"})}</span>}
          <span role="status" className={'growth-shell__connection is-'+connectionState}>{connectionState==='ready'?'本地服务已响应':connectionState==='error'?'连接异常':'正在连接'}</span><span className="growth-shell__avatar" aria-label="本机工作区">知</span>
        </div>
      </header>
      <main id="main-content" className="growth-shell__content" tabIndex={-1}
        inert={mobile && menuOpen ? true : undefined}>{children}</main>
    </div>
  </div>;
}
