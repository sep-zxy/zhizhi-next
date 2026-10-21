// Save A0 as a cloud-synced draft, pause the card, and exit the installed app.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, cardPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !cardPath || !outputPath) {
  throw new Error('用法：node m7_s02_draft_pause_ui.mjs <exe> <workspace> <credentials> <card> <output>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cardEvidence = JSON.parse(await readFile(cardPath, 'utf8'));
const taskId = cardEvidence.task_id;
const draftText = '最后应该显示 ab 的结果，因为返回时还要检查请求编号。';
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
const evidence = {
  mode: 'live_observation', status: 'running', account_id: credentials.account_id,
  task_id: taskId, draft_text: draftText, cloud_origin: cloudUrl,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-profile-s02-draft')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const local = await (await page.request.get(`${origin}/api/growth/local`, { headers })).json();
  if (local.growth_tasks.length !== 1 || local.growth_tasks[0].task_id !== taskId
    || !['ready', 'in_progress'].includes(local.growth_tasks[0].progress)) {
    throw new Error('S02 草稿前置卡片不是唯一待开始任务');
  }
  const project = local.projects[0];
  const binding = local.bindings[0];
  const snapshot = local.snapshots[0];
  await page.locator('#growth-project-select').selectOption(project.project_id);
  await page.locator('#growth-binding-select').selectOption(binding.binding_id);
  await page.locator('#growth-snapshot-select').selectOption(snapshot.snapshot_id);
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: cardEvidence.task_chain.opportunity.title });
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  if (local.growth_tasks[0].progress === 'ready') {
    const startedResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/tasks/${taskId}/progress`),
    { timeout: 30_000 });
    await card.getByRole('button', { name: '开始或继续' }).click();
    if ((await startedResponse).status() !== 200) throw new Error('S02 卡片开始失败');
  }
  evidence.started_card = true;
  await save();

  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = sync.locator('details').first();
  if (!await details.evaluate((element) => element.open)) await details.locator('summary').click();
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const synchronize = async () => {
    const responsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/growth/local/sync',
    { timeout: 180_000 });
    await sync.locator('form:has(#growth-cloud-url) button').click();
    const response = await responsePromise;
    if (response.status() !== 200) throw new Error(`S02 云同步 HTTP ${response.status()}`);
    const summary = await response.json();
    if (summary.account_id !== credentials.account_id || summary.outbox.conflict !== 0) {
      throw new Error('S02 云同步账号或冲突状态不正确');
    }
    return summary;
  };
  evidence.started_sync = await synchronize();
  await save();
  const excerptSource = page.locator('#growth-excerpt-source');
  await excerptSource.waitFor({ state: 'visible', timeout: 30_000 });
  await excerptSource.selectOption({ label: 'src/search.ts' });
  await page.locator('#growth-excerpt-start').fill('5');
  await page.locator('#growth-excerpt-end').fill('14');
  const excerptPreviewResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/excerpt/preview'), { timeout: 30_000 });
  await page.getByRole('button', { name: '预览片段' }).click();
  const excerptPreview = await excerptPreviewResponse;
  if (excerptPreview.status() !== 200) throw new Error('S02 源码片段预览失败');
  evidence.excerpt_preview = await excerptPreview.json();
  if (!evidence.excerpt_preview.content_text.includes('latestRequest')) {
    throw new Error('S02 源码片段未包含最新请求守卫');
  }
  await page.getByLabel('我已核对这段源码，同意同步到成长云').check();
  const excerptQueueResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/excerpt'), { timeout: 30_000 });
  await page.getByRole('button', { name: '加入云同步队列' }).click();
  const excerptQueued = await excerptQueueResponse;
  if (excerptQueued.status() !== 201) throw new Error(`S02 源码片段批准入队失败：${excerptQueued.status()} ${await excerptQueued.text()}`);
  evidence.excerpt_queue = await excerptQueued.json();
  evidence.excerpt_sync = await synchronize();
  await save();
  const taskSelect = page.locator('#growth-synced-task');
  await taskSelect.locator(`option[value="${credentials.account_id}:${taskId}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await taskSelect.selectOption(`${credentials.account_id}:${taskId}`);
  const taskPanel = taskSelect.locator('..');
  await page.locator('#growth-sync-task-answer').fill(draftText);
  const draftResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/sync/tasks/${taskId}/draft`),
  { timeout: 30_000 });
  await taskPanel.getByRole('button', { name: '保存离线草稿' }).click();
  const drafted = await draftResponse;
  if (drafted.status() !== 201) throw new Error(`S02 A0 草稿入队 HTTP ${drafted.status()}`);
  evidence.draft_receipt = await drafted.json();
  await taskPanel.getByText(draftText, { exact: true }).waitFor({ timeout: 30_000 });
  await save();

  const pausedResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/tasks/${taskId}/progress`),
  { timeout: 30_000 });
  await card.getByRole('button', { name: '暂停' }).click();
  const paused = await pausedResponse;
  if (paused.status() !== 200) throw new Error(`S02 暂停 HTTP ${paused.status()}`);
  evidence.paused_card = await paused.json();
  evidence.final_sync = await synchronize();
  const workspaceResponse = await page.request.get(`${origin}/api/growth/local/sync/workspace`,
    { headers });
  if (workspaceResponse.status() !== 200) throw new Error('S02 同步任务工作区不可读取');
  const synced = await workspaceResponse.json();
  evidence.synced_task = synced.tasks.find((item) => item.task_id === taskId);
  const chainResponse = await page.request.get(`${origin}/api/growth/local/tasks/${taskId}`,
    { headers });
  if (chainResponse.status() !== 200) throw new Error('S02 暂停后本机卡片不可读取');
  evidence.local_chain = await chainResponse.json();
  if (evidence.final_sync.outbox.pending !== 0 || !evidence.synced_task
    || evidence.synced_task.task.progress !== 'paused'
    || evidence.synced_task.remote_draft?.content_text !== draftText
    || evidence.synced_task.attempts.length !== 0
    || evidence.local_chain.task.progress !== 'paused'
    || evidence.local_chain.attempts.length !== 0) {
    throw new Error('S02 A0 草稿未同步为暂停且未提交的状态');
  }
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_DRAFT_PAUSED ${taskId}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
