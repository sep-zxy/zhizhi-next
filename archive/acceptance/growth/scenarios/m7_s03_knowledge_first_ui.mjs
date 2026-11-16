// Installed S03: analyze a captured Java cache snapshot and learn its first card.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, snapshotId, moduleId, vault, outputPath] = process.argv.slice(2);
if (![executable, workspace, snapshotId, moduleId, vault, outputPath].every(Boolean)) {
  throw new Error('用法：node m7_s03_knowledge_first_ui.mjs <exe> <repo> <snapshot> <module> <vault> <output>');
}
const evidence = { status: 'running', workspace, snapshot_id: snapshotId, module_id: moduleId,
  started_at: new Date().toISOString(), checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
await mkdir(path.join(vault, '.obsidian'), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
let page;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-s03-first')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const local = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`本机状态 HTTP ${response.status()}`);
    return response.json();
  };
  const index = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge`, { headers });
    if (response.status() !== 200) throw new Error(`知识索引 HTTP ${response.status()}`);
    return response.json();
  };
  const initial = await local();
  const snapshot = initial.snapshots.find(item => item.snapshot_id === snapshotId);
  if (!snapshot) throw new Error('S03 旧缓存快照不存在');
  const feature = initial.features.find(item => item.feature_id === snapshot.feature_id);
  if (!feature) throw new Error('S03 缓存快照缺少来源 Feature');
  evidence.project_id = feature.project_id;
  evidence.feature_id = snapshot.feature_id;
  evidence.head_sha = snapshot.head_sha;
  await page.goto(`${origin}/#/growth?view=all`);
  await page.locator('#growth-snapshot-select').selectOption(snapshotId);
  const policy = page.locator('section[aria-labelledby="growth-projects-title"] form:has(h3)');
  const modelAllowed = policy.getByLabel('允许将批准的内容发送给所选模型');
  if (!await modelAllowed.isChecked()) {
    await modelAllowed.check();
    const changed = page.waitForResponse(response => new URL(response.url()).pathname ===
      `/api/growth/local/projects/${feature.project_id}/policy`, { timeout: 30_000 });
    await policy.getByRole('button', { name: '保存项目开关' }).click();
    if ((await changed).status() !== 200) throw new Error('S03 项目模型授权失败');
  }
  const analysis = page.locator('#growth-analysis-title').locator('..');
  await analysis.locator('#growth-provider-select').selectOption('deepseek');
  const sources = analysis.locator('fieldset.growth-page__untracked');
  for (const name of ['PriceCache.java', 'OrderService.java']) {
    const source = sources.locator('label').filter({ hasText: name });
    await source.locator('input').check();
  }
  await analysis.getByRole('button', { name: '预览模型请求' }).click();
  const payload = await analysis.locator('.growth-page__payload').innerText();
  evidence.checks.real_java_sources_approved = payload.includes('PriceCache.java')
    && payload.includes('OrderService.java') && payload.includes('expiresAt')
    && payload.includes('gateway.priceCents');
  if (!evidence.checks.real_java_sources_approved) throw new Error('S03 预览缺少真实 Java 缓存源码');
  await analysis.locator('.growth-page__approval input').check();
  await analysis.getByRole('button', { name: '发送并生成学习机会' }).click();
  const deadline = Date.now() + 180_000;
  let candidate;
  while (Date.now() < deadline) {
    candidate = (await index()).candidates.find(item =>
      item.project_ids?.includes(feature.project_id)
      && /缓存|TTL|过期|PriceCache/i.test(`${item.title} ${item.learning_goal ?? ''}`));
    if (candidate) break;
    await page.waitForTimeout(1000);
  }
  evidence.candidate_id = candidate?.candidate_id;
  evidence.checks.real_model_cache_candidate = !!candidate;
  if (!candidate) throw new Error('真实 DeepSeek 未产生 TTL 缓存候选');
  await page.goto(`${origin}/#/growth?view=learning&project=${feature.project_id}`);
  const candidateRow = page.locator('.knowledge-candidate').filter({ hasText: candidate.title });
  await candidateRow.getByRole('button', {
    name: candidate.suggested_card_id ? '确认归并' : '确认成卡',
  }).click();
  await page.waitForURL(/card=/, { timeout: 30_000 });
  const cardId = new URL(page.url()).hash.match(/[?&]card=([^&]+)/)?.[1];
  if (!cardId) throw new Error('S03 候选未形成知识卡');
  evidence.card_id = cardId;
  evidence.checks.ui_confirm_card = !candidate.suggested_card_id
    || cardId === candidate.suggested_card_id;
  evidence.merged_existing_card = !!candidate.suggested_card_id;
  if (candidate.suggested_card_id) {
    await page.getByRole('button', { name: '标记', exact: true }).click();
    await page.getByRole('menuitem', { name: '修订卡片…' }).click();
    await page.getByRole('textbox', { name: '修订原因' }).fill(
      '新增 Java 订单计价缓存真实源码，重新学习访问时 TTL 及失效边界。');
    await page.getByRole('button', { name: '确认创建修订版本' }).click();
  }
  await page.goto(`${origin}/#/growth?view=cards&card=${cardId}&project=${feature.project_id}`);
  await page.getByRole('button', { name: '预览材料生成范围' }).waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: '预览材料生成范围' }).click();
  const setup = page.locator('.knowledge-material-setup');
  await setup.getByRole('button', { name: '确认并生成知识讲解与两题' }).waitFor({ timeout: 30_000 });
  evidence.checks.live_material_approval = (await setup.innerText()).includes('api.deepseek.com');
  const generated = page.waitForResponse(response => new URL(response.url()).pathname ===
    `/api/growth/local/knowledge/cards/${cardId}/materials`, { timeout: 180_000 });
  await setup.getByRole('button', { name: '确认并生成知识讲解与两题' }).click();
  if ((await generated).status() !== 200) throw new Error('S03 材料生成失败');
  await page.getByRole('button', { name: '翻面看讲解' }).click();
  await page.getByRole('button', { name: '进入理解检查' }).click();
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
  const vaultLoaded = page.waitForResponse(response => new URL(response.url()).pathname ===
    '/api/growth/local/knowledge/vault', { timeout: 30_000 });
  await page.goto(`${origin}/#/growth?view=settings`);
  await vaultLoaded;
  await page.getByRole('textbox', { name: '现有 Obsidian vault 路径' }).fill(vault);
  await page.getByRole('textbox', { name: '目标目录' }).fill('工程成长伴侣/订单计价');
  await page.getByRole('button', { name: '检查并保存连接' }).click();
  await page.getByText('现有知识库路径已保存').waitFor({ timeout: 30_000 });
  await page.goto(`${origin}/#/growth?view=cards&card=${cardId}&project=${feature.project_id}`);
  await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).click();
  const editor = page.getByRole('textbox', { name: 'Wiki 应用草稿' });
  await editor.waitFor({ timeout: 30_000 });
  await editor.fill((await editor.inputValue()) + '\n人工核对：TTL 到期后，下一次访问才重载；没有后台刷新。');
  await page.getByRole('button', { name: '保存草稿' }).click();
  await page.getByText('草稿已保存。').waitFor({ timeout: 30_000 });
  const saved = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
  if (saved.status() !== 200 || !(await saved.json()).wiki_draft?.markdown?.includes('下一次访问才重载')) {
    throw new Error('S03 UI 编辑内容未写入草稿');
  }
  await page.getByRole('button', { name: '确认写入 Obsidian' }).click();
  await page.getByText('已确认写入 Obsidian。').waitFor({ timeout: 30_000 });
  const card = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
  if (card.status() !== 200) throw new Error('S03 无法读取知识卡概念 ID');
  const conceptId = (await card.json()).card.concept_id;
  const article = (await index()).wiki_articles.find(item => item.concept_id === conceptId);
  evidence.wiki_relative_path = article?.relative_path;
  const file = article?.relative_path ? path.join(vault, article.relative_path) : '';
  evidence.checks.wiki_confirmed = !!file && (await readFile(file, 'utf8')).includes('下一次访问才重载');
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('S03 首次学习断言未通过');
  evidence.status = 'PASS'; evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_FIRST_LEARNING_PASS ${cardId}\n`);
} catch (error) {
  evidence.status = 'FAIL'; evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  if (page && !page.isClosed()) {
    evidence.last_url = page.url();
    evidence.material_panel = await page.locator('.knowledge-material-setup').allInnerTexts().catch(() => []);
    evidence.alerts = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
  }
  evidence.finished_at = new Date().toISOString(); await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
