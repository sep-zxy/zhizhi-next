// Save exact S03 N1 from the installed card while ReMe is unavailable.
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
  throw new Error('用法：node m7_s03_note_ui.mjs <exe> <preparation.json> <credentials.json> <output.json> [--live]');
}
const live = flags.includes('--live');
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
if (credentials.account_id !== prepared.account_id ||
    `http://127.0.0.1:${credentials.port}` !== prepared.cloud_origin) {
  throw new Error('S03 N1 云账号不一致');
}
const note = '订单计价现在先经过 PriceCache，再按需要调用 PricingGateway。这个例子的 TTL 是访问时检查的：到期不会自动刷新，下一次 get 才重新加载。我不能只看到 expiresAt 就推断项目里存在定时清理线程。另外，这个 HashMap 实现是否适合并发使用，需要单独检查。';
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: live ? 'live_observation' : 'replay_not_live', status: 'running',
  executable_sha256: hash.digest('hex'), workspace: prepared.workspace,
  account_id: prepared.account_id, task_id: prepared.task_id,
  note_text: note, note_sha256: createHash('sha256').update(note).digest('hex'),
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
  const headers = { 'X-AhaDiff-Token': token };
  const taskResponse = await page.request.get(
    `${origin}/api/growth/local/tasks/${prepared.task_id}`, { headers });
  if (taskResponse.status() !== 200) throw new Error('旧模块卡片不可读');
  const before = await taskResponse.json();
  if (before.task.module_id !== prepared.module_id ||
      before.task.module_index_revision !== prepared.old_module_index_revision ||
      before.attempts.length !== 2 || before.notes.length !== 0) {
    throw new Error('N1 前置模块卡片、回答或旧索引不一致');
  }
  await page.locator('#growth-snapshot-select')
    .selectOption(prepared.old_snapshot_id);
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: before.opportunity.title });
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.locator('#growth-note').fill(note);
  const noteResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/tasks/${prepared.task_id}/notes`),
    { timeout: 30_000 });
  await card.getByRole('button', { name: '保存笔记' }).click();
  const saved = await noteResponse;
  if (![200, 201].includes(saved.status())) {
    throw new Error(`本机 N1 保存失败：${saved.status()}`);
  }
  evidence.note_id = (await saved.json()).note_id;
  await card.getByText(note, { exact: true }).waitFor({ timeout: 30_000 });
  const localResponse = await page.request.get(
    `${origin}/api/growth/local/tasks/${prepared.task_id}`, { headers });
  const local = await localResponse.json();
  if (local.notes.length !== 1 || local.notes[0].note_id !== evidence.note_id ||
      local.notes[0].content_text !== note || local.attempts.length !== 2) {
    throw new Error('保存后本机 N1 或旧回答不一致');
  }
  evidence.local_note = local.notes[0];
  await save();

  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(prepared.cloud_origin);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const synced = await syncResponse;
  if (synced.status() !== 200) throw new Error(`N1 同步失败：${synced.status()}`);
  evidence.sync = await synced.json();
  if (evidence.sync.account_id !== prepared.account_id ||
      evidence.sync.outbox.pending !== 0 || evidence.sync.outbox.conflict !== 0) {
    throw new Error('N1 云端回执不完整');
  }
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_N1_SYNC_OBSERVED ${evidence.note_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
