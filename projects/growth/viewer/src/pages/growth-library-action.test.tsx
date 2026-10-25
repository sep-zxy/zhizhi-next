import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
vi.mock('../growth/GrowthShell', () => ({ default: ({ children }: { children: unknown }) => children }));
import GrowthPage from './GrowthPage';
function render(query: string) {
  return renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: ['/growth?' + query] }, createElement(GrowthPage)));
}
describe('P06 新建学习任务入口（SSR，不创建虚假任务）', () => {
  it('主操作进入真实来源选择，保留项目范围和次要刷新', () => {
    const html = render('view=library&project=p%2F1&q=旧搜索&library_tab=revisit');
    expect(html).toMatch(/class="zz-button"[^>]*href="\/growth\?view=projects&amp;project=p%2F1"/);
    expect(html).toContain('新建学习任务');
    expect(html).toContain('刷新');
    expect(html).not.toContain('view=projects&amp;project=p%2F1&amp;q=');
  });
  it('无项目时允许选择已授权项目，不伪造默认来源', () => {
    expect(render('view=library')).toContain('href="/growth?view=projects"');
  });
  it('其他页面及学习详情不混入创建入口', () => {
    expect(render('view=home')).not.toContain('新建学习任务');
    expect(render('view=library&card=c1')).not.toContain('新建学习任务');
  });
});
