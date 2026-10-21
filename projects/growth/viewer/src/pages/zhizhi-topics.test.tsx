import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('../api/client', () => ({ apiFetch, apiFetchBlob: vi.fn() }));
import { connectLearningTopics } from './zhizhi-topics';
import { zhizhiView } from './zhizhi-model';
import ZhizhiTopics, { learningTopicsFailureSummary } from './ZhizhiTopics';

describe('学习主题维护兼容入口', () => {
  beforeEach(() => apiFetch.mockReset());

  it('旧设置和主题深链接进入独立学习维护，不退回首页或通用审核', () => {
    expect(zhizhiView('settings')).toBe('topic-management');
    expect(zhizhiView('topics')).toBe('topic-management');
    expect(zhizhiView('topic-management')).toBe('topic-management');
  });

  it('使用现有学习同步接口，不额外批准新项目发布或强制刷新历史', async () => {
    apiFetch.mockResolvedValueOnce({ account_id: 'fixture-learning-account' });
    await expect(connectLearningTopics(' https://learning.example ', ' fixture-token '))
      .resolves.toEqual({ cloud_url: 'https://learning.example', access_token: 'fixture-token', account_id: 'fixture-learning-account' });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/growth/local/sync');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ cloud_url: 'https://learning.example', access_token: 'fixture-token', refresh_history: false });
  });

  it('没有服务确认的账号时不伪造访问身份', async () => {
    apiFetch.mockResolvedValueOnce({ queued: 0 });
    await expect(connectLearningTopics('https://learning.example', 'fixture-token')).rejects.toThrow('未确认学习账号');
  });

  it('缺少地址或令牌不发送请求', async () => {
    await expect(connectLearningTopics(' ', 'fixture-token')).rejects.toThrow('请填写');
    await expect(connectLearningTopics('https://learning.example', ' ')).rejects.toThrow('请填写');
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('同步失败不产生成功身份或自动重试', async () => {
    apiFetch.mockRejectedValueOnce(new Error('waiting_auth'));
    await expect(connectLearningTopics('https://learning.example', 'fixture-token')).rejects.toThrow('waiting_auth');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('异常中的惰性秘密不会被读取、转换或渲染', async () => {
    const secret = 'sentinel-private-source-and-credential';
    const inspect = vi.fn(() => secret);
    const cause = Object.defineProperties({}, {
      message: { get: inspect },
      stack: { get: inspect },
      toString: { get: inspect },
      [Symbol.toPrimitive]: { get: inspect },
    });
    apiFetch.mockRejectedValueOnce(cause);
    let summary = '';
    try { await connectLearningTopics('https://learning.example', 'fixture-token'); }
    catch (failure) { summary = learningTopicsFailureSummary(failure); }
    const html = renderToStaticMarkup(createElement('p', { role: 'alert' }, summary));
    expect(html).toContain('未获确认');
    expect(html).not.toContain(secret);
    expect(html).not.toContain('fixture-token');
    expect(inspect).not.toHaveBeenCalled();
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('未连接页面没有伪造主题，也没有全库审核、导出或上传控件', () => {
    const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(ZhizhiTopics)));
    expect(html).toContain('growth-cloud-url');
    expect(html).toContain('type="password"');
    expect(html).toContain('当前页面内存');
    expect(html).toContain('不写入浏览器存储');
    expect(html).toContain('不代表服务端不保留账号或设备身份');
    expect(html).toContain('登记并保留本地设备标识');
    expect(html).toContain('首次连接或尚未初始化时读取历史');
    expect(html).toContain('可能同步已入队的学习变更');
    expect(html).not.toContain('不重新扫描全部历史');
    expect(html).not.toContain('不生成或保存新凭证');
    expect(html).not.toContain('growth-topic-select');
    expect(html).not.toContain('growth-review');
    expect(html).not.toContain('growth-cloud-export');
    expect(html).not.toContain('type="file"');
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
