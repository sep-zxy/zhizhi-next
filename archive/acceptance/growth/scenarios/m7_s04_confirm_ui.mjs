// Observe installed S04-03 confirmation and the idempotent retry after restart.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, sessionId, suggestionId, outputPath,
  ...flags] = process.argv.slice(2);
const titleFlag = flags.find((flag) => flag.startsWith('--suggestion-title='));
const suggestionTitle = titleFlag?.slice('--suggestion-title='.length) || '';
if (!executable || !workspace || !credentialsPath || !sessionId || !suggestionId
  || !outputPath || flags.some((flag) => flag !== '--replay' && flag !== titleFlag)) {
  throw new Error('用法：node m7_s04_confirm_ui.mjs <exe> <workspace> <credentials.json> '
    + '<session-id> <suggestion-id> <output.json> [--replay] [--suggestion-title=标题]');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (!credentials.access_token || !credentials.account_id || !/^https?:\/\//.test(cloudUrl)) {
  throw new Error('云端凭据不完整');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: flags.includes('--replay') ? 'replay_not_live' : 'live_observation',
  status: 'running',
  platform: 'win32-x64',
  executable_sha256: hash.digest('hex'),
  workspace,
  account_id: credentials.account_id,
  cloud_origin: new URL(cloudUrl).origin,
  session_id: sessionId,
  suggestion_id: suggestionId,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
let electron;

async function openInstalledApp() {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  await sync.locator('form:has(#growth-cloud-url) button').click();
  await sync.locator('details > p[role="status"]').waitFor({ timeout: 120_000 });
  const chat = sync.getByRole('heading', { name: '成长对话' }).locator('..');
  await chat.locator(`#growth-chat-session option[value="${sessionId}"]`).waitFor({
    state: 'attached', timeout: 30_000,
  });
  await page.locator('#growth-chat-session').selectOption(sessionId);
  await page.locator('#growth-topic-select option').first().waitFor({
    state: 'attached', timeout: 30_000,
  });
  return { page, chat };
}

const topics = (page) => page.locator('#growth-topic-select option').evaluateAll(
  (options) => options.map((option) => ({ id: option.value, title: option.textContent })),
);

try {
  let { page, chat } = await openInstalledApp();
  evidence.topics_before = await topics(page);
  if (evidence.topics_before.length !== 1) throw new Error('确认前正式主题不是唯一旧主题');
  const selectedSuggestion = suggestionTitle
    ? chat.locator('p').filter({ hasText: suggestionTitle }).locator('..') : chat;
  const confirm = selectedSuggestion.getByRole('button', { name: '确认并创建主题' });
  await confirm.waitFor({ timeout: 30_000 });
  const responsePromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/suggestions/${suggestionId}/decision`),
  { timeout: 30_000 });
  await confirm.click();
  const response = await responsePromise;
  evidence.first_http_status = response.status();
  evidence.first_receipt = await response.json();
  if (response.status() !== 200 || evidence.first_receipt.status !== 'confirmed'
    || !evidence.first_receipt.topic_id) throw new Error('首次确认未获云端确认');
  await confirm.waitFor({ state: 'detached', timeout: 30_000 });
  await page.waitForFunction((topicId) => {
    const options = [...document.querySelectorAll('#growth-topic-select option')];
    return options.length === 2 && options.some((option) => option.value === topicId);
  }, evidence.first_receipt.topic_id, { timeout: 10_000 }).catch(() => {});
  evidence.topics_after_ui = await topics(page);
  await save();
  if (evidence.topics_after_ui.length !== 2
    || !evidence.topics_after_ui.some((item) => item.id === evidence.first_receipt.topic_id)) {
    throw new Error('确认后主题列表没有立即显示新主题');
  }
  if (suggestionTitle) {
    evidence.confirmed_suggestion_title = suggestionTitle;
    await page.locator('#growth-topic-select').selectOption(evidence.first_receipt.topic_id);
    await page.locator('#growth-topic-rename').fill('Web 认证与授权');
    const renamePromise = page.waitForResponse((item) =>
      new URL(item.url()).pathname.endsWith(
        `/topics/${evidence.first_receipt.topic_id}/update`), { timeout: 30_000 });
    await page.getByRole('button', { name: '重命名主题' }).click();
    const renamed = await renamePromise;
    evidence.rename_http_status = renamed.status();
    evidence.rename_receipt = await renamed.json();
    if (renamed.status() !== 200) throw new Error('主题重命名未获云端确认');
    await page.waitForFunction((topicId) => {
      const option = document.querySelector(`#growth-topic-select option[value="${topicId}"]`);
      return option?.textContent?.startsWith('Web 认证与授权 · r2');
    }, evidence.first_receipt.topic_id, { timeout: 30_000 });
    evidence.topics_after_rename = await topics(page);
  }

  await electron.close();
  electron = null;
  ({ page, chat } = await openInstalledApp());
  evidence.topics_after_restart = await topics(page);
  const remainingSelectedConfirm = suggestionTitle
    ? chat.locator('p').filter({ hasText: suggestionTitle }).locator('..')
      .getByRole('button', { name: '确认并创建主题' }).count()
    : chat.getByRole('button', { name: '确认并创建主题' }).count();
  if (evidence.topics_after_restart.length !== 2
    || await remainingSelectedConfirm !== 0
    || (suggestionTitle && !evidence.topics_after_restart.some((item) =>
      item.id === evidence.first_receipt.topic_id
        && item.title.startsWith('Web 认证与授权 · r2')))) {
    throw new Error('重启后主题或建议状态不一致');
  }
  const origin = new URL(page.url()).origin;
  const auth = await page.request.get(`${origin}/api/auth/token`, {
    headers: { Origin: origin },
  });
  if (auth.status() !== 200) throw new Error('无法取得本机写入令牌');
  const token = (await auth.json()).token;
  const retry = await page.request.post(
    `${origin}/api/growth/local/cloud/chats/${sessionId}/suggestions/${suggestionId}/decision`,
    {
      headers: { Origin: origin, 'X-AhaDiff-Token': token },
      data: {
        cloud_url: cloudUrl,
        access_token: credentials.access_token,
        account_id: credentials.account_id,
        decision: 'confirm',
      },
    },
  );
  evidence.retry_http_status = retry.status();
  evidence.retry_receipt = await retry.json();
  if (retry.status() !== 200
    || evidence.retry_receipt.topic_id !== evidence.first_receipt.topic_id) {
    throw new Error('重启后的同一确认重试没有返回原主题');
  }
  evidence.status = 'observed_pending_database_and_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_CONFIRM_OBSERVED ${evidence.mode} ${evidence.first_receipt.topic_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
