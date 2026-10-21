// Installed S04: event snapshot -> live model -> linked discussion -> card -> Wiki.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, setupPath, eventPath, vault, outputPath] = process.argv.slice(2);
if (![executable, workspace, setupPath, eventPath, vault, outputPath].every(Boolean)) {
  throw new Error('用法：node m7_s04_knowledge_first_ui.mjs <exe> <repo> <setup> <event> <vault> <output>');
}
const setup = JSON.parse(await readFile(setupPath, 'utf8'));
const event = JSON.parse(await readFile(eventPath, 'utf8'));
const projectId = setup.project_id;
const snapshotId = event.snapshot_id;
const evidence = { status: 'running', project_id: projectId, snapshot_id: snapshotId,
  session_id: setup.explore_session_id, topic_id: setup.explore_topic_id,
  commit_sha: setup.commit_sha, checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
await mkdir(path.join(vault, '.obsidian'), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
let page;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-s04-knowledge')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token, Origin: origin };
  const local = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`S04 本机状态 HTTP ${response.status()}`);
    return response.json();
  };
  const index = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge`, { headers });
    if (response.status() !== 200) throw new Error(`S04 知识索引 HTTP ${response.status()}`);
    return response.json();
  };
  const state = await local();
  const snapshot = state.snapshots.find(item => item.snapshot_id === snapshotId);
  evidence.checks.event_snapshot_matches_commit = snapshot?.head_sha === setup.commit_sha &&
    snapshot?.feature_id === setup.feature_id;
  if (!evidence.checks.event_snapshot_matches_commit) throw new Error('S04 事件快照和 Git 提交不一致');
  await page.goto(`${origin}/#/growth?view=all&project=${projectId}`);
  await page.locator('#growth-snapshot-select').selectOption(snapshotId);
  const analysis = page.locator('#growth-analysis-title').locator('..');
  await analysis.locator('#growth-provider-select').selectOption('deepseek');
  for (const source of ['src/document.ts', 'src/access.ts']) {
    const selected = analysis.locator('.growth-page__untracked label').filter({ hasText: source });
    await selected.locator('input').check({ timeout: 30_000 });
  }
  await analysis.getByRole('button', { name: '预览模型请求' }).click();
  const payload = await analysis.locator('.growth-page__payload').innerText();
  evidence.checks.authorization_sources_approved = payload.includes('ownerId') &&
    payload.includes('canEdit') && payload.includes('FORBIDDEN') &&
    payload.includes(snapshotId);
  if (!evidence.checks.authorization_sources_approved) {
    throw new Error('S04 批准范围缺少真实权限源码和快照');
  }
  let completedAnalysis;
  evidence.analysis_attempts = [];
  for (let retry = 0; retry < 3; retry += 1) {
    if (!await analysis.locator('.growth-page__approval input').isChecked()) {
      await analysis.locator('.growth-page__approval input').check();
    }
    const submitted = page.waitForResponse(response => new URL(response.url()).pathname ===
      `/api/growth/local/snapshots/${snapshotId}/analysis`, { timeout: 30_000 });
    await analysis.getByRole('button', { name: '发送并生成学习机会' }).click();
    const response = await submitted;
    if (![200, 202].includes(response.status())) {
      throw new Error(`S04 授权分析提交 HTTP ${response.status()}`);
    }
    const run = await response.json();
    const until = Date.now() + 180_000;
    while (Date.now() < until) {
      completedAnalysis = (await local()).analysis_runs.find(item => item.analysis_id === run.analysis_id);
      if (['succeeded', 'failed'].includes(completedAnalysis?.status)) break;
      await page.waitForTimeout(1000);
    }
    evidence.analysis_attempts.push({ analysis_id: run.analysis_id,
      status: completedAnalysis?.status, error: completedAnalysis?.error });
    await save();
    if (completedAnalysis?.status === 'succeeded') break;
    if (completedAnalysis?.status !== 'failed' ||
        !String(completedAnalysis.error).includes('failed to publish run artifacts')) {
      throw new Error(`S04 授权分析失败：${completedAnalysis?.error ?? 'timeout'}`);
    }
  }
  if (completedAnalysis?.status !== 'succeeded') {
    throw new Error('S04 授权分析多次发生上游产物发布故障');
  }
  const produced = (await local()).opportunities.filter(item =>
    item.analysis_id === completedAnalysis.analysis_id).map(item => item.opportunity_id);
  const deadline = Date.now() + 30_000;
  let candidate;
  while (Date.now() < deadline) {
    candidate = (await index()).candidates.find(item =>
      item.opportunity_ids?.some(id => produced.includes(id)) &&
      item.project_ids?.includes(projectId) &&
      /权限|授权|owner|文档|认证|canEdit/i.test(`${item.title} ${item.learning_goal ?? ''}`));
    if (candidate) break;
    await page.waitForTimeout(1000);
  }
  if (!candidate) throw new Error('S04 真实 DeepSeek 未生成文档授权候选');
  evidence.candidate_id = candidate.candidate_id;
  evidence.checks.live_authorization_candidate = true;
  const linked = await page.request.post(
    `${origin}/api/growth/local/knowledge/candidates/${candidate.candidate_id}/discussion`,
    { headers, data: { session_id: setup.explore_session_id } });
  if (linked.status() !== 200) {
    throw new Error(`S04 代码候选与探讨关联失败：${linked.status()} ${await linked.text()}`);
  }
  candidate = await linked.json();
  evidence.checks.discussion_linked = candidate.discussions?.some(item =>
    item.session_id === setup.explore_session_id && item.topic_id === setup.explore_topic_id);
  if (!evidence.checks.discussion_linked) throw new Error('S04 候选缺少先前确认的探讨主题');
  await page.goto(`${origin}/#/growth?view=learning&project=${projectId}`);
  const row = page.locator('.knowledge-candidate').filter({ hasText: candidate.title });
  await row.getByRole('button', { name: candidate.suggested_card_id ? '确认归并' : '确认成卡' }).click();
  await page.waitForURL(/card=/, { timeout: 30_000 });
  const cardId = new URL(page.url()).hash.match(/[?&]card=([^&]+)/)?.[1];
  if (!cardId) throw new Error('S04 代码候选未确认成卡');
  evidence.card_id = cardId;
  let detail = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
  if (detail.status() !== 200) throw new Error('S04 正式卡详情不可读取');
  let card = await detail.json();
  evidence.checks.same_topic_as_exploration = card.card.topic_id === setup.explore_topic_id;
  evidence.checks.same_git_evidence = card.sources?.some(source =>
    source.commit_sha === setup.commit_sha) && card.sources?.some(source =>
    /document\.ts|access\.ts/.test(source.relative_path ?? ''));
  if (!evidence.checks.same_topic_as_exploration || !evidence.checks.same_git_evidence) {
    throw new Error(`S04 卡片主题或 Git 证据断裂：${JSON.stringify({topic: card.card.topic_id, sources: card.sources})}`);
  }
  await page.goto(`${origin}/#/growth?view=topics&project=${projectId}`);
  await page.getByRole('textbox', { name: '探讨问题' }).fill(
    '请根据刚刚获准的代码上下文，明确写出 src/access.ts 和 src/document.ts 两个文件路径，再解释 Bob 为什么不能编辑 Alice 的文档。');
  await page.getByRole('button', { name: '预览并发送探讨' }).click();
  await page.getByText('确认发送范围').waitFor({ timeout: 30_000 });
  await page.locator('label').filter({ hasText: '我确认发送这些内容' }).locator('input').check();
  const citedReply = page.waitForResponse(response => new URL(response.url()).pathname ===
    `/api/growth/local/projects/${projectId}/explore`, { timeout: 180_000 });
  await page.getByRole('button', { name: '发送并继续对话' }).click();
  const citedResponse = await citedReply;
  if (citedResponse.status() !== 200) throw new Error(`S04 证据探讨 HTTP ${citedResponse.status()}`);
  const citedBody = await citedResponse.json();
  const citedMessage = citedBody.messages.at(-1);
  evidence.cited_exploration = { session_id: citedBody.session_id,
    source_refs: citedMessage?.source_refs, content_text: citedMessage?.content_text };
  evidence.checks.explore_refs_are_approved_commit = citedMessage?.source_refs?.some(source =>
    source.project_id === projectId && source.commit_sha === setup.commit_sha &&
    ['src/access.ts', 'src/document.ts'].includes(source.relative_path)) === true;
  if (!evidence.checks.explore_refs_are_approved_commit) {
    throw new Error('S04 探讨回复未附带本次批准提交的源码引用');
  }
  await page.goto(`${origin}/#/growth?view=cards&card=${cardId}&project=${projectId}`);
  await page.getByRole('button', { name: '预览材料生成范围' }).waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: '预览材料生成范围' }).click();
  const material = page.locator('.knowledge-material-setup');
  await material.getByRole('button', { name: '确认并生成知识讲解与两题' }).waitFor({ timeout: 30_000 });
  evidence.checks.real_material_model = (await material.innerText()).includes('api.deepseek.com');
  const generated = page.waitForResponse(response => new URL(response.url()).pathname ===
    `/api/growth/local/knowledge/cards/${cardId}/materials`, { timeout: 180_000 });
  await material.getByRole('button', { name: '确认并生成知识讲解与两题' }).click();
  if ((await generated).status() !== 200) throw new Error('S04 材料生成失败');
  detail = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
  card = await detail.json();
  const lesson = `${card.card.material?.back_summary ?? ''} ${card.card.material?.back_mechanism ?? ''} ${card.card.material?.back_boundary ?? ''}`;
  const prediction = card.questions?.find(question => question.kind === 'prediction');
  const predictedCase = `${prediction?.stem ?? ''} ${prediction?.scenario ?? ''}`;
  evidence.checks.authentication_vs_authorization_lesson = /身份|认证/.test(lesson) &&
    /授权|权限|所有者/.test(lesson);
  evidence.checks.bob_alice_prediction = /Bob/i.test(predictedCase) &&
    /Alice/i.test(predictedCase) && /ownerId|所有者|归属/i.test(predictedCase);
  if (!evidence.checks.authentication_vs_authorization_lesson ||
      !evidence.checks.bob_alice_prediction) {
    throw new Error(`S04 真实题包缺少认证/授权讲解或明确 Bob/Alice 归属条件：${predictedCase}`);
  }
  const askAiButton = page.getByRole('button', { name: '问 AI', exact: true });
  const aiInDrawer = await askAiButton.isVisible();
  if (aiInDrawer) await askAiButton.click();
  await page.getByRole('textbox', { name: '向 AI 提问' }).fill(
    '在这个提交里，为什么 Bob 已登录仍不能修改 Alice 的文档？请指出 canEdit 的依据和 JWT 边界。');
  await page.locator('.knowledge-ai-compose').getByRole('button', { name: '发送' }).click();
  const chatDeadline = Date.now() + 120_000;
  do {
    detail = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
    card = await detail.json();
    if (card.conversation?.some(item => item.reply_text)) break;
    await page.waitForTimeout(1000);
  } while (Date.now() < chatDeadline);
  evidence.checks.contextual_ai_chat = card.conversation?.some(item =>
    /Bob|Alice|所有者|ownerId|canEdit/.test(item.reply_text ?? '') &&
    item.context?.stage === 'front' && item.context?.evidence_refs?.length > 0);
  if (aiInDrawer) {
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: 'AI 速问' }).waitFor({ state: 'hidden' });
  }
  await page.getByRole('button', { name: '翻面看讲解' }).click();
  await page.getByRole('button', { name: '进入理解检查' }).click();
  await page.getByRole('radio').first().check();
  await page.getByRole('button', { name: '提交答案' }).click();
  await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).waitFor({ timeout: 30_000 });
  evidence.checks.understanding_four_explanations = await page.locator('.knowledge-explanation').count() === 4;
  await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).click();
  await page.getByRole('radio').first().check();
  await page.getByRole('button', { name: '提交答案' }).click();
  await page.getByRole('button', { name: '我已读完解析，完成学习' }).waitFor({ timeout: 30_000 });
  evidence.checks.prediction_four_explanations = await page.locator('.knowledge-explanation').count() === 4;
  await page.getByRole('button', { name: '我已读完解析，完成学习' }).click();
  await page.getByText('个人笔记（不参与判分）').click();
  await page.getByRole('textbox', { name: '个人笔记' }).fill(
    '我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。');
  const savedNote = page.waitForResponse(response =>
    new URL(response.url()).pathname ===
      `/api/growth/local/knowledge/cards/${cardId}/note` &&
    response.request().method() === 'PUT', { timeout: 30_000 });
  await page.getByRole('button', { name: '保存笔记' }).click();
  if ((await savedNote).status() !== 200) throw new Error('S04 个人笔记保存失败');
  detail = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
  card = await detail.json();
  evidence.checks.personal_note_saved = card.learning?.note_text?.includes('没有实现 JWT 验签');
  const vaultLoaded = page.waitForResponse(response => new URL(response.url()).pathname ===
    '/api/growth/local/knowledge/vault', { timeout: 30_000 });
  await page.goto(`${origin}/#/growth?view=settings`);
  await vaultLoaded;
  await page.getByRole('textbox', { name: '现有 Obsidian vault 路径' }).fill(vault);
  await page.getByRole('textbox', { name: '目标目录' }).fill('工程成长伴侣/认证授权');
  await page.getByRole('button', { name: '检查并保存连接' }).click();
  await page.getByText('现有知识库路径已保存').waitFor({ timeout: 30_000 });
  await page.goto(`${origin}/#/growth?view=cards&card=${cardId}&project=${projectId}`);
  await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).click();
  const editor = page.getByRole('textbox', { name: 'Wiki 应用草稿' });
  await editor.waitFor({ timeout: 30_000 });
  await editor.fill((await editor.inputValue()) +
    '\n人工核对：这里仅演示资源所有者授权，没有实现 JWT 验签。');
  await page.getByRole('button', { name: '保存草稿' }).click();
  await page.getByText('草稿已保存。').waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: '确认写入 Obsidian' }).click();
  await page.getByText('已确认写入 Obsidian。').waitFor({ timeout: 30_000 });
  const article = (await index()).wiki_articles.find(item => item.concept_id === card.card.concept_id);
  evidence.wiki_relative_path = article?.relative_path;
  evidence.checks.wiki_confirmed = !!article?.relative_path &&
    (await readFile(path.join(vault, article.relative_path), 'utf8')).includes('没有实现 JWT 验签');
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('S04 知识优先完整链断言失败');
  evidence.status = 'PASS'; await save();
  process.stdout.write(`S04_KNOWLEDGE_FIRST_PASS ${cardId}\n`);
} catch (error) {
  evidence.status = 'FAIL'; evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  if (page && !page.isClosed()) {
    evidence.last_url = page.url();
    evidence.alerts = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
  }
  await save(); throw error;
} finally {
  if (electron) await electron.close();
}
