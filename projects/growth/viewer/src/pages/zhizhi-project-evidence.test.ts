import { expect, it } from 'vitest';
import { projectCommitLearning } from './zhizhi-model';
import type { KnowledgeCard } from '../api/knowledge';
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
