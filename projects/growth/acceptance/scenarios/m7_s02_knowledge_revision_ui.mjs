// Relearn the real request-race card in the installed app, then update its Wiki.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, cardId, snapshotId, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !cardId || !snapshotId || !outputPath) {
  throw new Error('用法：node m7_s02_knowledge_revision_ui.mjs <exe> <workspace> <credentials> <card_id> <snapshot_id> <output>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const noteText = '我原以为先请求的结果会先展示。latestRequest 只阻止过期响应写入界面；它不会取消旧网络请求。';
const evidence = { status: 'running', card_id: cardId, note_text: noteText,
  started_at: new Date().toISOString(), checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-s02-knowledge')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const detail = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
    if (response.status() !== 200) throw new Error(`本机知识卡 HTTP ${response.status()}`);
    return response.json();
  };
  const before = await detail();
  evidence.before_version = before.card.card_version;
  evidence.before_wiki = before.wiki_draft?.status ?? null;
  if (before.questions.length !== 2 || before.learning.stage !== 'completed'
      || !before.sources.some(source => source.excerpt?.includes('latestRequest'))) {
    throw new Error('S02 原始真实模型卡、已完成学习或源码证据不完整');
  }
  await page.goto(`${origin}/#/growth?view=cards&card=${cardId}`);
  await page.getByRole('button', { name: '标记', exact: true }).waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: '标记', exact: true }).click();
  await page.getByRole('menuitem', { name: '修订卡片…' }).click();
  await page.getByRole('textbox', { name: '修订原因' }).fill('核对搜索竞态的第二个真实提交，重新学习过期响应和取消请求的边界。');
  await page.getByRole('button', { name: '确认创建修订版本' }).click();
  await page.getByText('先生成知识讲解与两道检查题').waitFor({ timeout: 30_000 });
  const revised = await detail();
  evidence.revised_version = revised.card.card_version;
  evidence.checks.new_version = revised.card.card_version === before.card.card_version + 1
    && revised.questions.length === 0;
  if (!evidence.checks.new_version) throw new Error('S02 修订没有保留旧版并创建待学习新版本');
  await page.getByRole('button', { name: '预览材料生成范围' }).click();
  const preview = page.locator('.knowledge-material-setup');
  await preview.getByRole('button', { name: '确认并生成知识讲解与两题' }).waitFor({ timeout: 30_000 });
  evidence.checks.model_preview = (await preview.innerText()).includes('api.deepseek.com');
  if (!evidence.checks.model_preview) throw new Error('S02 模型批准范围未显示真实 DeepSeek 目标');
  const materialsResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/growth/local/knowledge/cards/${cardId}/materials`,
  { timeout: 180_000 });
  await preview.getByRole('button', { name: '确认并生成知识讲解与两题' }).click();
  const generated = await materialsResponse;
  if (generated.status() !== 200) {
    throw new Error(`S02 新版真实模型材料 HTTP ${generated.status()}: ${await generated.text()}`);
  }
  await page.getByRole('button', { name: '翻面看讲解' }).waitFor({ timeout: 30_000 });
  const material = await detail();
  evidence.question_ids = material.questions.map(question => question.question_id);
  evidence.checks.real_material = material.questions.length === 2
    && material.questions.every(question => question.options.length === 4);
  if (!evidence.checks.real_material) throw new Error('S02 新版真实模型题包不完整');
  await page.getByRole('button', { name: '翻面看讲解' }).click();
  await page.getByRole('button', { name: '进入理解检查' }).click();
  const firstOption = page.getByRole('radio').first();
  await firstOption.check();
  await page.getByRole('link', { name: '知识卡', exact: true }).first().click();
  const libraryCard = page.locator('.knowledge-card').filter({ hasText: cardId.slice(0, 8) });
  await libraryCard.getByRole('button', { name: '继续学习' }).click();
  await page.getByRole('radio').first().waitFor({ timeout: 30_000 });
  evidence.checks.option_draft_survived_pause = await page.getByRole('radio').first().isChecked();
  if (!evidence.checks.option_draft_survived_pause) throw new Error('S02 离开并恢复后选项草稿丢失');
  await page.getByRole('button', { name: '提交答案' }).click();
  await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).waitFor({ timeout: 30_000 });
  evidence.checks.understanding_explanations = await page.locator('.knowledge-explanation').count() === 4;
  if (!evidence.checks.understanding_explanations) throw new Error('S02 理解题逐项解析不足四项');
  await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).click();
  await page.getByRole('radio').first().check();
  await page.getByRole('button', { name: '提交答案' }).click();
  await page.getByRole('button', { name: '我已读完解析，完成学习' }).waitFor({ timeout: 30_000 });
  evidence.checks.prediction_explanations = await page.locator('.knowledge-explanation').count() === 4;
  if (!evidence.checks.prediction_explanations) throw new Error('S02 预测题逐项解析不足四项');
  await page.getByRole('button', { name: '我已读完解析，完成学习' }).click();
  await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: '个人笔记（不参与判分）' }).click();
  await page.getByRole('textbox', { name: '个人笔记' }).fill(noteText);
  await page.getByRole('button', { name: '保存笔记' }).click();
  await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).click();
  await page.getByRole('textbox', { name: 'Wiki 应用草稿' }).waitFor({ timeout: 30_000 });
  evidence.checks.wiki_still_draft = (await detail()).wiki_draft?.status === 'draft';
  if (!evidence.checks.wiki_still_draft) throw new Error('S02 生成草稿时 Wiki 已提前写入');
  const editor = page.getByRole('textbox', { name: 'Wiki 应用草稿' });
  await editor.fill((await editor.inputValue()) + '\n人工核对：旧请求完成不代表有权更新最新搜索结果。');
  await page.getByRole('button', { name: '确认写入 Obsidian' }).click();
  await page.getByText('已确认写入 Obsidian。').waitFor({ timeout: 30_000 });
  const completed = await detail();
  const indexResponse = await page.request.get(`${origin}/api/growth/local/knowledge`, { headers });
  if (indexResponse.status() !== 200) throw new Error('S02 Wiki 索引不可读取');
  const index = await indexResponse.json();
  evidence.after_version = completed.card.card_version;
  evidence.after_stage = completed.learning.stage;
  evidence.wiki_path = index.wiki_articles.find(article =>
    article.concept_id === completed.card.concept_id)?.relative_path ?? null;
  evidence.checks.completed = completed.learning.stage === 'completed'
    && completed.learning.note_text === noteText
    && completed.learning.answers.understanding?.explanation_confirmed
    && completed.learning.answers.prediction?.explanation_confirmed;
  evidence.checks.wiki_published = completed.wiki_draft?.status === 'published'
    && !!evidence.wiki_path;
  if (!evidence.checks.completed || !evidence.checks.wiki_published) {
    throw new Error('S02 新版学习或人工确认的 Wiki 未持久化');
  }
  await page.goto(`${origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = sync.locator('details').first();
  if (!await details.evaluate(element => element.open)) await details.locator('summary').click();
  await page.locator('#growth-cloud-url').fill(credentials.cloud_url);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const responsePromise = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const response = await responsePromise;
  evidence.sync = await response.json();
  evidence.checks.synced = response.status() === 200 && evidence.sync.outbox?.pending === 0
    && evidence.sync.knowledge_uploaded >= 1;
  if (!evidence.checks.synced) throw new Error('S02 新版知识卡未由安装版上传');
  await page.locator('#growth-snapshot-select').selectOption(snapshotId);
  const source = page.locator('#growth-excerpt-source');
  await source.locator('option', { hasText: 'src/search.mjs' }).waitFor({ state: 'attached', timeout: 30_000 });
  await source.selectOption({ label: 'src/search.mjs' });
  await page.locator('#growth-excerpt-start').fill('1');
  await page.locator('#growth-excerpt-end').fill('10');
  const excerptPreview = page.waitForResponse(response =>
    new URL(response.url()).pathname.endsWith('/excerpt/preview'), { timeout: 30_000 });
  await page.getByRole('button', { name: '预览片段' }).click();
  const excerpt = await excerptPreview;
  if (excerpt.status() !== 200) {
    throw new Error(`S02 源码片段预览 HTTP ${excerpt.status()}: ${await excerpt.text()}`);
  }
  evidence.excerpt_preview = await excerpt.json();
  evidence.checks.excerpt_preview = evidence.excerpt_preview.content_text.includes('latestRequest')
    && evidence.excerpt_preview.relative_path === 'src/search.mjs';
  if (!evidence.checks.excerpt_preview) throw new Error('S02 批准前源码范围未包含竞态守卫');
  await page.getByLabel('我已核对这段源码，同意同步到成长云').check();
  const queuedResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname.endsWith('/excerpt'), { timeout: 30_000 });
  await page.getByRole('button', { name: '加入云同步队列' }).click();
  const queued = await queuedResponse;
  if (queued.status() !== 201) throw new Error(`S02 批准源码入队 HTTP ${queued.status()}`);
  evidence.excerpt_queue = await queued.json();
  const excerptSync = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const syncedExcerpt = await excerptSync;
  evidence.excerpt_sync = await syncedExcerpt.json();
  evidence.checks.excerpt_synced = syncedExcerpt.status() === 200
    && evidence.excerpt_sync.outbox?.pending === 0
    && evidence.excerpt_sync.uploaded >= 1;
  if (!evidence.checks.excerpt_synced) throw new Error('S02 人工批准的源码片段未上传云端');
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_KNOWLEDGE_REVISION_PASS ${cardId}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
