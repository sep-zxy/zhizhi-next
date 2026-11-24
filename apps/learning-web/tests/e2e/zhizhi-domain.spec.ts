import { isBackendApiRoute } from '../fixtures/api-routes';
import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import type { EntryContextLink } from '../../src/api/entry-context';
import type {
  WikiLearningContext,
  WikiLearningSubmission,
} from '../../src/api/wiki-learning';
import type { UnderstandingSubmission } from '../../src/api/understanding';
import type { KnowledgeCandidate } from '../../src/api/knowledge';

test.use({
  launchOptions: {
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
  },
});
const wikiId = '44444444-4444-4444-8444-444444444444';
const wikiUrl = '/#/growth?view=wiki-learning&context_id=' + wikiId;
const hashText = (value: string) =>
  createHash('sha256').update(value, 'utf8').digest('hex');
const submissionId = '1'.repeat(64);
const otherCardId = '22222222-2222-4222-8222-222222222222';
const cardId = '11111111-1111-4111-8111-111111111111';
const root = '/api/growth/local/knowledge';
const formalEntry = {
  entry_id: 'entry_transaction_boundary',
  revision: 2,
  title: '指定的正式历史版本',
  body: '正式 v2 原文：失败重试需要独立边界。',
  content_hash: createHash('sha256')
    .update('正式 v2 原文：失败重试需要独立边界。', 'utf8')
    .digest('hex'),
  evidence_refs: [{ source_id: 'source-formal', source_revision: 1 }],
  data_classification: 'personal',
  classification_labels: ['private'],
};
const entryUrl =
  '/#/growth?view=entry-context&entry_id=' +
  formalEntry.entry_id +
  '&revision=2';
