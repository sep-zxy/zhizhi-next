// Installed S03: reindex the rebased Java module, merge its evidence, relearn, update Wiki.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, oldSnapshotId, newSnapshotId, moduleId, cardId, oldWikiPath,
  credentialsPath, outputPath] = process.argv.slice(2);
if (![executable, workspace, oldSnapshotId, newSnapshotId, moduleId, cardId,
  oldWikiPath, credentialsPath, outputPath].every(Boolean)) {
  throw new Error('用法：node m7_s03_reindex_revision_ui.mjs <exe> <repo> <old-snapshot> <new-snapshot> <module> <card> <wiki-path> <credentials> <output>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const evidence = { status: 'running', card_id: cardId, module_id: moduleId,
  old_snapshot_id: oldSnapshotId, new_snapshot_id: newSnapshotId,
  started_at: new Date().toISOString(), checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-s03-revision')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const detail = async (selectedCardId = cardId) => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${selectedCardId}`, { headers });
    if (response.status() !== 200) throw new Error(`知识卡 HTTP ${response.status()}`);
    return response.json();
  };
  const index = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge`, { headers });
    if (response.status() !== 200) throw new Error(`知识索引 HTTP ${response.status()}`);
    return response.json();
  };
  const stateResponse = await page.request.get(`${origin}/api/growth/local`, { headers });
  if (stateResponse.status() !== 200) throw new Error('S03 本机项目状态不可读');
  const state = await stateResponse.json();
  const newSnapshot = state.snapshots.find(item => item.snapshot_id === newSnapshotId);
  const feature = state.features.find(item => item.feature_id === newSnapshot?.feature_id);
  if (!feature) throw new Error('S03 rebase 后快照没有来源项目');
  const before = await detail();
  evidence.before_version = before.card.card_version;
  evidence.checks.old_card_and_answers_retained = before.learning.stage === 'completed'
    && before.questions.length === 2 && before.sources.some(source => source.relative_path?.endsWith('PriceCache.java'));
  if (!evidence.checks.old_card_and_answers_retained) throw new Error('换基后旧卡或旧来源丢失');
  await page.goto(`${origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate(element => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  const previewed = page.waitForResponse(response => new URL(response.url()).pathname ===
    '/api/growth/local/sync/preview', { timeout: 30_000 });
  await sync.locator('details > button').first().click();
  const preview = await (await previewed).json();
  if (!preview.operations?.some(operation => operation.kind === 'snapshot'
      && operation.source_id === newSnapshotId)) {
    throw new Error('S03 换基后新快照未进入云发布预览');
  }
  await sync.locator('input[type="checkbox"]').first().check();
  await page.locator('#growth-cloud-url').fill(credentials.cloud_url);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const synced = page.waitForResponse(response => new URL(response.url()).pathname ===
    '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  if ((await synced).status() !== 200) throw new Error('S03 修订端云连接失败');
  await page.locator('#growth-snapshot-select').selectOption(newSnapshotId);
  const diagnostics = page.locator('section[aria-labelledby="growth-analysis-title"] details.growth-page__advanced').first();
  if (!await diagnostics.evaluate(element => element.open)) {
    await diagnostics.locator('summary').first().click();
  }
  await page.locator('#growth-code-symbols').fill(
    'OrderController.quote -> OrderService.quote -> PriceCache.get -> PricingGateway.priceCents');
  const modulePanel = page.locator('div[aria-labelledby="growth-module-title"]');
  const indexed = page.waitForResponse(response => new URL(response.url()).pathname ===
    `/api/growth/local/snapshots/${newSnapshotId}/code-index`, { timeout: 180_000 });
  await page.locator('.growth-page__code-index').first().locator('button').first().click();
  if ((await indexed).status() !== 200) throw new Error('S03 换基后源码索引失败');
  await modulePanel.locator(`#growth-module-select option[value="${moduleId}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await modulePanel.locator('#growth-module-select').selectOption(moduleId);
  const mapped = page.waitForResponse(response => new URL(response.url()).pathname ===
    '/api/growth/local/cloud/modules/map', { timeout: 120_000 });
  await modulePanel.getByRole('button', { name: '按当前快照重新索引所选模块' }).click();
  const mapResponse = await mapped;
  if (mapResponse.status() !== 200) throw new Error(`S03 锁定模块重索引 HTTP ${mapResponse.status()}`);
  evidence.reindex = await mapResponse.json();
  evidence.checks.locked_module_identity = evidence.reindex.module_id === moduleId;
  const analysis = page.locator('#growth-analysis-title').locator('..');
  await analysis.locator('#growth-provider-select').selectOption('deepseek');
  const sources = analysis.locator('fieldset.growth-page__untracked');
  for (const name of ['PriceCache.java', 'OrderService.java']) {
    await sources.locator('label').filter({ hasText: name }).locator('input').check();
  }
  await analysis.getByRole('button', { name: '预览模型请求' }).click();
  const payload = await analysis.locator('.growth-page__payload').innerText();
  evidence.checks.rebased_sources_approved = payload.includes('PriceCache.java')
    && payload.includes('OrderService.java');
  await analysis.locator('.growth-page__approval input').check();
  let completedAnalysis;
  evidence.analysis_attempts = [];
  for (let retry = 0; retry < 3; retry += 1) {
    if (!await analysis.locator('.growth-page__approval input').isChecked()) {
      await analysis.locator('.growth-page__approval input').check();
    }
    const submitted = page.waitForResponse(response => new URL(response.url()).pathname ===
      `/api/growth/local/snapshots/${newSnapshotId}/analysis`, { timeout: 30_000 });
    await analysis.getByRole('button', { name: '发送并生成学习机会' }).click();
    const response = await submitted;
    if (![200, 202].includes(response.status())) {
      throw new Error(`S03 换基分析提交 HTTP ${response.status()}`);
    }
    const run = await response.json();
    const until = Date.now() + 180_000;
    while (Date.now() < until) {
      const local = await page.request.get(`${origin}/api/growth/local`, { headers });
      const state = await local.json();
      completedAnalysis = state.analysis_runs.find(item => item.analysis_id === run.analysis_id);
      if (['succeeded', 'failed'].includes(completedAnalysis?.status)) break;
      await page.waitForTimeout(1000);
    }
    evidence.analysis_attempts.push({ analysis_id: run.analysis_id,
      status: completedAnalysis?.status, error: completedAnalysis?.error });
    if (completedAnalysis?.status === 'succeeded') break;
    if (completedAnalysis?.status !== 'failed'
        || !String(completedAnalysis.error).includes('failed to publish run artifacts')) {
      throw new Error(`S03 换基分析失败：${completedAnalysis?.error ?? 'timeout'}`);
    }
  }
  if (completedAnalysis?.status !== 'succeeded') {
    throw new Error('S03 换基分析多次发生上游产物发布故障');
  }
  const local = await page.request.get(`${origin}/api/growth/local`, { headers });
  const produced = (await local.json()).opportunities.filter(item =>
    item.analysis_id === completedAnalysis.analysis_id).map(item => item.opportunity_id);
  const deadline = Date.now() + 30_000;
  let candidate;
  while (Date.now() < deadline) {
    const current = await index();
    candidate = current.candidates.find(item => item.status === 'pending'
      && item.suggested_card_id
      && item.project_ids?.includes(feature.project_id)
      && current.cards.some(card => card.card_id === item.suggested_card_id)
      && item.opportunity_ids?.some(id => produced.includes(id)));
    if (candidate) break;
    await page.waitForTimeout(1000);
  }
  evidence.candidate_id = candidate?.candidate_id;
  evidence.checks.same_concept_suggested = !!candidate;
  if (!candidate) throw new Error('S03 换基后候选未建议关联本项目已有知识卡');
  const revisionCardId = candidate.suggested_card_id;
  const revisionBefore = await detail(revisionCardId);
  const priorArticle = (await index()).wiki_articles.find(item =>
    item.concept_id === revisionBefore.card.concept_id);
  if (revisionBefore.learning.stage !== 'completed' || !priorArticle?.relative_path) {
    throw new Error('S03 待归并卡缺少已完成学习或既有 Wiki');
  }
  evidence.revision_card_id = revisionCardId;
  evidence.revision_before_version = revisionBefore.card.card_version;
  await page.goto(`${origin}/#/growth?view=learning`);
  const row = page.locator('.knowledge-candidate').filter({ hasText: candidate.title });
  await row.getByRole('button', { name: '确认归并' }).click();
  await page.waitForURL(new RegExp(`card=${revisionCardId}`), { timeout: 30_000 });
  const merged = await detail(revisionCardId);
  evidence.checks.new_evidence_same_card = merged.card.card_id === revisionCardId
    && merged.sources.length > revisionBefore.sources.length;
  if (!evidence.checks.new_evidence_same_card) throw new Error('S03 新证据没有归并原卡');
  await page.getByRole('button', { name: '标记', exact: true }).click();
  await page.getByRole('menuitem', { name: '修订卡片…' }).click();
  await page.getByRole('textbox', { name: '修订原因' }).fill('缓存提交换基后增加来源版本，核对 TTL 和线程安全边界。');
  await page.getByRole('button', { name: '确认创建修订版本' }).click();
  evidence.checks.new_version = (await detail(revisionCardId)).card.card_version ===
    revisionBefore.card.card_version + 1;
  await page.goto(`${origin}/#/growth?view=cards&card=${revisionCardId}&project=${feature.project_id}`);
  await page.getByRole('button', { name: '预览材料生成范围' }).waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: '预览材料生成范围' }).click();
  const setup = page.locator('.knowledge-material-setup');
  await setup.getByRole('button', { name: '确认并生成知识讲解与两题' }).waitFor({ timeout: 30_000 });
  evidence.checks.live_material_approval = (await setup.innerText()).includes('api.deepseek.com');
  const generated = page.waitForResponse(response => new URL(response.url()).pathname ===
    `/api/growth/local/knowledge/cards/${revisionCardId}/materials`, { timeout: 180_000 });
  await setup.getByRole('button', { name: '确认并生成知识讲解与两题' }).click();
  if ((await generated).status() !== 200) throw new Error('S03 修订材料生成失败');
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
  const reviseDraft = page.getByRole('button', { name: '创建修订草稿' });
  if (await reviseDraft.count()) await reviseDraft.click();
  else await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).click();
  const editor = page.getByRole('textbox', { name: 'Wiki 应用草稿' });
  await editor.waitFor({ timeout: 30_000 });
  await editor.fill((await editor.inputValue()) + '\n人工复核修订：保留访问时惰性过期；并发访问仍须单独评估。');
  await page.getByRole('button', { name: '保存草稿' }).click();
  await page.getByText('草稿已保存。').waitFor({ timeout: 30_000 });
  if (!(await detail(revisionCardId)).wiki_draft?.markdown?.includes('人工复核修订')) {
    throw new Error('S03 修订内容未由 UI 保存到草稿');
  }
  await page.getByRole('button', { name: '确认写入 Obsidian' }).click();
  await page.getByText('已确认写入 Obsidian。').waitFor({ timeout: 30_000 });
  evidence.after_version = (await detail(revisionCardId)).card.card_version;
  evidence.wiki_relative_path = (await index()).wiki_articles.find(item =>
    item.concept_id === revisionBefore.card.concept_id)?.relative_path;
  const vaultResponse = await page.request.get(`${origin}/api/growth/local/knowledge/vault`, { headers });
  const vault = vaultResponse.status() === 200 ? await vaultResponse.json() : {};
  const article = evidence.wiki_relative_path && vault.path
    ? await readFile(path.join(vault.path, evidence.wiki_relative_path), 'utf8') : '';
  evidence.checks.original_wiki_updated = evidence.wiki_relative_path === priorArticle.relative_path
    && article.includes('人工复核修订');
  evidence.checks.first_card_preserved = (await detail()).learning.stage === 'completed'
    && (await index()).wiki_articles.some(item => item.relative_path === oldWikiPath);
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('S03 换基后知识修订断言未通过');
  evidence.status = 'PASS'; evidence.finished_at = new Date().toISOString(); await save();
  process.stdout.write(`S03_REINDEX_REVISION_PASS ${revisionCardId}\n`);
} catch (error) {
  evidence.status = 'FAIL'; evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  evidence.finished_at = new Date().toISOString(); await save(); throw error;
} finally {
  if (electron) await electron.close();
}
