// Publish S02's live local card to an isolated PostgreSQL cloud account.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, cardPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !cardPath || !outputPath) {
  throw new Error('用法：node m7_s02_publish_ui.mjs <exe> <workspace> <credentials> <card> <output>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const card = JSON.parse(await readFile(cardPath, 'utf8'));
if (card.status !== 'observed_pending_audit' || !card.task_id || !credentials.account_id) {
  throw new Error('S02 云同步的真实卡片或账号缺失');
}
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
const evidence = {
  mode: 'live_observation', status: 'running', task_id: card.task_id,
  account_id: credentials.account_id, cloud_origin: cloudUrl,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-profile-s02-publish')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const local = await (await page.request.get(`${origin}/api/growth/local`, { headers })).json();
  if (local.projects.length !== 1 || local.growth_tasks.length !== 1
    || local.growth_tasks[0].task_id !== card.task_id) {
    throw new Error('S02 发布前项目与卡片非唯一');
  }
  await page.locator('#growth-project-select').selectOption(local.projects[0].project_id);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = sync.locator('details').first();
  if (!await details.evaluate((element) => element.open)) await details.locator('summary').click();
  const previewResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync/preview',
  { timeout: 30_000 });
  await sync.locator('details > button').first().click();
  const preview = await previewResponse;
  if (preview.status() !== 200) throw new Error(`S02 发布预览 HTTP ${preview.status()}`);
  evidence.preview = await preview.json();
  if (evidence.preview.project_id !== local.projects[0].project_id
    || evidence.preview.count < 2) {
    throw new Error('S02 发布预览未含项目与卡片链');
  }
  await sync.locator('input[type="checkbox"]').first().check();
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync',
  { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const uploaded = await syncResponse;
  if (uploaded.status() !== 200) throw new Error(`S02 首次云同步 HTTP ${uploaded.status()}`);
  evidence.sync = await uploaded.json();
  if (evidence.sync.account_id !== credentials.account_id
    || evidence.sync.outbox.pending !== 0 || evidence.sync.outbox.conflict !== 0) {
    throw new Error('S02 首次云同步未归零或账号不一致');
  }
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_PUBLISHED ${card.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
