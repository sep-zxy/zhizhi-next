import { expect, it } from 'vitest';
import { candidateCommitSource, projectCommitLearning } from './zhizhi-model';
import type { GrowthBinding, GrowthProject } from '../api/growth';
import type { KnowledgeCandidate, KnowledgeCard } from '../api/knowledge';
it('工程学习关联同时要求项目与完整提交值，不能靠标题或短SHA猜测', () => {
  const base = {
    concept_id: 'concept',
    title: '相同标题',
    front_question: '',
    back_answer: '',
    back_explanation: '',
    card_version: 1,
    followup_mark: 'none' as const,
  };
  const cards: KnowledgeCard[] = [
    {
      ...base,
      card_id: 'exact',
      project_ids: ['p'],
      source_commit_shas: ['abcdef123'],
    },
    {
      ...base,
      card_id: 'wrong-project',
      project_ids: ['q'],
      source_commit_shas: ['abcdef123'],
    },
    {
      ...base,
      card_id: 'prefix',
      project_ids: ['p'],
      source_commit_shas: ['abcdef'],
    },
  ];
  expect(
    projectCommitLearning(cards, 'p', 'abcdef123').map((card) => card.card_id),
  ).toEqual(['exact']);
  expect(projectCommitLearning(cards, 'p', '')).toEqual([]);
  expect(projectCommitLearning(cards, '', 'abcdef123')).toEqual([]);
});

const firstProject: GrowthProject = {
  project_id: 'p1',
  name: '项目一',
  local_processing: true,
  model_allowed: false,
  cloud_allowed: false,
  created_at: '',
  last_publish_error: null,
};
const secondProject = { ...firstProject, project_id: 'p2', name: '项目二' };
const firstBinding: GrowthBinding = {
  binding_id: 'b1',
  project_id: 'p1',
  canonical_local_path: '/fixture/first',
  created_at: '',
  provider_names: [],
};
const secondBinding = { ...firstBinding, binding_id: 'b2', project_id: 'p2' };
const alternateBinding = { ...secondBinding, binding_id: 'b3' };
const candidate: KnowledgeCandidate = {
  candidate_id: 'candidate',
  title: '合成候选',
  reason: '用于验证提交的来源归属',
  source_commit_shas: ['abcdef123', 'def456'],
  project_ids: ['p1', 'p2'],
  evidence_count: 2,
  status: 'pending',
};
const state = {
  projects: [firstProject, secondProject],
  bindings: [firstBinding, alternateBinding, secondBinding],
};

it('多项目候选按完整提交找到来源，使用该来源的仓库绑定', () => {
  const withSources = {
    ...candidate,
    sources: [{ commit_sha: 'abcdef123', project_id: 'p1', binding_id: 'b1' }],
    source_refs: [
      { commit_sha: 'def', project_id: 'p1', binding_id: 'b1' },
      { commit_sha: 'def456', project_id: 'p2', binding_id: 'b2' },
    ],
  };
  expect(candidateCommitSource(withSources, 'abcdef123', state)).toMatchObject({
    project: firstProject, bindingId: 'b1',
  });
  expect(candidateCommitSource(withSources, 'def456', state)).toMatchObject({
    project: secondProject, bindingId: 'b2',
  });
});

it('旧候选只有在项目及仓库均可唯一确认时才回退', () => {
  expect(candidateCommitSource({ ...candidate, project_ids: ['p1'] }, 'abcdef123', state))
    .toMatchObject({ project: firstProject, bindingId: 'b1' });
  expect(candidateCommitSource({ ...candidate, project_ids: [] }, 'abcdef123', state))
    .toMatchObject({ reason: '无法确定此提交所属的项目。' });
  expect(candidateCommitSource(candidate, 'abcdef123', state))
    .toMatchObject({ reason: '无法确定此提交所属的项目。' });
  expect(candidateCommitSource({ ...candidate, project_ids: ['p2'] }, 'def456', state))
    .toMatchObject({ reason: '无法确定此提交所属的仓库。' });
});

it('来源冲突、绑定无效或拒绝本地处理时保留摘要并阻止跳转', () => {
  const source = { commit_sha: 'abcdef123', project_id: 'p1', binding_id: 'b1', commit_title: '已有摘要' };
  for (const conflict of [
    { ...candidate, sources: [source, { ...source, project_id: 'p2' }] },
    { ...candidate, project_ids: ['p2'], sources: [source] },
    { ...candidate, sources: [source, { ...source, binding_id: 'b2' }] },
    { ...candidate, sources: [{ ...source, binding_id: 'b2' }] },
  ]) {
    const evidence = candidateCommitSource(conflict, 'abcdef123', state);
    expect(evidence).toMatchObject({ title: '已有摘要', reason: expect.any(String) });
    expect(evidence.bindingId).toBeUndefined();
  }
  const revoked = candidateCommitSource({ ...candidate, sources: [source] }, 'abcdef123', {
    ...state,
    projects: [{ ...firstProject, local_processing: false }, secondProject],
  });
  expect(revoked).toEqual({ title: '已有摘要', reason: '此提交所属的项目当前未授权读取代码。' });
});
