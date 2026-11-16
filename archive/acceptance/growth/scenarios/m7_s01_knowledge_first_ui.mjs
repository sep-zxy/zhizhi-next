// Installed app: a fresh Git repository commit becomes a learned card and a confirmed Wiki.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, repository, commitSha, vault, outputPath, scenario = 'concurrency'] =
  process.argv.slice(2);
if (!executable || !repository || !commitSha || !vault || !outputPath) {
  throw new Error('用法：node m7_s01_knowledge_first_ui.mjs <exe> <fresh-git-repo> <commit-sha> <vault> <output.json> [concurrency|request-race]');
}
const scenarios = {
  concurrency: { project: 'S01 全流程安装版验收', source: /RouteService\.java/,
    payload: ['CompletableFuture', 'RouteService'], candidate: /CompletableFuture|线程|并行|异步/,
    wiki: '人工核对：执行器控制线程来源，join 等待组合结果。' },
  'request-race': { project: 'S02 全流程安装版验收', source: /src\/search\.mjs/,
    payload: ['latestRequest', 'search.mjs'], candidate: /搜索|请求|竞态|latestRequest/,
    wiki: '人工核对：旧请求仍会结束，但只允许最新请求更新界面。' },
};
const config = scenarios[scenario];
if (!config) throw new Error(`未知的知识首次学习情境：${scenario}`);
const evidence = { status: 'running', repository, commit_sha: commitSha, scenario,
  started_at: new Date().toISOString(), checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
await mkdir(path.join(vault, '.obsidian'), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-s01-knowledge-first')}`],
    env: { ...process.env, GROWTH_WORKSPACE: repository }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const localState = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`本机状态 HTTP ${response.status()}`);
    return response.json();
  };
  const knowledge = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge`, { headers });
    if (response.status() !== 200) throw new Error(`知识列表 HTTP ${response.status()}`);
    return response.json();
  };
  await page.goto(`${origin}/#/growth?view=projects`);
  await page.getByRole('button', { name: '添加项目' }).click();
  const importer = page.getByRole('dialog', { name: '导入本地项目' });
  await importer.getByRole('textbox', { name: '项目名称' }).fill(config.project);
  await importer.getByRole('textbox', { name: 'Git 仓库路径' }).fill(repository);
  await importer.getByLabel('允许对这个项目使用已配置模型').check();
  await importer.getByRole('button', { name: '导入项目' }).click();
  await page.waitForURL(/view=pending/, { timeout: 30_000 });
  const imported = await localState();
  const binding = imported.bindings.find(item =>
    item.canonical_local_path.toLowerCase() === repository.toLowerCase());
  const feature = imported.features.find(item => item.project_id === binding?.project_id);
  evidence.project_id = binding?.project_id;
  evidence.feature_id = feature?.feature_id;
  evidence.checks.ui_import = !!binding && !!feature;
  if (!evidence.checks.ui_import) throw new Error('安装版没有导入全新 Git 仓库和 Feature');
  await save();

  await page.goto(`${origin}/#/growth?view=projects&project=${binding.project_id}`);
  await page.getByRole('textbox', { name: '搜索提交信息或哈希' }).fill(commitSha.slice(0,12));
  const selector = page.getByRole('checkbox', { name: /选择提交/ });
  await selector.waitFor({ timeout: 30_000 });
  await selector.check();
  await page.getByRole('button', { name: '从所选 1 条提交准备候选' }).click();
  await page.getByText('已为所选提交分别保存源码快照').waitFor({ timeout: 30_000 });
  const snapshots = (await localState()).snapshots.filter(item => item.head_sha === commitSha);
  evidence.snapshot_id = snapshots.at(-1)?.snapshot_id;
  evidence.checks.ui_capture = !!evidence.snapshot_id;
  if (!evidence.checks.ui_capture) throw new Error('安装版未捕获选定的真实提交');
  await save();

  const analysis = page.locator('#growth-analysis-title').locator('..');
  await analysis.locator('#growth-provider-select').selectOption('deepseek');
  const source = analysis.locator('fieldset.growth-page__untracked');
  const sourceLabels = source.locator('label');
  const sourceCount = await sourceLabels.count();
  for (let index = 0; index < sourceCount; index++) {
    const label = sourceLabels.nth(index);
    if (config.source.test(await label.innerText())) await label.locator('input').check();
  }
  evidence.checks.real_source_selected = await source.locator('input:checked').count() > 0;
  if (!evidence.checks.real_source_selected) throw new Error('S01 真实并发源码未进入批准范围');
  await analysis.getByRole('button', { name: '预览模型请求' }).click();
  await analysis.locator('.growth-page__payload').waitFor({ timeout: 30_000 });
  const payload = await analysis.locator('.growth-page__payload').innerText();
  evidence.checks.approval_scope = config.payload.every(text => payload.includes(text));
  if (!evidence.checks.approval_scope) throw new Error('模型预览缺少本次真实来源代码');
  await analysis.locator('.growth-page__approval input').check();
  await analysis.getByRole('button', { name: '发送并生成学习机会' }).click();
  const deadline = Date.now() + 180_000;
  let candidate;
  while (Date.now() < deadline) {
    const index = await knowledge();
    candidate = index.candidates.find(item => item.source_commit_shas?.includes(commitSha));
    if (candidate) break;
    await page.waitForTimeout(1000);
  }
  evidence.candidate_id = candidate?.candidate_id;
  evidence.checks.real_model_candidate = !!candidate && config.candidate.test(candidate.title);
  if (!evidence.checks.real_model_candidate) throw new Error('真实 DeepSeek 分析未生成对应并发候选');
  await save();

  await page.goto(`${origin}/#/growth?view=learning&project=${binding.project_id}`);
  const card = page.locator('.knowledge-candidate').filter({ hasText: candidate.title });
  await card.getByRole('button', { name: '确认成卡' }).click();
  await page.waitForURL(/card=/, { timeout: 30_000 });
  const cardId = new URL(page.url()).hash.match(/[?&]card=([^&]+)/)?.[1];
  evidence.card_id = cardId;
  evidence.checks.ui_confirm_card = !!cardId;
  if (!cardId) throw new Error('安装版候选确认未进入正式卡');
  await save();

  await page.getByRole('button', { name: '预览材料生成范围' }).click();
  const setup = page.locator('.knowledge-material-setup');
  await setup.getByRole('button', { name: '确认并生成知识讲解与两题' }).waitFor({ timeout: 30_000 });
  evidence.checks.material_approval = (await setup.innerText()).includes('api.deepseek.com');
  if (!evidence.checks.material_approval) throw new Error('卡片材料未使用真实 DeepSeek 目标');
  const generated = page.waitForResponse(response => new URL(response.url()).pathname ===
    `/api/growth/local/knowledge/cards/${cardId}/materials`, { timeout: 180_000 });
  await setup.getByRole('button', { name: '确认并生成知识讲解与两题' }).click();
  const generatedResponse = await generated;
  if (generatedResponse.status() !== 200) throw new Error(`材料生成 HTTP ${generatedResponse.status()}`);
  await page.getByRole('button', { name: '翻面看讲解' }).click();
  await page.getByRole('button', { name: '进入理解检查' }).click();
  if (scenario === 'request-race') {
    const draftSaved = page.waitForResponse(response => new URL(response.url()).pathname ===
      `/api/growth/local/knowledge/cards/${cardId}/learning`, { timeout: 30_000 });
    await page.getByRole('radio').first().check();
    if ((await draftSaved).status() !== 200) throw new Error('S02 首次理解题选项草稿未持久化');
    await page.goto(`${origin}/#/growth?view=cards`);
    const paused = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
    const detail = await paused.json();
    const firstQuestion = detail.questions.find(item => item.kind === 'understanding');
    evidence.checks.option_draft_before_first_answer = paused.status() === 200
      && detail.learning.stage === 'understanding'
      && detail.learning.option_drafts[firstQuestion?.question_id] === firstQuestion?.options[0]?.id
      && !detail.learning.answers.understanding && !detail.learning.answers.prediction;
    evidence.checks.wiki_not_published = !detail.wiki_draft;
    evidence.paused_question_id = firstQuestion?.question_id;
    evidence.learning_stage = detail.learning.stage;
  } else {
  await page.getByRole('radio').first().check();
  await page.getByRole('button', { name: '提交答案' }).click();
  await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).waitFor({ timeout: 30_000 });
  evidence.checks.understanding_explanations = await page.locator('.knowledge-explanation').count() === 4;
  await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).click();
  await page.getByRole('radio').first().check();
  await page.getByRole('button', { name: '提交答案' }).click();
  await page.getByRole('button', { name: '我已读完解析，完成学习' }).waitFor({ timeout: 30_000 });
  evidence.checks.prediction_explanations = await page.locator('.knowledge-explanation').count() === 4;
  await page.getByRole('button', { name: '我已读完解析，完成学习' }).click();
  await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).waitFor({ timeout: 30_000 });
  evidence.checks.ui_learning_completed = true;
  await save();

  const vaultLoaded = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/growth/local/knowledge/vault',
  { timeout: 30_000 });
  await page.goto(`${origin}/#/growth?view=settings`);
  await vaultLoaded;
  await page.getByRole('textbox', { name: '现有 Obsidian vault 路径' }).fill(vault);
  await page.getByRole('textbox', { name: '目标目录' }).fill('工程成长伴侣/安装版首次学习');
  const saveVault = page.getByRole('button', { name: '检查并保存连接' });
  await saveVault.waitFor({ state: 'visible' });
  if (!await saveVault.isEnabled()) throw new Error('Vault 表单填写后仍禁用保存');
  await saveVault.click();
  await page.getByText('现有知识库路径已保存').waitFor({ timeout: 30_000 });
  await page.goto(`${origin}/#/growth?view=cards&card=${cardId}`);
  await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).click();
  await page.getByRole('textbox', { name: 'Wiki 应用草稿' }).waitFor({ timeout: 30_000 });
  const detailBefore = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
  const draft = (await detailBefore.json()).wiki_draft;
  evidence.checks.draft_before_publish = draft?.status === 'draft';
  if (!evidence.checks.draft_before_publish) throw new Error('确认前的 Wiki 草稿状态不正确');
  const editor = page.getByRole('textbox', { name: 'Wiki 应用草稿' });
  await editor.fill((await editor.inputValue()) + `\n${config.wiki}`);
  await page.getByRole('button', { name: '确认写入 Obsidian' }).click();
  await page.getByText('已确认写入 Obsidian。').waitFor({ timeout: 30_000 });
  const index = await knowledge();
  const article = index.wiki_articles.find(item => item.concept_id === candidate.concept_id
    || item.card_id === cardId) ?? index.wiki_articles[0];
  evidence.wiki_relative_path = article?.relative_path;
  const wikiText = article?.relative_path
    ? await readFile(path.join(vault, article.relative_path), 'utf8') : '';
  evidence.checks.wiki_published = wikiText.includes(config.wiki);
  if (!evidence.checks.wiki_published) throw new Error('人工确认后的现有 Obsidian vault 文件不存在或内容不符');
  }
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('S01 安装版端到端存在失败断言');
  evidence.status = 'PASS'; evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_KNOWLEDGE_FIRST_UI_PASS ${cardId}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
