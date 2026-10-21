// Continue an unsubmitted objective answer on device B and publish its Wiki on A.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [phase, executable, workspace, credentialsPath, cardId, vault, outputPath] =
  process.argv.slice(2);
if (!['resume', 'publish', 'read'].includes(phase) || ![
  executable, workspace, credentialsPath, cardId, vault, outputPath,
].every(Boolean)) {
  throw new Error('用法：node m7_s02_first_continue_ui.mjs <resume|publish|read> <exe> <workspace> <credentials> <card-id> <vault> <output>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const evidence = { status: 'running', phase, card_id: cardId,
  account_id: credentials.account_id, started_at: new Date().toISOString(), checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), `electron-s02-first-${phase}`)}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const detail = async () => {
    const response = await page.request.get(
      `${origin}/api/growth/local/knowledge/cards/${cardId}`, { headers });
    if (response.status() !== 200) throw new Error(`S02 本机卡片 HTTP ${response.status()}`);
    return response.json();
  };
  const index = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/knowledge`, { headers });
    if (response.status() !== 200) throw new Error(`S02 知识索引 HTTP ${response.status()}`);
    return response.json();
  };
  const synchronize = async () => {
    await page.goto(`${origin}/#/growth?view=all`);
    const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
    const details = sync.locator('details').first();
    if (!await details.evaluate(element => element.open)) await details.locator('summary').click();
    await page.locator('#growth-cloud-url').fill(credentials.cloud_url);
    await page.locator('#growth-cloud-token').fill(credentials.access_token);
    const response = page.waitForResponse(item =>
      new URL(item.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
    await sync.locator('form:has(#growth-cloud-url) button').click();
    const completed = await response;
    if (completed.status() !== 200) throw new Error(`S02 同步 HTTP ${completed.status()}`);
    return completed.json();
  };
  if (phase === 'resume') {
    await page.goto(`${origin}/#/growth?view=cards&card=${cardId}`);
    const first = page.getByRole('radio').first();
    await first.waitFor({ timeout: 30_000 });
    evidence.checks.draft_restored_on_second_device = await first.isChecked();
    const before = await detail();
    evidence.checks.not_submitted_before_resume = !before.learning.answers.understanding
      && before.learning.stage === 'understanding';
    if (!Object.values(evidence.checks).every(Boolean)) {
      throw new Error('S02 第二端未恢复首次理解题的未提交选项');
    }
    await page.getByRole('button', { name: '提交答案' }).click();
    await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).waitFor({ timeout: 30_000 });
    evidence.checks.understanding_explanations = await page.locator('.knowledge-explanation').count() === 4;
    await page.getByRole('button', { name: '我已读完解析，继续结果预测' }).click();
    await page.getByRole('radio').first().check();
    await page.getByRole('button', { name: '提交答案' }).click();
    await page.getByRole('button', { name: '我已读完解析，完成学习' }).waitFor({ timeout: 30_000 });
    evidence.checks.prediction_explanations = await page.locator('.knowledge-explanation').count() === 4;
    await page.getByRole('button', { name: '我已读完解析，完成学习' }).click();
    await page.getByRole('button', { name: /提炼 Wiki 草稿/ }).click();
    await page.getByRole('textbox', { name: 'Wiki 应用草稿' }).waitFor({ timeout: 30_000 });
    const completed = await detail();
    evidence.checks.learning_completed_on_second_device = completed.learning.stage === 'completed'
      && completed.learning.answers.understanding?.explanation_confirmed
      && completed.learning.answers.prediction?.explanation_confirmed;
    evidence.checks.draft_without_vault = completed.wiki_draft?.status === 'draft'
      && !(await index()).vault_path;
    evidence.sync = await synchronize();
    evidence.checks.second_device_uploaded_learning = evidence.sync.knowledge_uploaded >= 1;
  } else if (phase === 'publish') {
    evidence.sync = await synchronize();
    const resumed = await detail();
    evidence.checks.first_device_pulled_draft = evidence.sync.knowledge_pulled >= 1
      && resumed.learning.stage === 'completed' && resumed.wiki_draft?.status === 'draft';
    if (!evidence.checks.first_device_pulled_draft) {
      throw new Error('S02 第一端未取得第二端完成学习后的未发布 Wiki 草稿');
    }
    const vaultLoaded = page.waitForResponse(item =>
      new URL(item.url()).pathname === '/api/growth/local/knowledge/vault',
    { timeout: 30_000 });
    await page.goto(`${origin}/#/growth?view=settings`);
    await vaultLoaded;
    await page.getByRole('textbox', { name: '现有 Obsidian vault 路径' }).fill(vault);
    await page.getByRole('textbox', { name: '目标目录' }).fill('工程成长伴侣/安装版首次学习');
    await page.getByRole('button', { name: '检查并保存连接' }).click();
    await page.getByText('现有知识库路径已保存').waitFor({ timeout: 30_000 });
    await page.goto(`${origin}/#/growth?view=wiki&card=${cardId}`);
    const editor = page.getByRole('textbox', { name: 'Wiki 应用草稿' });
    await editor.waitFor({ timeout: 30_000 });
    await editor.fill((await editor.inputValue())
      + '\n人工核对：旧请求仍会完成，但只能由最新请求更新搜索结果。');
    await page.getByRole('button', { name: '确认写入 Obsidian' }).click();
    await page.getByText('已确认写入 Obsidian。').waitFor({ timeout: 30_000 });
    const published = await detail();
    const knowledge = await index();
    const article = knowledge.wiki_articles.find(item =>
      item.concept_id === published.card.concept_id);
    evidence.wiki_relative_path = article?.relative_path;
    evidence.checks.manually_confirmed_original_draft = published.wiki_draft?.status === 'published'
      && !!article?.relative_path;
    evidence.sync_after_publish = await synchronize();
    evidence.checks.wiki_uploaded = evidence.sync_after_publish.knowledge_uploaded >= 1;
  } else {
    evidence.sync = await synchronize();
    const observed = await detail();
    const knowledge = await index();
    const article = knowledge.wiki_articles.find(item =>
      item.concept_id === observed.card.concept_id);
    evidence.wiki_relative_path = article?.relative_path;
    await page.goto(`${origin}/#/growth?view=wiki&card=${cardId}`);
    await page.getByText('该版本已确认写入知识库。').waitFor({ timeout: 30_000 });
    await page.getByRole('textbox', { name: 'Wiki 应用草稿' }).waitFor({ timeout: 30_000 });
    evidence.checks.second_device_reads_same_article = evidence.sync.knowledge_pulled >= 1
      && observed.learning.stage === 'completed'
      && observed.wiki_draft?.status === 'published' && !!article?.relative_path
      && await page.getByRole('textbox', { name: 'Wiki 应用草稿' }).inputValue() !== '';
  }
  if (!Object.values(evidence.checks).every(Boolean)) {
    throw new Error(`S02 ${phase} 安装版阶段断言未全部通过`);
  }
  evidence.status = 'PASS'; evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_FIRST_${phase.toUpperCase()}_PASS ${cardId}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
