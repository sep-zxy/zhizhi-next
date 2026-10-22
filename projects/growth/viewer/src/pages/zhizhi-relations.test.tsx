import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import ZhizhiRelations from './ZhizhiRelations';
describe('关系图入口（SSR）', () => {
  it('没有正式条目时不渲染虚构图和假确认', () => {
    const html = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(ZhizhiRelations, {
          entryId: '',
          onEntryChange: () => {},
        }),
      ),
    );
    expect(html).toContain('先选择一个正式条目');
    expect(html).toContain('导航关联');
    expect(html).toContain('证据确认');
    expect(html).toContain('AI 候选');
    expect(html).not.toContain('zz-relation-map');
    expect(html).toContain('关系不等于掌握程度');
    expect(html).not.toContain('掌握率');
  });
});
