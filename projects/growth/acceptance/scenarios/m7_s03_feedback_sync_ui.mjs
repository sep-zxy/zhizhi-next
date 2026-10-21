// Sync the installed S03 A1/A2 feedback chain to its isolated PostgreSQL account.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, preparationPath, credentialsPath, outputPath, ...flags] = process.argv.slice(2);
if (!executable || !preparationPath || !credentialsPath || !outputPath
    || flags.some((flag) => flag !== '--live')) {
  throw new Error('用法：node m7_s03_feedback_sync_ui.mjs <exe> <preparation.json> <credentials.json> <output.json> [--live]');
}
const live = flags.includes('--live');
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
if (credentials.account_id !== prepared.account_id ||
    `http://127.0.0.1:${credentials.port}` !== prepared.cloud_origin) {
  throw new Error('S03 同步账号与来源不一致');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: live ? 'live_observation' : 'replay_not_live', status: 'running',
  executable_sha256: hash.digest('hex'), workspace: prepared.workspace,
  task_id: prepared.task_id, account_id: prepared.account_id,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-sync-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable, args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: prepared.workspace }, timeout: 90_000,
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
  const chainResponse = await page.request.get(
    `${origin}/api/growth/local/tasks/${prepared.task_id}`,
    { headers: { 'X-AhaDiff-Token': token } });
  if (chainResponse.status() !== 200) throw new Error('S03 本地反馈链不可读取');
  const chain = await chainResponse.json();
  if (chain.attempts.length !== 2 ||
      chain.attempts.some((attempt) => attempt.status !== 'feedback_ready')) {
    throw new Error('S03 云同步前两次反馈未完成');
  }
  evidence.attempt_ids = chain.attempts.map((item) => item.attempt_id);
  const syncSection = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = syncSection.locator('details').first();
  if (!await details.evaluate((element) => element.open)) {
    await details.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(prepared.cloud_origin);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const sync = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await syncSection.locator('form:has(#growth-cloud-url) button').click();
  const synced = await sync;
  if (synced.status() !== 200) throw new Error(`S03 反馈云同步失败：${synced.status()}`);
  evidence.sync = await synced.json();
  if (evidence.sync.account_id !== prepared.account_id ||
      evidence.sync.outbox.pending !== 0 || evidence.sync.outbox.conflict !== 0 ||
      evidence.sync.uploaded < 4) {
    throw new Error('S03 反馈云同步未确认两次回答和反馈');
  }
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_FEEDBACK_SYNC_OBSERVED ${prepared.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
