import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { KnowledgeCard } from '../api/knowledge';
vi.mock('./WikiLearningLibrary', () => ({
  default: () => createElement('section', null, '独立自由学习记录'),
}));
import ZhizhiLibrary from './ZhizhiLibrary';
const cards: KnowledgeCard[] = [
  {
    card_id: 'card-a',
    concept_id: 'concept-a',
    title: '幂等边界',
    front_question: '失败后如何重试？',
    back_answer: 'AI解释不能成为原话',
    back_explanation: '',
    card_version: 7,
    followup_mark: 'revisit',
    learning_stage: 'back',
    project_ids: ['project-a'],
    source_commit_shas: ['commit-a'],
  },
  {
    card_id: 'card-b',
    concept_id: 'concept-b',
    title: '版本比较',
    front_question: '为何校验旧版本？',
    back_answer: '',
    back_explanation: '',
    card_version: 2,
    followup_mark: 'none',
    project_ids: ['project-a'],
  },
];
function render(query = '', items = cards) {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      { initialEntries: ['/growth?view=library' + query] },
      createElement(ZhizhiLibrary, {
        cards: items,
        current: items[0],
        projectId: 'project-a',
        projects: [{ project_id: 'project-a', name: '授权项目' }],
      }),
    ),
  );
}
describe('P06 学习库设计契约（SSR，不是浏览器或模型端到端）', () => {
  it('呈现三视图、独立来源和三种证据，不将 AI 解释当本人原话', () => {
    const html = render();
    expect(html).toContain('我的计划');
    expect(html).toContain('主题与项目');
    expect(html).toContain('待回看');
    expect(html).toContain('三者不是分数');
    expect(html).toContain('独立自由学习记录');
    expect(html).not.toContain('AI解释不能成为原话');
    expect(html).toContain('v7');
    expect(html).toContain(
      'view=session&amp;project=project-a&amp;card=card-a',
    );
  });
  it('待回看只投影本人标记，不从阅读阶段推断薄弱项', () => {
    const html = render('&library_tab=revisit');
    expect(html).toContain('幂等边界');
    expect(html).not.toContain('版本比较');
    expect(html).toContain('我标记的问题');
  });
  it('项目视图只按真实项目组织，不编造主题或评分', () => {
    const html = render('&library_tab=topics');
    expect(html).toContain('授权项目');
    expect(html).toContain('2 份当前范围材料');
    expect(html).toContain('管理学习主题');
    expect(html).not.toContain('掌握率');
  });
  it('空列表保留真实空态与起步入口，不造学习任务', () => {
    const html = render('', []);
    expect(html).toContain('还没有符合条件的材料');
    expect(html).toContain('暂无待回看标记');
    expect(html).not.toContain('幂等边界');
  });
  it('未知视图回退我的计划，不启用未定义能力', () => {
    const html = render('&library_tab=unknown');
    expect(html).toContain('我的学习材料');
    expect(html).toContain('继续学习');
  });
});