const card = {
  card_id: cardId,
  concept_id: 'concept-1',
  title: '事务边界学习',
  front_question: '何时提交，失败如何恢复？',
  back_answer: 'AI 示例讲解，不能代替本人观点。',
  back_explanation: '事务负责原子性。',
  card_version: 1,
  followup_mark: 'none',
  learning_stage: 'back',
  project_ids: ['p1'],
  source_commit_shas: ['abc123'],
};
const baseState = {
  projects: [
    {
      project_id: 'p1',
      name: '隔离测试项目',
      local_processing: true,
      model_allowed: false,
      cloud_allowed: false,
      created_at: '2026-01-01',
      last_publish_error: null,
    },
  ],
  bindings: [
    {
      binding_id: 'b1',
      project_id: 'p1',
      canonical_local_path: '/fixture',
      provider_names: [],
      created_at: '2026-01-01',
    },
  ],
  features: [
    { feature_id: 'f1', project_id: 'p1', label: '限定范围', base_ref: 'main' },
  ],
  snapshots: [],
  analysis_runs: [],
  opportunities: [],
  topic_proposals: [],
  growth_tasks: [],
  local_topics: [],
  local_topic_projects: [],
  local_explorations: [],
};
const candidateState = {
  ...baseState,
  projects: [
    ...baseState.projects,
    { ...baseState.projects[0], project_id: 'p2', name: '第二合成项目' },
    { ...baseState.projects[0], project_id: 'p3', name: '未授权项目', local_processing: false },
  ],
  bindings: [
    ...baseState.bindings,
    { ...baseState.bindings[0], project_id: 'p2', binding_id: 'b-default', canonical_local_path: '/fixture-default' },
    { ...baseState.bindings[0], project_id: 'p2', binding_id: 'b2', canonical_local_path: '/fixture-second' },
    { ...baseState.bindings[0], project_id: 'p3', binding_id: 'b3', canonical_local_path: '/fixture-denied' },
  ],
  features: [...baseState.features, { ...baseState.features[0], project_id: 'p2', feature_id: 'f2' }],
};
const commitCandidate: KnowledgeCandidate = {
  candidate_id: 'source-candidate',
  title: '多项目合成候选',
  reason: '两处改动都用于比较边界',
  status: 'pending',
  evidence_count: 3,
  project_ids: ['p1', 'p2', 'p3'],
  source_commit_shas: ['abc123', 'def456', 'aaa789'],
  sources: [
    { commit_sha: 'abc123', project_id: 'p1', binding_id: 'b1' },
    { commit_sha: 'def456', project_id: 'p2', binding_id: 'b2' },
    { commit_sha: 'aaa789', project_id: 'p3', binding_id: 'b3', commit_title: '不可读取的提交摘要' },
  ],
};
async function serve(
  page: Page,
  options: {
    failIndex?: boolean;
    empty?: boolean;
    failSave?: boolean;
    failSnoozedOnce?: boolean;
    writeback?: boolean;
    streamInterrupted?: boolean;
    bridge?:
      | 'unavailable'
      | 'candidate'
      | 'conflict'
      | 'lost'
      | 'retry'
      | 'delayed';
    noteText?: string;
    secondCard?: boolean;
    candidateEvidence?: boolean;
    entryContext?: 'empty' | 'conflict' | 'lost' | 'denied';
    wiki?:
      | 'candidate'
      | 'create-lost'
      | 'note-conflict'
      | 'note-lost'
      | 'submission-lost'
      | 'retry'
      | 'delayed'
      | 'denied';
    wikiExisting?: boolean;
    radarSource?: 'unknown' | 'unavailable';
    versionedRadar?: boolean;
  } = {},
) {
  let note = options.noteText ?? '本人已保存的原话';
  let saves = 0;
  let publishes = 0;
  const requests: string[] = [];
  const decisions: unknown[] = [];
  const completions: unknown[] = [];
  const radarSuggestion = {
    suggestion_id: 'r1',
    proposed_exercise: '解释幂等边界',
    reason: '真实待验证问题',
  };
  let radarState = 'proposed';
  let currentRadarRevision = 1;
  let pinnedRadarRevision: number | undefined;
  let radarResponse = '';
  let failSnoozed = options.failSnoozedOnce;
  const chats: Record<string, unknown>[] = [];
  const wikiPath = root + '/entry-learning-contexts';
  const wikiSubPath = wikiPath + '/' + wikiId + '/understanding/submissions';
  let wikiContext: WikiLearningContext | null = options.wikiExisting
    ? {
        context_id: wikiId,
        source_kind: 'wiki_entry',
        context_version: 1,
        entry: formalEntry,
        note_text: '独立自由理解旧稿',
        note_sha256: hashText('独立自由理解旧稿'),
        created_at: '2026-10-21',
        updated_at: '2026-10-21',
        state: 'reading',
      }
    : null;
  let wikiSubmission: WikiLearningSubmission | null = null;
  let wikiRelease = () => {};
  const wikiCreates: Record<string, unknown>[] = [];
  const wikiNotes: Record<string, unknown>[] = [];
  const wikiSubmits: Record<string, unknown>[] = [];
  const contextBodies: Record<string, unknown>[] = [];
  let linkedContext: EntryContextLink | null = null;
  let contextCardVersion = 1;
  let contextRevoked = false;
  const submissionBodies: unknown[] = [];
  let submission: UnderstandingSubmission | null = null;
  let committed = false;
  let releaseSubmission = () => {};
  const submissionsPath =
    root + '/cards/' + cardId + '/understanding/submissions';
  const conversation = [
    { role: 'user', content_text: '此前我问过事务边界' },
    { role: 'assistant', content_text: '此前会话中的辅助解释' },
  ];
  const methods: Record<string, string[]> = {
    '/api/auth/token': ['POST'],
    '/api/locale': ['GET'],
    '/api/growth/local': ['GET'],
    [root]: ['GET'],
    [root + '/cards/' + otherCardId]: ['GET'],
    [root + '/cards/' + cardId]: ['GET'],
    [root + '/cards/' + cardId + '/note']: ['PUT'],
    [root + '/cards/' + cardId + '/mark']: ['PUT'],
    [root + '/cards/' + cardId + '/chat/stream']: ['POST'],
    [root + '/wiki/drafts/d1']: ['PUT'],
    [root + '/wiki/drafts/d1/publish']: ['POST'],
    '/api/growth/local/bindings/b1/commits': ['GET'],
    '/api/growth/local/bindings/b2/commits': ['GET'],
    [root + '/commits/abc123']: ['GET'],
    [root + '/commits/def456']: ['GET'],
    [root + '/cards/' + otherCardId + '/understanding/submissions']: ['GET'],
    [submissionsPath]: ['GET', 'POST'],
    [submissionsPath + '/' + submissionId]: ['GET'],
    [wikiPath]: ['GET', 'POST'],
    [wikiPath + '/' + wikiId]: ['GET'],
    [wikiPath + '/' + wikiId + '/note']: ['POST'],
    [wikiSubPath]: ['GET', 'POST'],
    [wikiSubPath + '/' + submissionId]: ['GET'],
    [root + '/entry-context']: ['GET'],
    [root + '/entry-contexts']: ['POST'],
    [root + '/cards/' + cardId + '/entry-contexts']: ['GET'],
    [root + '/cards/' + otherCardId + '/entry-contexts']: ['GET'],
    [root + '/radar/runs']: ['POST'],
    [root + '/radar/snoozed']: ['GET'],
    [root + '/radar/r1/decision']: ['POST'],
    [root + '/radar/r1']: ['GET'],
    [root + '/radar/r1/complete']: ['POST'],
  };
  const draft = options.writeback
    ? {
        draft_id: 'd1',
        card_id: cardId,
        markdown: '新的边界说明',
        original_markdown: '旧的边界说明',
        status: 'draft',
      }
    : null;
  await page.route(isBackendApiRoute, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push(url.pathname + url.search);
    expect(
      methods[url.pathname],
      'Fixture must declare ' + url.pathname,
    ).toBeDefined();
    expect(
      methods[url.pathname],
      'HTTP contract for ' + url.pathname,
    ).toContain(request.method());
    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });
    if (url.pathname === '/api/auth/token')
      return json({ token: 'fixture-token' });
    if (url.pathname === '/api/locale') return json({ locale: 'zh-CN' });
    if (url.pathname === '/api/growth/local')
      return json(
        options.empty
          ? { ...baseState, projects: [], bindings: [], features: [] }
          : options.streamInterrupted
            ? {
                ...baseState,
                projects: [{ ...baseState.projects[0], model_allowed: true }],
                bindings: [
                  {
                    ...baseState.bindings[0],
                    provider_names: ['fixture-model'],
                  },
                ],
              }
            : options.candidateEvidence ? candidateState : baseState,
      );
    if (url.pathname === root)
      return options.failIndex
        ? json({ error: 'fixture unavailable' }, 503)
        : json({
            knowledge_mode: 'unified',
            cards: options.empty
              ? []
              : options.secondCard
                ? [
                    {
                      ...card,
                      card_id: otherCardId,
                      title: '列表排在前面的另一张卡',
                    },
                    card,
                  ]
                : [card],
            candidates: options.candidateEvidence ? [commitCandidate] : [],
            wiki_articles: [],
            vault_path: '',
          });
    if (url.pathname === root + '/cards/' + otherCardId)
      return json({
        card: {
          ...card,
          card_id: otherCardId,
          title: '列表排在前面的另一张卡',
        },
        learning: { stage: 'back', note_text: '另一张卡的原话', answers: {} },
        sources: [],
        questions: [],
        wiki_draft: null,
        conversation: [],
      });
    if (url.pathname === root + '/cards/' + cardId)
      return json({
        card: { ...card, card_version: contextCardVersion },
        learning: { stage: 'back', note_text: note, answers: {} },
        sources: [
          {
            source_ref_id: 'source1',
            relative_path: 'service.ts',
            commit_sha: 'abc123',
            project_id: 'p1',
            excerpt: '事务证据',
          },
        ],
        questions: [],
        wiki_draft: draft,
        conversation,
      });
    if (url.pathname === wikiPath) {
      if (request.method() === 'GET') {
        expect(Object.fromEntries(url.searchParams)).toEqual({
          limit: '20',
          offset: '0',
        });
        return json({
          items: wikiContext
            ? [
                {
                  context_id: wikiId,
                  source_kind: 'wiki_entry',
                  context_version: wikiContext.context_version,
                  entry_id: formalEntry.entry_id,
                  revision: 2,
                  content_hash: formalEntry.content_hash,
                  title: formalEntry.title,
                  note_sha256: wikiContext.note_sha256,
                  created_at: wikiContext.created_at,
                  updated_at: wikiContext.updated_at,
                  state: 'reading',
                },
              ]
            : [],
          has_more: false,
        });
      }
      const body = request.postDataJSON();
      wikiCreates.push(body);
      expect(body).toEqual({
        request_id: expect.any(String),
        entry_id: formalEntry.entry_id,
        revision: 2,
        expected_content_hash: formalEntry.content_hash,
      });
      expect(body.request_id).toMatch(/^[a-f0-9-]{36}$/);
      wikiContext ??= {
        context_id: wikiId,
        source_kind: 'wiki_entry',
        context_version: 1,
        entry: formalEntry,
        note_text: '',
        note_sha256: hashText(''),
        created_at: '2026-10-21',
        updated_at: '2026-10-21',
        state: 'reading',
      };
      if (options.wiki === 'create-lost') return route.abort('failed');
      await new Promise((resolve) => setTimeout(resolve, 120));
      return json(wikiContext);
    }
    if (url.pathname === wikiPath + '/' + wikiId) {
      expect(wikiContext).not.toBeNull();
      return contextRevoked || options.wiki === 'denied'
        ? json({ error: 'wiki-source-secret-sentinel' }, 403)
        : json(wikiContext);
    }
    if (url.pathname === wikiPath + '/' + wikiId + '/note') {
      expect(wikiContext).not.toBeNull();
      const body = request.postDataJSON();
      wikiNotes.push(body);
      expect(body).toEqual({
        request_id: expect.any(String),
        expected_context_version: wikiContext!.context_version,
        note_text: expect.any(String),
      });
      expect(body.request_id).toMatch(/^[a-f0-9-]{36}$/);
      if (options.wiki === 'note-conflict' && wikiNotes.length === 1) {
        wikiContext = {
          ...wikiContext!,
          context_version: 2,
          note_text: '另一端保存的本人原话',
          note_sha256: hashText('另一端保存的本人原话'),
        };
        return json({ error: 'CONTEXT_VERSION_STALE' }, 409);
      }
      wikiContext = {
        ...wikiContext!,
        context_version:
          wikiContext!.context_version +
          (body.note_text === wikiContext!.note_text ? 0 : 1),
        note_text: body.note_text,
        note_sha256: hashText(body.note_text),
      };
      if (options.wiki === 'note-lost') return route.abort('failed');
      await new Promise((resolve) => setTimeout(resolve, 120));
      return json(wikiContext);
    }
    if (url.pathname === wikiSubPath) {
      if (contextRevoked)
        return json({ error: 'wiki-source-secret-sentinel' }, 403);
      if (request.method() === 'GET')
        return json({
          items: wikiSubmission ? [wikiSubmission] : [],
          has_more: false,
        });
      expect(wikiContext).not.toBeNull();
      const body = request.postDataJSON();
      wikiSubmits.push(body);
      const text =
        wikiSubmission && wikiSubmission.note_sha256 === body.note_sha256
          ? wikiSubmission.user_text
          : wikiContext!.note_text;
      const version =
        wikiSubmission && wikiSubmission.note_sha256 === body.note_sha256
          ? wikiSubmission.context_version
          : wikiContext!.context_version;
      expect(body).toEqual({
        context_version: version,
        note_sha256: hashText(text),
      });
      wikiSubmission = {
        submission_id: submissionId,
        context_id: wikiId,
        context_version: version,
        note_sha256: body.note_sha256,
        user_text: text,
        delivery_state: 'pending',
        outcome_id: null,
        task_id: null,
        remote_state: 'not_submitted',
        task: null,
        change: null,
        error: null,
      };
      if (options.wiki === 'submission-lost') return route.abort('failed');
      if (options.wiki === 'retry')
        wikiSubmission =
          wikiSubmits.length === 1
            ? {
                ...wikiSubmission,
                remote_state: 'unknown',
                error: { code: 'NETWORK_ERROR', retryable: true },
              }
            : {
                ...wikiSubmission,
                delivery_state: 'acknowledged',
                remote_state: 'available',
                outcome_id: 'free-outcome-1',
                task_id: 'free-task-1',
                task: {
                  task_id: 'free-task-1',
                  kind: 'knowledge.generate',
                  state: 'queued',
                },
              };
      if (options.wiki === 'delayed')
        await new Promise<void>((resolve) => {
          wikiRelease = resolve;
        });
      else await new Promise((resolve) => setTimeout(resolve, 120));
      return json(wikiSubmission, 202);
    }
    if (url.pathname === wikiSubPath + '/' + submissionId) {
      if (contextRevoked)
        return json({ error: 'wiki-source-secret-sentinel' }, 403);
      expect(wikiSubmission).not.toBeNull();
      if (options.wiki === 'candidate')
        wikiSubmission = {
          ...wikiSubmission!,
          delivery_state: 'acknowledged',
          remote_state: 'available',
          outcome_id: 'free-outcome-1',
          task_id: 'free-task-1',
          task: {
            task_id: 'free-task-1',
            kind: 'knowledge.generate',
            state: 'succeeded',
          },
          change: {
            change_id: 'free-change-1',
            state: committed ? 'committed' : 'needs_ai_review',
            entry_id: 'independent_understanding_entry',
            base_revision: 0,
            committed_revision: committed ? 1 : null,
            title: '本人独立理解候选',
            path: 'understandings/free.md',
            original_markdown: '',
            candidate_markdown: '从本人原话整理的独立解释',
            evidence_refs: [
              {
                learning_outcome_id: 'free-outcome-1',
                source_id: 'source-formal',
                revision: 2,
              },
            ],
          },
        };
      return json(wikiSubmission);
    }
    if (url.pathname === root + '/entry-context') {
      expect(Object.fromEntries(url.searchParams)).toEqual({
        entry_id: formalEntry.entry_id,
        revision: '2',
      });
      if (options.entryContext === 'denied' || contextRevoked)
        return json({ error: 'private-entry-secret-sentinel' }, 403);
      return json({
        entry: formalEntry,
        cards:
          options.entryContext === 'empty'
            ? []
            : [
                {
                  card_id: cardId,
                  card_version: contextCardVersion,
                  title: card.title,
                },
              ],
      });
    }
    if (url.pathname === root + '/entry-contexts') {
      const body = request.postDataJSON();
      contextBodies.push(body);
      expect(body).toEqual({
        request_id: expect.any(String),
        entry_id: formalEntry.entry_id,
        revision: 2,
        expected_content_hash: formalEntry.content_hash,
        target_card_id: cardId,
        expected_card_version: contextCardVersion,
      });
      expect(body.request_id).toMatch(/^[a-f0-9-]{36}$/);
      if (options.entryContext === 'conflict' && contextBodies.length === 1) {
        contextCardVersion = 2;
        return json({ error: 'CARD_VERSION_STALE' }, 409);
      }
      linkedContext = {
        context_id: '33333333-3333-4333-8333-333333333333',
        entry_id: formalEntry.entry_id,
        revision: 2,
        content_hash: formalEntry.content_hash,
        card_id: cardId,
        card_version: contextCardVersion,
        created_at: '2026-10-21',
        state: 'linked',
      };
      if (options.entryContext === 'lost') return route.abort('failed');
      await new Promise((resolve) => setTimeout(resolve, 120));
      return json(linkedContext);
    }
    if (
      url.pathname.endsWith('/entry-contexts') &&
      url.pathname.startsWith(root + '/cards/')
    ) {
      if (contextRevoked)
        return json({ error: 'private-entry-secret-sentinel' }, 403);
      return json({
        items:
          linkedContext &&
          url.pathname === root + '/cards/' + cardId + '/entry-contexts'
            ? [{ ...linkedContext, entry: formalEntry }]
            : [],
        has_more: false,
      });
    }
    if (
      url.pathname ===
      root + '/cards/' + otherCardId + '/understanding/submissions'
    )
      return json({ items: [], has_more: false });
    if (url.pathname === submissionsPath) {
      if (options.bridge === 'unavailable')
        return json({ error: 'sentinel-private-upstream-secret' }, 404);
      if (request.method() === 'GET')
        return json({ items: submission ? [submission] : [], has_more: false });
      const body = request.postDataJSON();
      submissionBodies.push(body);
      const submittedText =
        submission && submission.note_sha256 === body.note_sha256
          ? submission.user_text
          : note;
      expect(body).toEqual({
        card_version: 1,
        note_sha256: createHash('sha256')
          .update(submittedText, 'utf8')
          .digest('hex'),
      });
      if (options.bridge === 'conflict')
        return json({ error: 'stale_saved_note' }, 409);
      submission = {
        submission_id: submissionId,
        card_id: cardId,
        card_version: 1,
        note_sha256: body.note_sha256,
        user_text: submittedText,
        delivery_state: 'pending',
        outcome_id: null,
        task_id: null,
        remote_state: 'not_submitted',
        task: null,
        change: null,
        error: null,
      };
      if (options.bridge === 'lost') return route.abort('failed');
      if (options.bridge === 'retry')
        submission =
          submissionBodies.length === 1
            ? {
                ...submission,
                remote_state: 'unknown',
                error: { code: 'NETWORK_ERROR', retryable: true },
              }
            : {
                ...submission,
                delivery_state: 'acknowledged',
                remote_state: 'available',
                outcome_id: 'outcome-1',
                task_id: 'generation-1',
                task: {
                  task_id: 'generation-1',
                  kind: 'knowledge.generate',
                  state: 'queued',
                },
              };
      if (options.bridge === 'delayed')
        await new Promise<void>((resolve) => {
          releaseSubmission = resolve;
        });
      else await new Promise((resolve) => setTimeout(resolve, 120));
      return json(submission, 202);
    }
    if (url.pathname === submissionsPath + '/' + submissionId) {
      expect(submission).not.toBeNull();
      if (options.bridge === 'candidate')
        submission = {
          ...submission!,
          delivery_state: 'acknowledged',
          outcome_id: 'outcome-1',
          task_id: 'generation-1',
          remote_state: 'available',
          task: {
            task_id: 'generation-1',
            kind: 'knowledge.generate',
            state: 'succeeded',
          },
          change: {
            change_id: 'understanding-change-1',
            state: committed ? 'committed' : 'needs_ai_review',
            entry_id: 'entry-1',
            base_revision: 3,
            committed_revision: committed ? 4 : null,
            title: '从原话整理的事务边界候选',
            path: 'architecture/transactions.md',
            original_markdown: '旧版只说明原子性',
            candidate_markdown: '新版加入重试边界与本人实践',
            evidence_refs: [
              {
                source_id: 'source1',
                learning_outcome_id: 'outcome-1',
                revision: 1,
              },
            ],
          },
        };
      return json(submission);
    }
    if (url.pathname === root + '/cards/' + cardId + '/note') {
      saves++;
      if (options.failSave) return json({ error: '保存未确认' }, 503);
      note = request.postDataJSON().text;
      await new Promise((resolve) => setTimeout(resolve, 120));
      return json({ note_text: note });
    }
    if (url.pathname === root + '/cards/' + cardId + '/chat/stream') {
      expect(options.streamInterrupted).toBe(true);
      chats.push(request.postDataJSON());
      const body =
        chats.length === 1
          ? 'event: delta\ndata: ' +
            JSON.stringify('仅收到一半的 AI 回复') +
            '\n\n'
          : 'event: done\ndata: ' +
            JSON.stringify({
              conversation: [
                ...conversation,
                { role: 'user', content_text: chats.at(-1)?.message },
                { role: 'assistant', content_text: '重试确认的完整回复' },
              ],
            }) +
            '\n\n';
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body,
      });
    }
    if (url.pathname === root + '/cards/' + cardId + '/mark')
      return json({ mark: request.postDataJSON().mark });
    if (url.pathname === root + '/wiki/drafts/d1' && request.method() === 'PUT')
      return json({ saved: true });
    if (url.pathname === root + '/wiki/drafts/d1/publish') {
      publishes++;
      await new Promise((resolve) => setTimeout(resolve, 120));
      return json(
        {
          state: 'submitted',
          published: false,
          change_id: 'change-1',
          task_id: 'task-1',
        },
        202,
      );
    }
    if (url.pathname === '/api/growth/local/bindings/b1/commits')
      return json({
        commits: [
          {
            sha: 'abc123',
            title: '明确事务边界',
            paths: ['service.ts'],
            authored_at: '2026-01-01',
          },
        ],
        base_sha: 'base',
        next_cursor: null,
        authors: [],
      });
    if (url.pathname === '/api/growth/local/bindings/b2/commits') {
      expect(url.searchParams.get('feature_id')).toBe('f2');
      return json({ commits: [], base_sha: 'base', next_cursor: null, authors: [] });
    }
    if (url.pathname === root + '/commits/abc123') {
      expect(url.searchParams.get('binding_id')).toBe('b1');
      return json({ title: '明确事务边界', patch: '-旧实现\n+新的事务边界' });
    }
    if (url.pathname === root + '/commits/def456') {
      expect(url.searchParams.get('binding_id')).toBe('b2');
      return json({ title: '第二项目的队列改动', patch: '+第二仓库的队列实现' });
    }
    if (url.pathname === root + '/radar/runs') {
      if (radarState === 'proposed') pinnedRadarRevision ??= currentRadarRevision;
      return json({
        suggestions: radarState === 'proposed'
          ? [{ ...radarSuggestion, suggestion_state: radarState }]
          : [],
      });
    }
    if (url.pathname === root + '/radar/snoozed') {
      if (failSnoozed) {
        failSnoozed = false;
        return json({ error: '暂时无法读取暂缓建议' }, 503);
      }
      return json({
        suggestions: radarState === 'snoozed'
          ? [{ ...radarSuggestion, suggestion_state: radarState }]
          : [],
        next_cursor: null,
      });
    }
    if (url.pathname === root + '/radar/r1/decision') {
      const { decision } = request.postDataJSON();
      decisions.push({ decision });
      expect(['accept', 'snooze']).toContain(decision);
      radarState = decision === 'snooze' ? 'snoozed' : 'accepted';
      return json({
        suggestion_id: 'r1',
        suggestion_state: radarState,
        ...(radarState === 'accepted' ? { learning_id: 'learning1' } : {}),
      });
    }
    if (url.pathname === root + '/radar/r1') {
      const body = options.versionedRadar
        ? `第 ${pinnedRadarRevision ?? currentRadarRevision} 版的合成资料`
        : '已有知识证据';
      const suggestionState = options.versionedRadar ? radarState : 'accepted';
      return json({
        suggestion_id: 'r1',
        suggestion_state: options.radarSource ? 'completed' : suggestionState,
        ...(options.radarSource ? { user_text: '这是已经提交的本人原话。' } : {}),
        ...(options.versionedRadar ? { user_text: radarResponse } : {}),
        practice: {
          entry_id: 'entry1',
          title: options.radarSource ? '' : '幂等边界',
          body: options.radarSource ? '' : body,
          question: options.radarSource === 'unavailable' ? '' : '重试何时安全？',
          source_status: options.radarSource ?? 'available',
        },
      });
    }
    if (url.pathname === root + '/radar/r1/complete') {
      const response = request.postDataJSON();
      completions.push(response);
      if (options.versionedRadar) {
        radarResponse = response.user_text;
        radarState = 'completed';
        return json({ state: 'submitted', published: false, suggestion_id: 'r1' });
      }
      return json({ state: 'pending', published: false, suggestion_id: 'r1' });
    }
    return json(
      { error: 'fixture route not implemented: ' + url.pathname },
      404,
    );
  });
  return {
    wikiCreates,
    wikiNotes,
    wikiSubmits,
    wikiRelease: () => wikiRelease(),
    saves: () => saves,
    publishes: () => publishes,
    requests,
    decisions,
    completions,
    advanceRadarSource: () => { currentRadarRevision += 1; },
    chats,
    submissionBodies,
    contextBodies,
    revokeContext: () => {
      contextRevoked = true;
    },
    releaseSubmission: () => releaseSubmission(),
    confirmWrite: () => {
      committed = true;
    },
  };
}
test('专域首页与空态不制造设计示例数据', async ({ page }) => {
  await serve(page, { empty: true });
  await page.goto('/#/growth?view=home');
  await expect(
    page.getByRole('heading', { name: '继续你的工程学习' }),
  ).toBeVisible();
  await expect(page.getByText('从自己的项目或笔记开始')).toBeVisible();
  const nav = page.locator('#growth-navigation');
  await expect(nav.getByRole('link')).toHaveCount(7);
  await expect(nav).not.toContainText('收件箱');
  await expect(nav).not.toContainText('重要确认');
  await expect(page.getByText('SQLite 单写与 Git CAS')).toHaveCount(0);
});
test('API 错误不是空成功', async ({ page }) => {
  await serve(page, { failIndex: true });
  await page.goto('/#/growth?view=library');
  await expect(page.getByRole('alert')).toContainText('读取未完成');
  await expect(page.getByText('还没有符合条件的材料')).toHaveCount(0);
});
test('学习主题设置保留旧深链接，学习库也有明确入口', async ({ page }) => {
  await serve(page);
  await page.goto('/#/growth?view=settings');
  await expect(
    page.getByRole('heading', { name: '学习主题管理', exact: true }),
  ).toBeVisible();
  await expect(page.locator('#growth-cloud-url')).toBeVisible();
  await page.getByRole('link', { name: '返回学习库' }).click();
  await page.getByRole('link', { name: '学习主题管理', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '学习主题管理', exact: true }),
  ).toBeVisible();
});
test('保存本人原话，双击只一次，AI 讲解不代写', async ({ page }) => {
  const calls = await serve(page);
  await page.goto('/#/growth?view=session&card=' + cardId);
  const answer = page.getByRole('textbox', { name: '我的理解', exact: true });
  await expect(answer).toHaveValue('本人已保存的原话');
  await answer.fill('这是我自己的架构取舍');
  await page
    .getByRole('button', { name: '保存我的理解', exact: true })
    .dblclick();
  await expect(
    page.getByText('本人原话已保存到本机学习记录，未自动回填或公开。'),
  ).toBeVisible();
  expect(calls.saves()).toBe(1);
  await expect(answer).toHaveValue('这是我自己的架构取舍');
  await expect(page.getByRole('button', { name: '发送追问' })).toBeDisabled();
});
test('保存失败与导航返回都保留本人草稿', async ({ page }) => {
  await serve(page, { failSave: true });
  await page.goto('/#/growth?view=session&card=' + cardId);
  await page
    .getByRole('textbox', { name: '我的理解', exact: true })
    .fill('需要保留的本机草稿');
  await page.getByRole('button', { name: '保存我的理解', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('输入仍保留');
  await page.getByRole('link', { name: '返回学习库' }).click();
  await page.goBack();
  await expect(
    page.getByRole('textbox', { name: '我的理解', exact: true }),
  ).toHaveValue('需要保留的本机草稿');
});
test('开放理解服务不可用时明确阻止提交，不伪造已完成或回显秘密', async ({
  page,
}) => {
  const calls = await serve(page, { bridge: 'unavailable' });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  await expect(
    page.getByRole('button', { name: '生成知识候选', exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: '提交理解，生成并复核候选', exact: true }),
  ).toBeDisabled();
  await expect(page.getByRole('alert')).toContainText('操作未获确认');
  await expect(page.locator('body')).not.toContainText(
    'sentinel-private-upstream-secret',
  );
  expect(calls.submissionBodies).toEqual([]);
});
test('正式候选提交只显示待复核，不冒称已公开', async ({ page }) => {
  const calls = await serve(page, { writeback: true });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  await expect(page.getByLabel('正式知识与候选差异')).toContainText(
    '新的边界说明',
  );
  await page.getByRole('button', { name: '提交学习成果' }).dblclick();
  await expect(page.getByText('已提交复核，尚未确认正式写入。')).toBeVisible();
  expect(calls.publishes()).toBe(1);
  await expect(page.getByText('change_id: change-1')).toBeVisible();
});
test('项目读取限定范围，不发起全历史扫描或模型发送', async ({ page }) => {
  const calls = await serve(page);
  await page.goto('/#/growth?view=projects');
  await expect(page.getByRole('heading', { name: '选择已授权的工程项目' })).toBeVisible();
  await page.getByRole('link', { name: '隔离测试项目' }).click();
  await expect(page).toHaveURL(/view=projects&project=p1$/);
  await expect(page.getByText('先选择一条具体提交。')).toBeVisible();
  await page.goto('/#/growth?view=projects&project=p1&commit=abc123');
  await expect(page.getByText('新的事务边界', { exact: false })).toBeVisible();
  expect(calls.requests.some((url) => url.includes('scan=true'))).toBe(false);
  expect(calls.requests.some((url) => url.endsWith('/analysis'))).toBe(false);
  await expect(
    page.getByRole('button', { name: '职责演进图（接口待接入）' }),
  ).toBeDisabled();
});

for (const entry of ['learning', 'pending']) {
  test(`候选入口 ${entry} 的代表提交与片段使用各自的项目和仓库`, async ({ page }) => {
    const calls = await serve(page, { candidateEvidence: true });
    await page.goto('/#/growth?view=' + entry);
    await expect(page.getByRole('heading', { name: '聚合依据与来源' })).toBeVisible();
    const first = page.getByRole('link', { name: /明确事务边界/ });
    const second = page.getByRole('link', { name: /第二项目的队列改动/ });
    const firstUrl = '#/growth?view=projects&project=p1&commit=abc123&binding=b1';
    const secondUrl = '#/growth?view=projects&project=p2&commit=def456&binding=b2';
    await expect(first).toHaveAttribute('href', firstUrl);
    await expect(second).toHaveAttribute('href', secondUrl);
    await page.locator('.knowledge-evidence-commit').filter({ has: first }).getByText('查看代码片段').click();
    await expect(page.getByText('新的事务边界', { exact: false })).toBeVisible();
    await page.locator('.knowledge-evidence-commit').filter({ has: second }).getByText('查看代码片段').click();
    await expect(page.getByText('第二仓库的队列实现', { exact: false })).toBeVisible();
    await expect(page.getByText('不可读取的提交摘要')).toBeVisible();
    await expect(page.getByText('此提交所属的项目当前未授权读取代码。')).toBeVisible();
    await expect(page.getByRole('link', { name: /不可读取的提交摘要/ })).toHaveCount(0);
    const detailRequests = () => calls.requests.filter(url => url.startsWith(root + '/commits/'));
    expect([...new Set(detailRequests())].sort()).toEqual([
      root + '/commits/abc123?binding_id=b1',
      root + '/commits/def456?binding_id=b2',
    ]);

    if (entry === 'learning') {
      await second.click();
      await expect(page).toHaveURL(url => url.hash === secondUrl);
      await expect(page.getByRole('heading', { name: '第二项目的队列改动' })).toBeVisible();
      await expect(page.getByText('第二仓库的队列实现', { exact: false })).toBeVisible();

      const previousRequests = detailRequests().length;
      await page.goto('/' + secondUrl.replace('binding=b2', 'binding=expired'));
      await expect(page.getByRole('alert')).toContainText('此提交所属的仓库当前未绑定或未授权。');
      await expect(page.getByText('第二仓库的队列实现', { exact: false })).toHaveCount(0);
      expect(detailRequests()).toHaveLength(previousRequests);
    } else {
      await first.click();
      await expect(page).toHaveURL(url => url.hash === firstUrl);
      await expect(page.getByRole('heading', { name: '明确事务边界' })).toBeVisible();
      await expect(page.getByText('新的事务边界', { exact: false })).toBeVisible();
    }
  });
}
test('雷达接受后只提交真实原话，离线显示待发', async ({ page }) => {
  const calls = await serve(page);
  await page.goto('/#/growth?view=radar');
  await page.getByRole('button', { name: '查看学习建议' }).click();
  await page.getByRole('button', { name: '开始练习' }).click();
  await page.getByText('查阅已有知识与依据').click();
  await expect(page.getByText('已有知识证据')).toBeVisible();
  await page
    .getByRole('textbox', { name: '用自己的话说明架构取舍或应用思路' })
    .fill('我会用请求标识保证重试幂等');
  await page.getByRole('button', { name: '提交学习原话' }).click();
  await expect(
    page.getByText('已保存在本机，等待连接恢复后提交。'),
  ).toBeVisible();
  expect(calls.decisions).toEqual([{ decision: 'accept' }]);
  expect(calls.completions).toEqual([
    { user_text: '我会用请求标识保证重试幂等' },
  ]);
});

test('雷达暂缓后刷新仍能找回同一建议并继续练习', async ({ page }) => {
  const calls = await serve(page, { failSnoozedOnce: true });
  await page.goto('/#/growth?view=radar');
  const radar = page.getByRole('region', { name: '架构学习雷达' });
  const saved = radar.getByRole('button', { name: '稍后再学', exact: true });
  const empty = radar.getByText('当前没有可以继续练习的暂缓建议。', { exact: true });
  const question = radar.getByRole('heading', { name: '解释幂等边界', exact: true });
  await radar.getByRole('button', { name: '查看学习建议' }).click();
  await expect(question).toBeVisible();
  await radar.getByRole('button', { name: '稍后', exact: true }).click();
  await expect(radar.getByText('已移到「稍后再学」。')).toBeVisible();
  await expect(question).toHaveCount(0);

  await page.reload();
  await saved.click();
  await expect(radar.getByRole('alert')).toContainText('暂时无法读取暂缓建议');
  await expect(empty).toHaveCount(0);
  await saved.click();
  await expect(question).toBeVisible();
  await expect(radar.getByRole('button', { name: '不采用' })).toHaveCount(0);
  await radar.getByRole('button', { name: '开始练习' }).click();
  await expect(radar.getByText('重试何时安全？')).toBeVisible();
  await radar.getByRole('button', { name: '返回学习建议' }).click();
  await saved.click();
  await expect(empty).toBeVisible();
  expect(calls.decisions).toEqual([{ decision: 'snooze' }, { decision: 'accept' }]);
});

test('雷达暂缓期间资料更新后仍按第 1 版练习、提交和回看', async ({ page }) => {
  const calls = await serve(page, { versionedRadar: true });
  await page.goto('/#/growth?view=radar');
  const radar = page.getByRole('region', { name: '架构学习雷达' });
  await radar.getByRole('button', { name: '查看学习建议' }).click();
  await radar.getByRole('button', { name: '稍后', exact: true }).click();
  await expect(radar.getByText('已移到「稍后再学」。')).toBeVisible();
  calls.advanceRadarSource();
  await page.reload();
  await radar.getByRole('button', { name: '稍后再学' }).click();
  await expect(radar.getByRole('heading', { name: '解释幂等边界' })).toHaveCount(1);
  await radar.getByRole('button', { name: '开始练习' }).click();
  await radar.getByText('查阅已有知识与依据').click();
  await expect(radar.getByText('第 1 版的合成资料')).toBeVisible();
  await expect(radar.getByText('第 2 版的合成资料')).toHaveCount(0);
  const userText = '我会用合成的第 1 版资料解释重试条件。';
  const answer = radar.getByRole('textbox', { name: '用自己的话说明架构取舍或应用思路' });
  await answer.fill(userText);
  await radar.getByRole('button', { name: '提交学习原话' }).click();
  await expect(radar.getByText('学习原话已提交，知识条目仍待生成与复核。')).toBeVisible();

  await page.reload();
  await expect(answer).toHaveValue(userText);
  await expect(answer).not.toBeEditable();
  await radar.getByText('查阅已有知识与依据').click();
  await expect(radar.getByText('第 1 版的合成资料')).toBeVisible();
  await expect(radar.getByText('第 2 版的合成资料')).toHaveCount(0);
  expect(calls.decisions).toEqual([{ decision: 'snooze' }, { decision: 'accept' }]);
  expect(calls.completions).toEqual([{ user_text: userText }]);
});

for (const [radarSource, notice] of [
  ['unknown', '当时的资料版本无法确认。'],
  ['unavailable', '当时的资料无法读取。'],
] as const) {
  test(`回看旧雷达练习时保留原话和提示 ${radarSource}`, async ({ page }) => {
    const calls = await serve(page, { radarSource });
    await page.goto('/#/growth?view=radar&radar=r1');
    await expect(page.getByText(notice)).toBeVisible();
    const response = page.getByRole('textbox', { name: '用自己的话说明架构取舍或应用思路' });
    await expect(response).toHaveValue('这是已经提交的本人原话。');
    await expect(response).not.toBeEditable();
    await expect(page.getByText('查阅已有知识与依据')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '提交学习原话' })).toBeDisabled();
    expect(calls.completions).toEqual([]);
  });
}

