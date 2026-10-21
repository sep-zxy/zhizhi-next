// Publish a saved S01 learning chain through the installed UI to a cloud API.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, outputPath, modeFlag] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !outputPath
  || (modeFlag && !['--replay', '--windows-local'].includes(modeFlag))) {
  throw new Error('用法：node m7_s01_cloud_ui.mjs <exe> <workspace> <credentials.json> <output.json> [--replay|--windows-local]');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (!credentials.access_token || !credentials.account_id || !/^https?:\/\//.test(cloudUrl)) {
  throw new Error('云端凭据不完整');
}
const mode = modeFlag === '--replay' ? 'replay_not_live'
  : modeFlag === '--windows-local' ? 'windows_local_live' : 'live_observation';
if (mode === 'live_observation' && /^(?:localhost|127\.0\.0\.1|\[::1\])$/.test(
  new URL(cloudUrl).hostname)) {
  throw new Error('live 观察拒绝本机隔离云地址');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode,
  status: 'running',
  executable_sha256: hash.digest('hex'),
  workspace,
  account_id: credentials.account_id,
  cloud_origin: new URL(cloudUrl).origin,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth`);
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地凭据引导失败：${response.status}`);
    return (await response.json()).token;
  });
  const headers = { 'X-AhaDiff-Token': token };
  const stateResponse = await page.request.get(`${origin}/api/growth/local`, { headers });
  if (stateResponse.status() !== 200) throw new Error('无法读取本地成长链');
  const state = await stateResponse.json();
  if (state.projects.length !== 1 || state.snapshots.length !== 1
    || state.opportunities.length < 1 || state.opportunities.length > 3
    || state.growth_tasks.length !== 1) {
    throw new Error('S01 隔离链应只有一个项目、快照和卡片，机会不超过三条');
  }
  evidence.project_id = state.projects[0].project_id;
  evidence.snapshot_id = state.snapshots[0].snapshot_id;
  evidence.opportunity_id = state.growth_tasks[0].opportunity_id;
  if (!state.opportunities.some((item) => item.opportunity_id === evidence.opportunity_id)) {
    throw new Error('卡片没有对应的模型机会');
  }
  evidence.task_id = state.growth_tasks[0].task_id;
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  await sync.locator(':scope > details > summary').click();
  const previewPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync/preview',
  { timeout: 30_000 });
  await sync.locator('details > button').first().click();
  const previewResponse = await previewPromise;
  if (previewResponse.status() !== 200) {
    throw new Error(`云端发布预览失败：${previewResponse.status()}`);
  }
  const preview = await previewResponse.json();
  const kinds = preview.operations.map((item) => item.kind);
  if (preview.project_id !== evidence.project_id || preview.count !== kinds.length
    || !['project', 'feature', 'snapshot', 'analysis', 'topic_decision', 'task',
      'attempt', 'feedback', 'progress', 'note'].every((kind) => kinds.includes(kind))) {
    throw new Error(`发布预览缺少学习链对象：${kinds.join(',')}`);
  }
  evidence.preview = {
    digest: preview.digest,
    count: preview.count,
    operation_kinds: kinds,
    source_ids: preview.operations.map((item) => item.source_id),
  };
  await save();
  await sync.locator('input[type="checkbox"]').first().check();
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const submittedPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync',
  { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const submitted = await submittedPromise;
  if (submitted.status() !== 200) {
    throw new Error(`云端同步失败：${submitted.status()} ${await submitted.text()}`);
  }
  const summary = await submitted.json();
  await sync.locator('details > p[role="status"]').waitFor({ timeout: 30_000 });
  const uiStatus = await sync.locator('details > p[role="status"]').innerText();
  const alerts = await sync.locator('details > p[role="alert"]').allInnerTexts();
  if (alerts.length || summary.account_id !== credentials.account_id
    || summary.outbox.pending !== 0 || summary.outbox.conflict !== 0
    || summary.outbox.deleted !== 0 || summary.uploaded < preview.count) {
    throw new Error(`同步摘要异常：${JSON.stringify({ summary, alerts })}`);
  }
  evidence.summary = summary;
  evidence.ui_status = uiStatus;
  await save();
  await electron.close();
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000,
  });
  const reopened = await electron.firstWindow({ timeout: 90_000 });
  await reopened.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  const reopenedOrigin = new URL(reopened.url()).origin;
  const reopenedToken = await reopened.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`重启后本地凭据引导失败：${response.status}`);
    return (await response.json()).token;
  });
  const chainResponse = await reopened.request.get(
    `${reopenedOrigin}/api/growth/local/tasks/${evidence.task_id}`,
    { headers: { 'X-AhaDiff-Token': reopenedToken } });
  if (chainResponse.status() !== 200) throw new Error('同步后本机卡片链不可读取');
  const chain = await chainResponse.json();
  if (chain.attempts.length !== 2 || chain.notes.length !== 1
    || chain.task.progress !== 'completed') {
    throw new Error('同步后本机回答、笔记或进度丢失');
  }
  evidence.local_chain_after = {
    attempt_ids: chain.attempts.map((item) => item.attempt_id),
    note_id: chain.notes[0].note_id,
    progress: chain.task.progress,
  };
  evidence.reopened_after_sync = true;
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_CLOUD_OBSERVED ${mode} ${evidence.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
