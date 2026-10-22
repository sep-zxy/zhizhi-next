import { beforeEach, describe, expect, it, vi } from 'vitest';
const fetch = vi.hoisted(() => vi.fn());
vi.mock('./client', () => ({ apiFetch: fetch }));
import {
  getLearningRelations,
  parseLearningRelations,
} from './learning-relations';
const nodes = [
  { entry_id: 'root', revision: 2, title: '根条目' },
  { entry_id: 'target', revision: 3, title: '目标条目' },
];
const base = {
  nodes,
  edges: [
    {
      edge_id: 'edge',
      source_entry_id: 'root',
      target_entry_id: 'target',
      source_revision: 2,
      target_revision: 3,
      kind: 'navigation',
      is_fact: false,
    },
  ],
  root_entry_id: 'root',
  projection_revision: 'git-sha',
  visibility: 'all',
  inference: 'none',
  truncated: false,
};
const confirmed = {
  ...base.edges[0],
  kind: 'confirmed',
  is_fact: true,
  verification: 'reviewed_evidence',
  evidence_refs: [{ source_id: 'source', content_hash: 'evidence-hash' }],
  review_provenance: { review_id: 'review' },
  declared_target_revision: 3,
};
describe('学习关系只读契约', () => {
  beforeEach(() => fetch.mockReset());
  it('只发送有限GET，不触发刷新、生成或写入', async () => {
    fetch.mockResolvedValue({ data: base });
    await getLearningRelations('root', 2, ['navigation', 'candidate']);
    const [path, options] = fetch.mock.calls[0];
    expect(path).toContain('/relations/root/traverse?');
    const query = new URL('http://fixture' + path).searchParams;
    expect(query.get('max_depth')).toBe('2');
    expect(query.get('max_nodes')).toBe('40');
    expect(query.getAll('kind')).toEqual(['navigation', 'candidate']);
    expect(options.method).toBeUndefined();
  });
  it('缺少根、深度越界、无关系类型均不发请求', async () => {
    await expect(getLearningRelations('', 1, ['confirmed'])).rejects.toThrow();
    await expect(
      getLearningRelations('root', 4, ['confirmed']),
    ).rejects.toThrow();
    await expect(getLearningRelations('root', 1, [])).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('保留导航非事实及服务返回的截断状态', () => {
    const result = parseLearningRelations({
      data: { ...base, truncated: true },
    });
    expect(result.edges[0].is_fact).toBe(false);
    expect(result.truncated).toBe(true);
  });
  it('只有精确版本与完整证据确认的边才允许confirmed', () => {
    expect(
      parseLearningRelations({ data: { ...base, edges: [confirmed] } }).edges[0]
        .kind,
    ).toBe('confirmed');
    for (const patch of [
      { evidence_refs: [] },
      { evidence_refs: [{}] },
      { evidence_refs: [{ source_id: 'source' }] },
      { evidence_refs: [{ source_id: ' ', content_hash: 'hash' }] },
      {
        evidence_refs: [
          {
            source_id: 'source',
            learning_outcome_id: 'outcome',
            content_hash: 'hash',
          },
        ],
      },
      { review_provenance: {} },
      { review_provenance: { review_id: ' ' } },
      { review_provenance: null },
      { is_fact: false },
      { declared_target_revision: 2 },
      { verification: 'pending_verification' },
    ])
      expect(() =>
        parseLearningRelations({
          data: { ...base, edges: [{ ...confirmed, ...patch }] },
        }),
      ).toThrow('版本与证据');
  });
  it('候选不能伪装事实，目标漂移或缺失节点不显示旧图', () => {
    for (const patch of [
      { kind: 'candidate', is_fact: true },
      { target_revision: 5 },
      { target_entry_id: 'hidden' },
    ])
      expect(() =>
        parseLearningRelations({
          data: { ...base, edges: [{ ...base.edges[0], ...patch }] },
        }),
      ).toThrow();
  });
  it('后端降级candidate被保留，不从证据或图上连线再升级', () => {
    const result = parseLearningRelations({
      data: {
        ...base,
        edges: [
          {
            ...confirmed,
            kind: 'candidate',
            is_fact: false,
            review_provenance: null,
            declared_target_revision: null,
            verification: 'pending_verification',
          },
        ],
      },
    });
    expect(result.edges[0].kind).toBe('candidate');
  });
  it('坏响应拒绝且异常不带上游正文或秘密', () => {
    expect(() => parseLearningRelations({ data: 'private-secret' })).toThrow(
      '关系响应未通过版本与证据核对。',
    );
  });
});

it('空投影、重复节点或缺失端点均拒绝，防止图定位使用无效坐标', () => {
  for (const data of [
    { ...base, nodes: [] },
    { ...base, nodes: [...nodes, nodes[0]] },
    { ...base, nodes: [nodes[0]] },
  ]) {
    expect(() => parseLearningRelations({ data })).toThrow('版本与证据核对');
  }
});

it('响应根必须精确绑定已规范化的本次请求', async () => {
  fetch.mockResolvedValue({ data: base });
  await expect(
    getLearningRelations(' other ', 1, ['navigation']),
  ).rejects.toThrow('版本与证据');
  await expect(
    getLearningRelations(' root ', 1, ['navigation']),
  ).resolves.toMatchObject({ root_entry_id: 'root' });
});
it('正式学习成果引用与来源引用使用同一证据边界', () => {
  const result = parseLearningRelations({
    data: {
      ...base,
      edges: [
        {
          ...confirmed,
          evidence_refs: [
            { learning_outcome_id: 'outcome', content_hash: 'hash' },
          ],
        },
      ],
    },
  });
  expect(result.edges[0].kind).toBe('confirmed');
});