test('首页与学习库继续同一卡片的会话和本人草稿', async ({ page }) => {
  const calls = await serve(page, { secondCard: true });
  await page.goto('/#/growth?view=home');
  await page.getByRole('link', { name: '事务边界学习', exact: true }).click();
  await expect(page).toHaveURL(new RegExp('view=session.*card=' + cardId));
  await expect(
    page.getByText('此前我问过事务边界', { exact: true }),
  ).toBeVisible();
  const answer = page.getByRole('textbox', { name: '我的理解', exact: true });
  await answer.fill('尚未保存但仍属于本人的解释');
  await page.getByRole('link', { name: '返回学习库' }).click();
  await page
    .locator('article')
    .filter({
      has: page.getByRole('link', { name: '事务边界学习', exact: true }),
    })
    .getByRole('link', { name: '继续学习', exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp('view=session.*card=' + cardId));
  await expect(answer).toHaveValue('尚未保存但仍属于本人的解释');
  await expect(
    page.getByText('此前会话中的辅助解释', { exact: true }),
  ).toBeVisible();
  await page
    .locator('#growth-navigation')
    .getByRole('link', { name: '首页', exact: true })
    .click();
  await page.getByRole('link', { name: '继续学习', exact: true }).click();
  await expect(page).toHaveURL(new RegExp('view=session.*card=' + cardId));
  await expect(answer).toHaveValue('尚未保存但仍属于本人的解释');
  expect(calls.saves()).toBe(0);
});
test('流提前中断保留原话与提问，重试同一请求不伪造完整回复', async ({
  page,
}) => {
  const calls = await serve(page, { streamInterrupted: true });
  await page.goto('/#/growth?view=session&card=' + cardId);
  const answer = page.getByRole('textbox', { name: '我的理解', exact: true });
  await answer.fill('本人观点不能被 AI 片段覆盖');
  const question = page.getByRole('textbox', { name: '继续提问', exact: true });
  await question.fill('失败重试如何保持原子性？');
  await page.getByRole('button', { name: '发送追问' }).click();
  await expect(page.getByRole('alert')).toContainText('回复连接提前结束');
  await expect(
    page.getByText('AI 回复片段 · 未确认完成', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('仅收到一半的 AI 回复', { exact: true }),
  ).toBeVisible();
  await expect(answer).toHaveValue('本人观点不能被 AI 片段覆盖');
  await expect(question).toHaveValue('失败重试如何保持原子性？');
  await page.getByRole('button', { name: '发送追问' }).click();
  await expect(
    page.getByText('重试确认的完整回复', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('AI 回复片段 · 未确认完成', { exact: true }),
  ).toHaveCount(0);
  await expect(question).toHaveValue('');
  await expect(answer).toHaveValue('本人观点不能被 AI 片段覆盖');
  expect(calls.chats).toHaveLength(2);
  expect(calls.chats[0]).toEqual({
    request_id: expect.any(String),
    provider_name: 'fixture-model',
    message: '失败重试如何保持原子性？',
    stage: 'back',
  });
  expect(calls.chats[1]).toEqual(calls.chats[0]);
  expect(calls.saves()).toBe(0);
});

test('本人原话提交到任务与只读候选差异，刷新恢复不重复提交', async ({
  page,
}) => {
  const note = '  本人的完整原话\n保留边界与空格  ';
  const calls = await serve(page, { bridge: 'candidate', noteText: note });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  const submit = page.getByRole('button', {
    name: '提交理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  const progress = page.getByRole('region', { name: '成果回填处理进度' });
  await expect(progress.getByRole('listitem')).toHaveCount(3);
  await expect(progress).toContainText('尚未提交');
  await submit.dblclick();
  await expect(
    page.getByText('已保存到本机待发，尚未确认服务受理。', { exact: true }),
  ).toBeVisible();
  await expect(progress).toContainText('已保存到本机待发');
  expect(calls.submissionBodies).toHaveLength(1);
  await expect(submit).toBeDisabled();
  await expect(
    page.getByText('已确认正式写入，不代表公开。', { exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole('button', { name: '刷新理解提交状态', exact: true })
    .click();
  await expect(
    page.getByText('候选等待独立复核，尚未确认正式写入。', { exact: true }),
  ).toBeVisible();
  await expect(progress).toContainText('等待独立复核');
  await expect(progress).toContainText('尚未确认写入');
  const diff = page.getByLabel('本人理解候选与基线差异');
  await expect(diff).toContainText('旧版只说明原子性');
  await expect(diff).toContainText('新版加入重试边界与本人实践');
  const tabs = page.getByRole('group', { name: '理解候选内容视图' });
  await tabs.getByRole('button', { name: '候选内容', exact: true }).click();
  await expect(page.getByLabel('只读理解候选')).toHaveText(
    '新版加入重试边界与本人实践',
  );
  await expect(page.getByRole('textbox', { name: '候选草稿' })).toHaveCount(0);
  await tabs.getByRole('button', { name: '引用与证据', exact: true }).click();
  await expect(page.getByLabel('理解候选授权引用')).toContainText(
    '来源 ID：source1',
  );
  await expect(page.getByLabel('理解候选授权引用')).toContainText(
    '理解成果 ID：outcome-1',
  );
  calls.confirmWrite();
  await page
    .getByRole('button', { name: '刷新理解提交状态', exact: true })
    .click();
  await expect(
    page.getByText('已确认正式写入，不代表公开。', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('公开状态：此接口未提供，不推定已公开。', { exact: true }),
  ).toBeVisible();
  await expect(progress).toContainText('已确认写入 v');
  await page.reload();
  await expect(progress).toContainText('已确认写入 v');
  await expect(submit).toBeDisabled();
  await expect(
    page.getByText('已确认正式写入，不代表公开。', { exact: true }),
  ).toBeVisible();
  expect(calls.submissionBodies).toEqual([
    {
      card_version: 1,
      note_sha256: createHash('sha256').update(note, 'utf8').digest('hex'),
    },
  ]);
  expect(
    calls.requests.some(
      (path) =>
        path.includes('/radar/') ||
        path.endsWith('/wiki/draft') ||
        path.endsWith('/complete'),
    ),
  ).toBe(false);
});
test('保存版本冲突阻止理解提交，先回读而非自动重试', async ({ page }) => {
  const calls = await serve(page, { bridge: 'conflict' });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  const submit = page.getByRole('button', {
    name: '提交理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(page.getByRole('alert')).toContainText('不会自动重新提交');
  await expect(submit).toBeDisabled();
  await expect(
    page.getByText('本人已保存的原话', { exact: true }),
  ).toBeVisible();
  expect(calls.submissionBodies).toHaveLength(1);
  await page
    .getByRole('button', { name: '刷新理解提交状态', exact: true })
    .click();
  await expect(submit).toBeEnabled();
  expect(calls.submissionBodies).toHaveLength(1);
  await expect(page.getByLabel('本人理解候选与基线差异')).toHaveCount(0);
});
test('提交响应丢失后通过 GET 恢复待发事实，不重复 POST', async ({ page }) => {
  const calls = await serve(page, { bridge: 'lost' });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  const submit = page.getByRole('button', {
    name: '提交理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(page.getByRole('alert')).toContainText('操作未获确认');
  await expect(submit).toBeDisabled();
  await page
    .getByRole('button', { name: '刷新理解提交状态', exact: true })
    .click();
  await expect(
    page.getByText('已保存到本机待发，尚未确认服务受理。', { exact: true }),
  ).toBeVisible();
  await expect(submit).toBeDisabled();
  expect(calls.submissionBodies).toHaveLength(1);
  await expect(
    page.getByText('已确认正式写入，不代表公开。', { exact: true }),
  ).toHaveCount(0);
});

test('显式重试只递送此前待发原话，不拿后来修改的理解替换', async ({ page }) => {
  const calls = await serve(page, { bridge: 'retry' });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  const submit = page.getByRole('button', {
    name: '提交理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(
    page.getByRole('button', { name: '重试递送这份原话', exact: true }),
  ).toBeVisible();
  await page.getByRole('link', { name: '补充我的理解', exact: true }).click();
  await page
    .getByRole('textbox', { name: '我的理解', exact: true })
    .fill('后来保存的理解，不能覆盖此前提交');
  await page.getByRole('button', { name: '保存我的理解', exact: true }).click();
  await expect(
    page.getByText('本人原话已保存到本机学习记录，未自动回填或公开。'),
  ).toBeVisible();
  await page.getByRole('link', { name: '查看成果回填', exact: true }).click();
  await expect(
    page.getByText('本次候选对应此前提交的原话，不会覆盖当前已保存的理解。', {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: '重试递送这份原话', exact: true })
    .click();
  await expect(
    page.getByText('已受理，等待生成或读取候选；尚未确认正式写入。', {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: '重试递送这份原话', exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText('后来保存的理解，不能覆盖此前提交', { exact: true }),
  ).toBeVisible();
  expect(calls.submissionBodies).toHaveLength(2);
  expect(calls.submissionBodies[1]).toEqual(calls.submissionBodies[0]);
  expect(calls.submissionBodies[0]).toEqual({
    card_version: 1,
    note_sha256: createHash('sha256')
      .update('本人已保存的原话', 'utf8')
      .digest('hex'),
  });
  expect(calls.saves()).toBe(1);
});

test('理解提交迟到响应不能覆盖后来打开的另一张卡', async ({ page }) => {
  const calls = await serve(page, { bridge: 'delayed', secondCard: true });
  await page.goto('/#/growth?view=writeback&card=' + cardId);
  const submit = page.getByRole('button', {
    name: '提交理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => calls.submissionBodies.length).toBe(1);
  await page
    .locator('#growth-navigation')
    .getByRole('link', { name: '学习库', exact: true })
    .click();
  await page
    .getByRole('link', { name: '列表排在前面的另一张卡', exact: true })
    .click();
  await page.getByRole('link', { name: '查看成果回填', exact: true }).click();
  await expect(page.getByText('另一张卡的原话', { exact: true })).toBeVisible();
  await expect(submit).toBeEnabled();
  const response = page.waitForResponse(
    (value) =>
      value.url().endsWith('/cards/' + cardId + '/understanding/submissions') &&
      value.request().method() === 'POST',
  );
  calls.releaseSubmission();
  const delivered = await response;
  await delivered.finished();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
  );
  await expect(page).toHaveURL(
    new RegExp('view=writeback.*card=' + otherCardId),
  );
  await expect(page.getByText('另一张卡的原话', { exact: true })).toBeVisible();
  await expect(
    page.getByText('submission_id: ' + submissionId, { exact: false }),
  ).toHaveCount(0);
  await expect(submit).toBeEnabled();
  expect(calls.submissionBodies).toHaveLength(1);
});

test('正式历史版本只读预览并显式关联既有卡，原话保持独立', async ({ page }) => {
  const calls = await serve(page);
  await page.goto(entryUrl);
  await expect(page.getByLabel('正式版本只读正文')).toHaveText(
    formalEntry.body,
  );
  expect(calls.contextBodies).toEqual([]);
  const attach = page.getByRole('button', {
    name: '确认关联此正式版本',
    exact: true,
  });
  await expect(attach).toBeDisabled();
  await page
    .getByRole('combobox', { name: '关联到已有学习卡', exact: true })
    .selectOption(cardId);
  await expect(attach).toBeDisabled();
  await page
    .getByRole('checkbox', {
      name: '我已核对这个正式版本与目标学习卡，确认只添加引用',
      exact: true,
    })
    .check();
  await attach.dblclick();
  await expect(
    page.getByText(
      '指定正式版本已关联。未创建新卡、未提交模型、未替换你的理解。',
      { exact: true },
    ),
  ).toBeVisible();
  expect(calls.contextBodies).toHaveLength(1);
  await page.getByRole('link', { name: '回到这张学习卡', exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: '我的理解', exact: true }),
  ).toHaveValue('本人已保存的原话');
  const refs = page.getByRole('region', {
    name: '关联的正式知识版本',
    exact: true,
  });
  await refs
    .getByText('指定的正式历史版本 · 正式版本 v2', { exact: true })
    .click();
  await expect(page.getByLabel('关联的正式正文 v2')).toHaveText(
    formalEntry.body,
  );
  await page.reload();
  await expect(
    refs.getByText('指定的正式历史版本 · 正式版本 v2', { exact: true }),
  ).toBeVisible();
  expect(calls.contextBodies).toHaveLength(1);
  calls.revokeContext();
  await refs
    .getByRole('button', { name: '重新验证正式引用', exact: true })
    .click();
  await expect(refs.getByRole('alert')).toContainText('不显示缓存正文');
  await expect(page.getByLabel('关联的正式正文 v2')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(
    'private-entry-secret-sentinel',
  );
  await expect(
    page.getByRole('textbox', { name: '我的理解', exact: true }),
  ).toHaveValue('本人已保存的原话');
  expect(calls.saves()).toBe(0);
  expect(
    calls.requests.some(
      (path) =>
        path.endsWith('/chat/stream') ||
        path.endsWith('/analysis') ||
        path.endsWith('/complete'),
    ),
  ).toBe(false);
});
test('正式版本没有既有卡时不自动建卡，也不提供假完成', async ({ page }) => {
  const calls = await serve(page, { entryContext: 'empty' });
  await page.goto(entryUrl);
  await expect(page.getByLabel('正式版本只读正文')).toHaveText(
    formalEntry.body,
  );
  await expect(
    page.getByText('暂无可关联的既有学习卡。', { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: '确认关联此正式版本', exact: true }),
  ).toHaveCount(0);
  expect(calls.contextBodies).toEqual([]);
  expect(calls.saves()).toBe(0);
});
test('正式引用卡版本冲突后必须重新预览与确认，不能复用旧批准', async ({
  page,
}) => {
  const calls = await serve(page, { entryContext: 'conflict' });
  await page.goto(entryUrl);
  const choose = page.getByRole('combobox', {
    name: '关联到已有学习卡',
    exact: true,
  });
  await choose.selectOption(cardId);
  const approval = page.getByRole('checkbox', {
    name: '我已核对这个正式版本与目标学习卡，确认只添加引用',
    exact: true,
  });
  await approval.check();
  await page
    .getByRole('button', { name: '确认关联此正式版本', exact: true })
    .click();
  await expect(page.getByRole('alert')).toContainText('不会自动重试');
  await expect(page.getByLabel('正式版本只读正文')).toHaveCount(0);
  expect(calls.contextBodies).toHaveLength(1);
  await page
    .getByRole('button', { name: '重新读取精确版本与关联', exact: true })
    .click();
  await expect(choose).toContainText('v2');
  await expect(approval).not.toBeChecked();
  await expect(
    page.getByRole('button', { name: '确认关联此正式版本', exact: true }),
  ).toBeDisabled();
  await approval.check();
  await page
    .getByRole('button', { name: '确认关联此正式版本', exact: true })
    .click();
  await expect(
    page.getByText(
      '指定正式版本已关联。未创建新卡、未提交模型、未替换你的理解。',
      { exact: true },
    ),
  ).toBeVisible();
  expect(calls.contextBodies.map((body) => body.expected_card_version)).toEqual(
    [1, 2],
  );
  expect(calls.contextBodies[0].request_id).not.toBe(
    calls.contextBodies[1].request_id,
  );
});
test('正式引用关联响应丢失后只读恢复，不自动重复关联', async ({ page }) => {
  const calls = await serve(page, { entryContext: 'lost' });
  await page.goto(entryUrl);
  await page
    .getByRole('combobox', { name: '关联到已有学习卡', exact: true })
    .selectOption(cardId);
  await page
    .getByRole('checkbox', {
      name: '我已核对这个正式版本与目标学习卡，确认只添加引用',
      exact: true,
    })
    .check();
  await page
    .getByRole('button', { name: '确认关联此正式版本', exact: true })
    .click();
  await expect(page.getByRole('alert')).toContainText('未能确认');
  await page
    .getByRole('button', { name: '重新读取精确版本与关联', exact: true })
    .click();
  await expect(
    page.getByText(
      '指定正式版本已关联。未创建新卡、未提交模型、未替换你的理解。',
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: '确认关联此正式版本', exact: true }),
  ).toBeDisabled();
  expect(calls.contextBodies).toHaveLength(1);
});
test('正式版本拒绝访问不展示正文，也不把错误详情带到页面', async ({ page }) => {
  const calls = await serve(page, { entryContext: 'denied' });
  await page.goto(entryUrl);
  await expect(page.getByRole('alert')).toContainText('不显示缓存正文');
  await expect(page.getByLabel('正式版本只读正文')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(
    'private-entry-secret-sentinel',
  );
  expect(calls.contextBodies).toEqual([]);
});
test('条目深链缺少明确版本时不请求 latest 或关联', async ({ page }) => {
  const calls = await serve(page);
  await page.goto(
    '/#/growth?view=entry-context&entry_id=' + formalEntry.entry_id,
  );
  await expect(page.getByRole('alert')).toContainText(
    '不会猜测条目或读取最新版',
  );
  expect(calls.requests.some((path) => path.includes('/entry-context'))).toBe(
    false,
  );
  expect(calls.contextBodies).toEqual([]);
});

test('无工程卡也可明确开始自由学习，原话到独立候选并从学习库恢复', async ({
  page,
}) => {
  const calls = await serve(page, {
    empty: true,
    entryContext: 'empty',
    wiki: 'candidate',
  });
  await page.goto(entryUrl);
  const start = page.getByRole('button', {
    name: '从当前正式版本开始自由学习',
    exact: true,
  });
  await expect(start).toBeEnabled();
  expect(calls.wikiCreates).toEqual([]);
  await start.dblclick();
  await expect(page).toHaveURL(
    new RegExp('view=wiki-learning&context_id=' + wikiId),
  );
  expect(calls.wikiCreates).toHaveLength(1);
  await expect(page.getByLabel('自由学习正式原文')).toHaveText(
    formalEntry.body,
  );
  const answer = page.getByRole('textbox', {
    name: '自由学习本人原话',
    exact: true,
  });
  await expect(answer).toHaveValue('');
  const note = '  我理解的失败边界\n不是复制来源正文  ';
  await answer.fill(note);
  await page
    .getByRole('button', { name: '保存自由学习原话', exact: true })
    .dblclick();
  await expect(
    page.getByText('本人原话已保存，尚未提交生成、复核或公开。', {
      exact: true,
    }),
  ).toBeVisible();
  expect(calls.wikiNotes).toHaveLength(1);
  expect(calls.wikiSubmits).toEqual([]);
  const submit = page.getByRole('button', {
    name: '提交自由理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.dblclick();
  await expect(
    page.getByText('已保存到本机待发，尚未确认服务受理。', { exact: true }),
  ).toBeVisible();
  expect(calls.wikiSubmits).toEqual([
    { context_version: 2, note_sha256: hashText(note) },
  ]);
  await page
    .getByRole('button', { name: '刷新自由理解提交', exact: true })
    .click();
  await expect(page.getByLabel('自由理解候选差异')).toContainText(
    '从本人原话整理的独立解释',
  );
  await expect(
    page.getByText('候选等待独立复核，尚未确认正式写入。', { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel('自由学习正式原文')).toHaveText(
    formalEntry.body,
  );
  await expect(answer).toHaveValue(note);
  expect(calls.contextBodies).toEqual([]);
  expect(calls.saves()).toBe(0);
  calls.confirmWrite();
  await page
    .getByRole('button', { name: '刷新自由理解提交', exact: true })
    .click();
  await expect(
    page.getByText('已确认正式写入，不代表公开。', { exact: true }),
  ).toBeVisible();
  await page.getByRole('link', { name: '返回学习库' }).click();
  await page
    .getByRole('region', { name: '来自正式知识的自由学习', exact: true })
    .getByRole('link', { name: '继续自由学习', exact: true })
    .click();
  await expect(answer).toHaveValue(note);
  await page.reload();
  await expect(answer).toHaveValue(note);
  await expect(submit).toBeDisabled();
  expect(calls.wikiCreates).toHaveLength(1);
  expect(calls.wikiSubmits).toHaveLength(1);
  expect(
    calls.requests.some(
      (path) =>
        path.includes('/cards/') ||
        path.endsWith('/complete') ||
        path.endsWith('/chat/stream'),
    ),
  ).toBe(false);
});

test('自由学习创建响应丢失后只读查回同一来源，不自动重建', async ({ page }) => {
  const calls = await serve(page, {
    entryContext: 'empty',
    wiki: 'create-lost',
  });
  await page.goto(entryUrl);
  await page
    .getByRole('button', { name: '从当前正式版本开始自由学习', exact: true })
    .click();
  await expect(page.getByRole('alert')).toBeVisible();
  expect(calls.wikiCreates).toHaveLength(1);
  await page
    .getByRole('button', { name: '重新读取精确版本与关联', exact: true })
    .click();
  await expect(
    page.getByText('已通过只读恢复找到这份自由学习记录。', { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', {
      name: '从当前正式版本开始自由学习',
      exact: true,
    }),
  ).toBeDisabled();
  await page.getByRole('link', { name: '继续自由学习', exact: true }).click();
  await expect(page.getByLabel('自由学习正式原文')).toHaveText(
    formalEntry.body,
  );
  expect(calls.wikiCreates).toHaveLength(1);
  expect(calls.wikiNotes).toEqual([]);
  expect(calls.wikiSubmits).toEqual([]);
});

test('自由原话 CAS 冲突保留输入，重新读取并确认后才使用新版本', async ({
  page,
}) => {
  const calls = await serve(page, {
    wikiExisting: true,
    wiki: 'note-conflict',
  });
  await page.goto(wikiUrl);
  const answer = page.getByRole('textbox', {
    name: '自由学习本人原话',
    exact: true,
  });
  await expect(answer).toHaveValue('独立自由理解旧稿');
  await answer.fill('冲突时仍属于本人的新输入');
  const save = page.getByRole('button', {
    name: '保存自由学习原话',
    exact: true,
  });
  await save.click();
  await expect(page.getByRole('alert')).toContainText('原话输入保留');
  await expect(answer).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(
    '冲突时仍属于本人的新输入',
  );
  await page.reload();
  await expect(answer).toHaveValue('冲突时仍属于本人的新输入');
  await expect(save).toBeDisabled();
  expect(calls.wikiNotes).toHaveLength(1);
  await page.getByText('服务已保存的本人原话 · v2', { exact: true }).click();
  await expect(
    page.getByText('另一端保存的本人原话', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('checkbox', {
      name: '已核对服务端原话，以当前版本保存我的输入',
      exact: true,
    })
    .check();
  await save.click();
  await expect(
    page.getByText('本人原话已保存，尚未提交生成、复核或公开。', {
      exact: true,
    }),
  ).toBeVisible();
  expect(calls.wikiNotes).toHaveLength(2);
  expect(calls.wikiNotes[1]).toMatchObject({
    expected_context_version: 2,
    note_text: '冲突时仍属于本人的新输入',
  });
  expect(calls.wikiSubmits).toEqual([]);
});

test('自由原话保存失联后 GET 核对已保存版本，不能自动重放', async ({
  page,
}) => {
  const calls = await serve(page, { wikiExisting: true, wiki: 'note-lost' });
  await page.goto(wikiUrl);
  const answer = page.getByRole('textbox', {
    name: '自由学习本人原话',
    exact: true,
  });
  await expect(answer).toHaveValue('独立自由理解旧稿');
  await answer.fill('已持久化但回执丢失的本人解释');
  await page
    .getByRole('button', { name: '保存自由学习原话', exact: true })
    .click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(answer).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(
    '已持久化但回执丢失的本人解释',
  );
  await page
    .getByRole('button', { name: '重新读取自由学习', exact: true })
    .click();
  await expect(
    page.getByText('已重新核对服务保存的原话。', { exact: true }),
  ).toBeVisible();
  await expect(answer).toHaveValue('已持久化但回执丢失的本人解释');
  await expect(
    page.getByRole('button', { name: '保存自由学习原话', exact: true }),
  ).toBeDisabled();
  expect(calls.wikiNotes).toHaveLength(1);
  expect(calls.wikiSubmits).toEqual([]);
});

test('自由理解提交失联只 GET 恢复不可变快照，不借用工程卡', async ({
  page,
}) => {
  const calls = await serve(page, {
    wikiExisting: true,
    wiki: 'submission-lost',
  });
  await page.goto(wikiUrl);
  const submit = page.getByRole('button', {
    name: '提交自由理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(page.getByRole('alert')).toContainText('原话输入保留');
  await expect(submit).toHaveCount(0);
  await page
    .getByRole('button', { name: '重新读取自由学习', exact: true })
    .click();
  await expect(
    page.getByText('已保存到本机待发，尚未确认服务受理。', { exact: true }),
  ).toBeVisible();
  await expect(submit).toBeDisabled();
  expect(calls.wikiSubmits).toEqual([
    { context_version: 1, note_sha256: hashText('独立自由理解旧稿') },
  ]);
  expect(calls.submissionBodies).toEqual([]);
  expect(calls.wikiNotes).toEqual([]);
});

test('自由理解重递旧版本快照，不被后来保存的解释替换', async ({ page }) => {
  const calls = await serve(page, { wikiExisting: true, wiki: 'retry' });
  await page.goto(wikiUrl);
  const submit = page.getByRole('button', {
    name: '提交自由理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(
    page.getByRole('button', { name: '重试递送这份自由理解', exact: true }),
  ).toBeVisible();
  const answer = page.getByRole('textbox', {
    name: '自由学习本人原话',
    exact: true,
  });
  await answer.fill('后来新增的独立解释');
  await page
    .getByRole('button', { name: '保存自由学习原话', exact: true })
    .click();
  await expect(
    page.getByText('本人原话已保存，尚未提交生成、复核或公开。', {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: '重试递送这份自由理解', exact: true })
    .click();
  await expect(
    page.getByText('已受理，等待生成或读取候选；尚未确认正式写入。', {
      exact: true,
    }),
  ).toBeVisible();
  expect(calls.wikiSubmits).toHaveLength(2);
  expect(calls.wikiSubmits[1]).toEqual(calls.wikiSubmits[0]);
  await expect(answer).toHaveValue('后来新增的独立解释');
  await expect(
    page.getByText('这份提交保留此前原话，不会被当前编辑替换。', {
      exact: true,
    }),
  ).toBeVisible();
});

test('自由学习来源撤权不显示缓存正文或本人原话，仍保留草稿待重新授权', async ({
  page,
}) => {
  const calls = await serve(page, { wikiExisting: true });
  await page.goto(wikiUrl);
  await expect(page.getByLabel('自由学习正式原文')).toHaveText(
    formalEntry.body,
  );
  const answer = page.getByRole('textbox', {
    name: '自由学习本人原话',
    exact: true,
  });
  await answer.fill('撤权后仍应保留的本人输入');
  calls.revokeContext();
  await page
    .getByRole('button', { name: '重新读取自由学习', exact: true })
    .click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('自由学习正式原文')).toHaveCount(0);
  await expect(answer).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(
    '撤权后仍应保留的本人输入',
  );
  await expect(page.locator('body')).not.toContainText('独立自由理解旧稿');
  await expect(page.locator('body')).not.toContainText(
    'wiki-source-secret-sentinel',
  );
  await expect(
    page.getByRole('region', { name: '自由理解提交与候选', exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      (key) => JSON.parse(localStorage.getItem(key)!).text,
      'zhizhi:wiki-understanding:' + wikiId,
    ),
  ).toBe('撤权后仍应保留的本人输入');
  expect(calls.wikiNotes).toEqual([]);
});

test('自由理解迟到提交不覆盖导航后的其他学习页', async ({ page }) => {
  const calls = await serve(page, { wikiExisting: true, wiki: 'delayed' });
  await page.goto(wikiUrl);
  const submit = page.getByRole('button', {
    name: '提交自由理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect.poll(() => calls.wikiSubmits.length).toBe(1);
  await page.getByRole('link', { name: '返回学习库' }).click();
  await page.getByRole('link', { name: '事务边界学习', exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: '我的理解', exact: true }),
  ).toHaveValue('本人已保存的原话');
  const response = page.waitForResponse(
    (value) =>
      value
        .url()
        .endsWith(
          '/entry-learning-contexts/' + wikiId + '/understanding/submissions',
        ) && value.request().method() === 'POST',
  );
  calls.wikiRelease();
  await (await response).finished();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
  );
  await expect(page).toHaveURL(new RegExp('view=session.*card=' + cardId));
  await expect(
    page.getByRole('textbox', { name: '我的理解', exact: true }),
  ).toHaveValue('本人已保存的原话');
  await expect(
    page.getByRole('region', { name: '自由理解提交与候选', exact: true }),
  ).toHaveCount(0);
  expect(calls.wikiSubmits).toHaveLength(1);
  expect(calls.saves()).toBe(0);
});

test('未授权打开自由学习时不把本机缓存原话放进 DOM', async ({ page }) => {
  const calls = await serve(page, { wikiExisting: true, wiki: 'denied' });
  await page.addInitScript(
    ({ key, text }) =>
      localStorage.setItem(key, JSON.stringify({ text, version: 1 })),
    {
      key: 'zhizhi:wiki-understanding:' + wikiId,
      text: '缓存中尚未授权显示的本人私密原话',
    },
  );
  await page.goto(wikiUrl);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: '自由学习本人原话', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel('自由学习正式原文')).toHaveCount(0);
  await expect(
    page.getByRole('region', { name: '自由理解提交与候选', exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(
    '缓存中尚未授权显示的本人私密原话',
  );
  expect(calls.wikiNotes).toEqual([]);
  expect(calls.wikiSubmits).toEqual([]);
});

test('候选刷新失去授权时连同缓存原话一起撤出 DOM', async ({ page }) => {
  const calls = await serve(page, { wikiExisting: true, wiki: 'candidate' });
  await page.goto(wikiUrl);
  const submit = page.getByRole('button', {
    name: '提交自由理解，生成并复核候选',
    exact: true,
  });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(
    page.getByText('已保存到本机待发，尚未确认服务受理。', { exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: '刷新自由理解提交', exact: true })
    .click();
  await expect(page.getByLabel('自由理解候选差异')).toBeVisible();
  calls.revokeContext();
  await page
    .getByRole('button', { name: '刷新自由理解提交', exact: true })
    .click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: '自由学习本人原话', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel('自由学习正式原文')).toHaveCount(0);
  await expect(page.getByLabel('自由理解候选差异')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('独立自由理解旧稿');
  await expect(page.locator('body')).not.toContainText(
    '从本人原话整理的独立解释',
  );
  expect(calls.wikiSubmits).toHaveLength(1);
});

// Isolated visual evidence only: no real user knowledge or model calls.
test('六页设计对照截图：隔离 fixture，不代表真实模型端到端', async ({
  page,
}) => {
  await serve(page, { writeback: true });
  const folder = process.env.ZHIZHI_REVIEW_SCREENSHOTS;
  test.skip(
    !folder,
    'Only generate local screenshots for an explicit design review.',
  );
  await page.setViewportSize({ width: 1586, height: 992 });
  for (const [name, query] of [
    ['P01', 'home'],
    ['P06', 'library'],
    ['P07', 'session&card=' + cardId],
    ['P08', 'writeback&card=' + cardId],
    ['P10', 'radar'],
    ['P12', 'projects&project=p1'],
  ]) {
    await page.goto('/#/growth?view=' + query);
    await expect(
      page.getByText('本地服务已响应', { exact: true }),
    ).toBeVisible();
    if (name === 'P07')
      await expect(
        page.getByRole('textbox', { name: '我的理解', exact: true }),
      ).toBeVisible();
    if (name === 'P08')
      await expect(page.getByLabel('正式知识与候选差异')).toBeVisible();
    if (name === 'P12')
      await expect(
        page.getByRole('heading', { name: '选择具体提交' }),
      ).toBeVisible();
    await page.screenshot({
      path: folder + '/' + name + '.png',
      fullPage: true,
    });
  }
});

test('关系图只读筛选和版本入口，不将候选冒充已确认', async ({ page }) => {
  await serve(page);
  const paths: string[] = [];
  await page.route(
    '**/api/growth/local/knowledge/relations/root/traverse?*',
    (route) => {
      expect(route.request().method()).toBe('GET');
      paths.push(route.request().url());
      const kind = new URL(route.request().url()).searchParams.getAll('kind');
      return route.fulfill({
        json: {
          data: {
            nodes: [
              { entry_id: 'root', revision: 2, title: '根事务条目' },
              { entry_id: 'target', revision: 3, title: '候选边界条目' },
            ],
            edges: kind.includes('candidate')
              ? [
                  {
                    edge_id: 'e1',
                    source_entry_id: 'root',
                    target_entry_id: 'target',
                    source_revision: 2,
                    target_revision: 3,
                    kind: 'candidate',
                    is_fact: false,
                    statement: '仍需核对的架构联系',
                    verification: 'pending_verification',
                  },
                ]
              : [],
            root_entry_id: 'root',
            projection_revision: 'fixture-projection',
            visibility: 'all',
            inference: 'none',
            truncated: false,
          },
        },
      });
    },
  );
  await page.goto('/#/growth?view=radar');
  await expect(
    page.getByText('先选择一个正式条目。', { exact: false }),
  ).toBeVisible();
  await page.getByRole('textbox', { name: '关系根条目' }).fill('root');
  await page.getByRole('button', { name: '查看关系', exact: true }).click();
  await expect(
    page.getByText('仍需核对的架构联系', { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: '核对精确正式版本' }),
  ).toHaveAttribute(
    'href',
    '#/growth?view=entry-context&entry_id=root&revision=2',
  );
  await page.getByRole('checkbox', { name: 'AI 候选', exact: true }).uncheck();
  await expect(
    page.getByText('当前范围暂无关系。这不代表你的知识薄弱。'),
  ).toBeVisible();
  expect(paths).toHaveLength(2);
  expect(
    paths.every(
      (value) => new URL(value).searchParams.get('max_nodes') === '40',
    ),
  ).toBe(true);
});

test('学习库视图恢复、会话独立导航和搜索空态', async ({ page }) => {
  await serve(page);
  await page.goto('/#/growth?view=library');
  await page.getByRole('button', { name: '待回看', exact: true }).click();
  await expect(page).toHaveURL(/library_tab=revisit/);
  await expect(page.getByText('暂无你标记的待回看问题')).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('button', { name: '待回看', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: '我的计划', exact: true }).click();
  await page
    .getByRole('textbox', { name: '搜索学习材料' })
    .fill('没有匹配的关键词');
  // Do not wait between the tab click and fill: consecutive events must merge.
  await expect(page).toHaveURL(/library_tab=plan/);
  await expect(
    page.getByRole('button', { name: '我的计划', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('还没有符合条件的材料')).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole('textbox', { name: '搜索学习材料' }),
  ).toHaveValue('没有匹配的关键词');
  await expect(
    page.getByRole('button', { name: '我的计划', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('还没有符合条件的材料')).toBeVisible();
  await page
    .locator('#growth-navigation')
    .getByRole('link', { name: '学习会话', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: '选择要继续的学习会话' }),
  ).toBeVisible();
});

test('学习库新建入口进入真实工程来源并可返回，不自动生成或提交', async ({ page }) => {
  const calls = await serve(page);
  await page.goto('/#/growth?view=library&project=p1&library_tab=revisit');
  await page.getByRole('link', { name: '新建学习任务', exact: true }).click();
  await expect(page).toHaveURL(/view=projects&project=p1/);
  await expect(page.getByRole('heading', { name: '工程项目与提交证据' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/view=library&project=p1&library_tab=revisit/);
  await expect(page.getByRole('link', { name: '新建学习任务', exact: true })).toBeVisible();
  expect(calls.submissionBodies).toHaveLength(0);
});
