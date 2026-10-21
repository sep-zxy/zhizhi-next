// Drive S03's exact A1/A2 through the installed module card without a model reply.
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
  throw new Error('用法：node m7_s03_answers_ui.mjs <exe> <preparation.json> <credentials.json> <output.json> [--live]');
}
const live = flags.includes('--live');
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
if (credentials.account_id !== prepared.account_id ||
    `http://127.0.0.1:${credentials.port}` !== prepared.cloud_origin) {
  throw new Error('S03 模块账号不匹配');
}
const a1 = 'TTL 到了以后会有后台线程自动删除缓存，然后自动去查询新的价格。';
const a2 = '这里没有看到定时任务。每次 get 会比较当前时间与 expiresAt，到期以后这次调用才执行 load 并覆盖缓存。如果没有下一次访问，就不会主动刷新。';
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: live ? 'live_observation' : 'replay_not_live', status: 'running',
  executable_sha256: hash.digest('hex'), workspace: prepared.workspace,
  task_id: prepared.task_id, module_id: prepared.module_id,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const match = (response, suffix) => new URL(response.url()).pathname.endsWith(suffix);
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
  const syncSection = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = syncSection.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(prepared.cloud_origin);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const sync = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await syncSection.locator('form:has(#growth-cloud-url) button').click();
  const synced = await sync;
  if (synced.status() !== 200) throw new Error(`S03 初次同步失败：${synced.status()}`);
  evidence.initial_sync = await synced.json();
  if (evidence.initial_sync.account_id !== prepared.account_id) {
    throw new Error('S03 初次同步账号不一致');
  }
  const readChain = async () => {
    const response = await page.request.get(
      `${origin}/api/growth/local/tasks/${prepared.task_id}`, { headers });
    if (response.status() !== 200) throw new Error('S03 卡片本地原文不可读取');
    return response.json();
  };
  const initial = await readChain();
  if (initial.task.module_id !== prepared.module_id ||
      initial.task.module_index_revision !== prepared.module_index_revision ||
      initial.attempts.length !== 0
      || (live ? !/expiresAt|TTL|PriceCache\.get|缓存.*条件|quote.*条件/i.test(initial.task.question)
        : !initial.task.question.includes('访问时检查 TTL'))) {
    throw new Error('S03 回答前置卡片或模块索引不一致');
  }
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: initial.opportunity.title });
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.getByText('订单计价', { exact: false }).waitFor({ timeout: 30_000 });
  if (initial.task.progress === 'ready') {
    const started = page.waitForResponse((response) =>
      match(response, `/tasks/${prepared.task_id}/progress`), { timeout: 30_000 });
    await card.getByRole('button', { name: '开始或继续' }).click();
    if ((await started).status() !== 200) throw new Error('S03 卡片启动失败');
  }
  await card.locator('#growth-answer').fill(a1);
  const first = page.waitForResponse((response) =>
    match(response, `/tasks/${prepared.task_id}/answers`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存回答' }).click();
  const firstResponse = await first;
  if (![200, 201].includes(firstResponse.status())) throw new Error('S03 A1 保存失败');
  evidence.first_attempt_id = (await firstResponse.json()).attempt_id;
  await card.locator('ul li').filter({ hasText: a1 }).first().waitFor({ timeout: 30_000 });
  await save();

  const hint = page.waitForResponse((response) =>
    match(response, `/tasks/${prepared.task_id}/hints`), { timeout: 30_000 });
  await card.getByRole('button', { name: '查看下一条提示' }).click();
  const hinted = await hint;
  if (![200, 201].includes(hinted.status())) throw new Error('S03 提示保存失败');
  evidence.hint = await hinted.json();
  await card.locator('ol li').filter({ hasText: evidence.hint.hint_text })
    .waitFor({ timeout: 30_000 });

  await card.locator('#growth-answer').fill(a2);
  await card.locator('#growth-parent-answer').selectOption(evidence.first_attempt_id);
  const second = page.waitForResponse((response) =>
    match(response, `/tasks/${prepared.task_id}/answers`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存回答' }).click();
  const secondResponse = await second;
  if (![200, 201].includes(secondResponse.status())) throw new Error('S03 A2 保存失败');
  evidence.second_attempt_id = (await secondResponse.json()).attempt_id;
  await card.locator('ul li').filter({ hasText: a2 }).first().waitFor({ timeout: 30_000 });
  const chain = await readChain();
  if (chain.attempts.length !== 2 ||
      chain.attempts[0].answer_text !== a1 ||
      chain.attempts[1].answer_text !== a2 ||
      chain.attempts[1].parent_attempt_id !== evidence.first_attempt_id ||
      chain.attempts[1].hint_level !== 1) {
    throw new Error('S03 A1/A2 原文、修正父链或提示级别不一致');
  }
  evidence.chain = chain;
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_ANSWERS_OBSERVED ${prepared.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
