import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const router = vi.hoisted(() => ({
  params: new URLSearchParams(),
  navigations: [] as URLSearchParams[],
  ref: null as null | { current: unknown },
}));
vi.mock('react', async (original) => ({
  ...(await original<typeof import('react')>()),
  useRef: (value: unknown) => (router.ref ??= { current: value }),
}));
vi.mock('react-router-dom', async (original) => ({
  ...(await original<typeof import('react-router-dom')>()),
  useSearchParams: () => {
    // Deliberately retain this render's snapshot: real router setters do not
    // queue functional updates, even when two input events arrive in order.
    const snapshot = router.params;
    return [snapshot, (next: URLSearchParams | ((value: URLSearchParams) => URLSearchParams)) => {
      router.navigations.push(new URLSearchParams(
        typeof next === 'function' ? next(snapshot) : next,
      ));
    }];
  },
}));
import ZhizhiLibrary from './ZhizhiLibrary';

type Element = ReactElement<Record<string, unknown>>;
function elements(value: unknown): Element[] {
  if (Array.isArray(value)) return value.flatMap(elements);
  if (!value || typeof value !== 'object' || !('props' in value)) return [];
  const element = value as Element;
  return [element, ...elements(element.props.children)];
}
function controls() {
  const nodes = elements(ZhizhiLibrary({ cards: [], projectId: 'p1', projects: [] }));
  return {
    select: (name: string) => {
      const button = nodes.find((node) => node.type === 'button' && node.props.children === name)!;
      (button.props.onClick as () => void)();
    },
    search: (value: string) => {
      const input = nodes.find((node) => node.type === 'input')!;
      (input.props.onChange as (event: { target: { value: string } }) => void)({ target: { value } });
    },
  };
}
function latest() { return router.navigations.at(-1)!; }
beforeEach(() => {
  router.params = new URLSearchParams('view=library&project=p1&library_tab=revisit');
  router.navigations = [];
  router.ref = null;
});
describe('学习库连续 URL 输入事件（无浏览器、无模型）', () => {
  it('同一渲染的切换计划与搜索合并，不能被旧待回看参数覆盖', () => {
    const ui = controls();
    ui.select('我的计划');
    ui.search('没有匹配的关键词');
    expect(latest().get('library_tab')).toBe('plan');
    expect(latest().get('q')).toBe('没有匹配的关键词');
    expect(latest().get('project')).toBe('p1');
    expect(latest().get('view')).toBe('library');
    expect(router.params.get('library_tab')).toBe('revisit');
    expect(router.params.has('q')).toBe(false);
  });
  it('连续输入、切换和清空保留彼此的最新意图', () => {
    const ui = controls();
    ui.search('事务');
    ui.select('我的计划');
    expect(latest().get('q')).toBe('事务');
    ui.search('');
    expect(latest().has('q')).toBe(false);
    expect(latest().get('library_tab')).toBe('plan');
  });
  it('后退或深链的新 router 快照取代待提交参数', () => {
    controls().select('我的计划');
    router.params = new URLSearchParams('view=library&project=p2&library_tab=revisit&q=旧搜索');
    controls().search('新搜索');
    expect(latest().get('project')).toBe('p2');
    expect(latest().get('library_tab')).toBe('revisit');
    expect(latest().get('q')).toBe('新搜索');
  });
});
