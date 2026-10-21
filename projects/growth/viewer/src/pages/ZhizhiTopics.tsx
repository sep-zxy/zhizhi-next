import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import type { GrowthCloudAccess } from '../api/growth';
import { useTranslation } from '../i18n/useTranslation';
import GrowthTopicsPanel from './GrowthTopicsPanel';
import { connectLearningTopics } from './zhizhi-topics';
import { learningUrl } from './zhizhi-model';
import './GrowthPage.css';

// Do not inspect or stringify a remote exception: it may contain credentials or source text.
export function learningTopicsFailureSummary(_cause: unknown): string {
  return '学习服务连接或同步未获确认。请核对服务地址、既有身份与网络后重试；不会据此认定登录或同步成功。';
}

export default function ZhizhiTopics() {
  const { t } = useTranslation();
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [access, setAccess] = useState<GrowthCloudAccess | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  async function connect(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    lock.current = true;
    setBusy(true); setError(''); setAccess(null);
    try {
      const confirmed = await connectLearningTopics(url, token);
      if (live.current) setAccess(confirmed);
    } catch (cause) {
      if (live.current) setError(learningTopicsFailureSummary(cause));
    } finally {
      lock.current = false;
      if (live.current) setBusy(false);
    }
  }

  return <>
    <div className="zz-toolbar"><Link to={learningUrl('library')}>← 返回学习库</Link></div>
    <div className="zz-notice">这里只维护学习主题、学习原话及任务归属。不是全库知识审核，不会修改个人 Wiki 的正式条目或公开状态。</div>
    <section className="zz-panel"><h2>连接已有学习账号</h2>
      <p>使用你已有的学习服务身份，不借用个人 Wiki 的登录。页面将访问令牌保留在当前页面内存中，不写入浏览器存储；这不代表服务端不保留账号或设备身份。</p>
      <p className="zz-muted">点击连接按既有流程登记并保留本地设备标识，首次连接或尚未初始化时读取历史，也可能同步已入队的学习变更。本次不额外批准新项目发布或强制刷新历史。主题合并仍须明确确认，拆分保留原始记录的显式归属。</p>
      <form className="growth-page__form" onSubmit={event => void connect(event)}>
        <label htmlFor="growth-cloud-url">{t('Growth.cloud_url')}</label>
        <input id="growth-cloud-url" type="url" value={url} disabled={busy} required maxLength={2048}
          onChange={event => { setUrl(event.target.value); setAccess(null); setError(''); }} />
        <label htmlFor="growth-cloud-token">{t('Growth.cloud_token')}</label>
        <input id="growth-cloud-token" type="password" value={token} disabled={busy} required autoComplete="off"
          onChange={event => { setToken(event.target.value); setAccess(null); setError(''); }} />
        <button className="zz-button" disabled={busy || !url.trim() || !token.trim()}>
          {busy ? t('Growth.sync_running') : t('Growth.sync_now')}
        </button>
      </form>
      {error && <p role="alert" className="zz-notice error">{error}</p>}
      {access && <p role="status">学习服务已确认账号，主题内容继续按真实读取结果显示。</p>}
    </section>
    {access && <section className="zz-panel growth-page__panel" aria-label="学习主题维护">
      <GrowthTopicsPanel key={access.cloud_url + ':' + access.account_id} access={access} disabled={busy} refreshKey={0} />
    </section>}
  </>;
}
