// Exercise a due review through the installed desktop UI and isolated cloud.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, taskId, clock, outputPath, modeFlag] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !taskId || !clock || !outputPath
  || (modeFlag && modeFlag !== '--windows-local')) {
  throw new Error('用法：node m7_s01_review_ui.mjs <exe> <workspace> <credentials.json> <task-id> <clock> <output.json> [--windows-local]');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (!credentials.access_token || !credentials.account_id ||
  !/^(?:localhost|127\.0\.0\.1|\[::1\])$/.test(new URL(cloudUrl).hostname)) {
  throw new Error('复习演练仅接受完整的本机隔离云凭据');
}
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const evidence = {
  mode: modeFlag === '--windows-local' ? 'windows_local_live' : 'replay_not_live', status: 'running',
  executable_sha256: executableHash.digest('hex'), workspace,
  cloud_origin: new URL(cloudUrl).origin,
  account_id: credentials.account_id, task_id: taskId, learning_clock: clock,
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
    env: { ...process.env, GROWTH_WORKSPACE: workspace, GROWTH_ACCEPTANCE_MODE: '1' },
    timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  const reviews = page.locator('[aria-labelledby="growth-reviews-title"]');
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  evidence.page_url = page.url();
  try {
    await sync.waitFor({ timeout: 30_000 });
  } catch {
    throw new Error(`复习页未出现云同步入口：URL=${page.url()} headings=${JSON.stringify(
      await page.locator('h1,h2').allInnerTexts())} alerts=${JSON.stringify(
      await page.locator('[role="alert"]').allInnerTexts())}`);
  }
  if (!(await sync.locator('details').first().evaluate((element) => element.open))) {
    await sync.locator('details > summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const synced = await syncPromise;
  if (synced.status() !== 200) throw new Error(`隔离账号同步失败：${synced.status()} ${await synced.text()}`);
  const summary = await synced.json();
  if (summary.account_id !== credentials.account_id || summary.outbox.pending !== 0 ||
    summary.outbox.conflict !== 0 || summary.outbox.deleted !== 0) {
    throw new Error(`隔离账号同步摘要异常：${JSON.stringify(summary)}`);
  }
  evidence.sync = summary;
  await reviews.waitFor({ timeout: 30_000 });
  await reviews.getByText('当前没有到期卡片。').waitFor({ timeout: 30_000 });
  evidence.before_due = 'empty';
  await save();

  evidence.clock_injections = { due: 0, submit: 0 };
  await page.route('**/api/growth/local/cloud/reviews/due', async (route) => {
    evidence.clock_injections.due += 1;
    const response = await route.fetch({
      headers: { ...route.request().headers(), 'X-Learning-Clock': clock },
    });
    return route.fulfill({ response, headers: {
      ...response.headers(), 'X-Acceptance-Clock': 'injected',
    } });
  });
  await page.route('**/api/growth/local/cloud/tasks/*/reviews', (route) => {
    evidence.clock_injections.submit += 1;
    return route.continue({ headers: { ...route.request().headers(), 'X-Learning-Clock': clock } });
  });
  const duePromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/cloud/reviews/due'
    && response.headers()['x-acceptance-clock'] === 'injected',
  { timeout: 30_000 });
  await reviews.getByRole('button', { name: '刷新到期卡片' }).click();
  const dueResponse = await duePromise;
  if (dueResponse.status() !== 200) throw new Error(`读取到期卡片失败：${dueResponse.status()}`);
  const due = (await dueResponse.json()).due;
  const selected = due.find((item) => item.task_id === taskId);
  if (!selected || selected.revision !== 1 || !selected.question || due.length !== 1) {
    throw new Error(`到期卡片与 S01 任务不一致：${JSON.stringify(due)}`);
  }
  evidence.due = due;
  await reviews.locator('#growth-review-answer').waitFor({ timeout: 30_000 });
  if (!(await reviews.innerText()).includes(selected.question)) {
    throw new Error('到期复习 UI 未显示原题');
  }
  const answerText = '否，join 可能等待。';
  await reviews.locator('#growth-review-answer').fill(answerText);
  await reviews.getByRole('button', { name: '揭示答案' }).click();
  await reviews.getByText('参考答案', { exact: true }).waitFor({ timeout: 30_000 });
  await reviews.locator('#growth-review-hint').selectOption('0');
  await reviews.locator('#growth-review-rating').selectOption('hard');
  const reviewPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/growth/local/cloud/tasks/${taskId}/reviews`,
  { timeout: 30_000 });
  await reviews.getByRole('button', { name: '保存复习回答' }).click();
  const reviewed = await reviewPromise;
  const request = reviewed.request().postDataJSON();
  if (request.answer_text !== answerText || request.answer !== 'hard' ||
    request.hint_level !== 0 || request.base_revision !== 1 ||
    !request.operation_id || !request.review_id) {
    throw new Error('复习请求没有保留用户原文、审慎自评或幂等 ID');
  }
  if (reviewed.status() !== 200) {
    throw new Error(`复习提交失败：${reviewed.status()} ${await reviewed.text()}`);
  }
  const result = await reviewed.json();
  if (result.review_id !== request.review_id || result.revision !== 2 ||
    new Date(result.due_at) <= new Date(selected.due_at)) {
    throw new Error(`复习修订或下次到期时间异常：${JSON.stringify(result)}`);
  }
  evidence.submission = {
    operation_id: request.operation_id, review_id: request.review_id,
    base_revision: request.base_revision, answer_text: request.answer_text,
    answer: request.answer, hint_level: request.hint_level,
    response: result,
  };
  await reviews.getByRole('status').getByText('复习回答已保存，下次到期时间已更新。')
    .waitFor({ timeout: 30_000 });
  await reviews.getByText('当前没有到期卡片。').waitFor({ timeout: 30_000 });
  evidence.after_due = 'empty';
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_REVIEW_OBSERVED ${taskId} ${result.review_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
