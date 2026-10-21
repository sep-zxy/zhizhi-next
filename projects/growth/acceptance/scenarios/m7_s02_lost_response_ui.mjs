// Drop one installed-UI note creation response after server commit, then retry.
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, installer, workspace, credentialsPath, priorPath, outputPath,
  candidateSha256] = process.argv.slice(2);
if (!executable || !installer || !workspace || !credentialsPath || !priorPath
  || !outputPath || !/^[0-9a-f]{64}$/.test(candidateSha256 ?? '')) {
  throw new Error('用法：node m7_s02_lost_response_ui.mjs <exe> <installer> <workspace> <credentials> <prior.json> <output.json> <sha256>');
}
const hash = async (file) => {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(file)) digest.update(chunk);
  return digest.digest('hex');
};
if (await hash(installer) !== candidateSha256) throw new Error('安装包哈希不匹配');
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prior = JSON.parse(await readFile(priorPath, 'utf8'));
if (prior.status !== 'PASS') throw new Error('前一阶段未通过');
const text = prior.base_text;
const topicId = prior.topic_id;
const pathname = `/api/growth/local/cloud/topics/${topicId}/notes`;
const evidence = {
  status: 'running', mode: 'two_windows_devices_local_cloud_rehearsal',
  candidate_sha256: candidateSha256, installed_exe_sha256: await hash(executable),
  account_id: credentials.account_id, topic_id: topicId, text,
  started_at: new Date().toISOString(), checks: {},
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
let page;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-lost-response')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  await page.waitForURL(/#\/growth\?view=all$/, { timeout: 30_000 });
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = sync.locator('details').first();
  if (!await details.evaluate((element) => element.open)) {
    await details.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(`http://127.0.0.1:${credentials.port}`);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 90_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  if ((await syncResponse).status() !== 200) throw new Error('连接隔离云失败');
  await page.locator(`#growth-topic-select option[value="${topicId}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await page.locator('#growth-topic-select').selectOption(topicId);
  if (prior.task_id) {
    await page.locator(`#growth-topic-note-task option[value="${prior.task_id}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-note-task').selectOption(prior.task_id);
  }
  await page.locator('#growth-topic-note').fill(text);
  const button = page.getByRole('button', { name: '保存笔记', exact: true });
  await page.route(`**${pathname}`, async (route) => {
    const body = route.request().postDataJSON();
    evidence.first_request = {
      operation_id: body.operation_id, note_id: body.note_id,
      content_text: body.content_text,
    };
    const committed = await route.fetch();
    evidence.committed_status = committed.status();
    evidence.committed_result = await committed.json();
    await save();
    await route.abort('failed');
  }, { times: 1 });
  await button.click();
  await page.waitForFunction(() => {
    const item = [...document.querySelectorAll('button')].find(
      (candidate) => candidate.textContent?.trim() === '保存笔记',
    );
    return item instanceof HTMLButtonElement && !item.disabled;
  }, null, { timeout: 30_000 });
  evidence.text_after_lost_response = await page.locator('#growth-topic-note').inputValue();
  const retryResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === pathname, { timeout: 60_000 });
  const retryRequest = page.waitForRequest((request) =>
    new URL(request.url()).pathname === pathname, { timeout: 60_000 });
  await button.click();
  const second = await retryRequest;
  const secondBody = second.postDataJSON();
  evidence.retry_request = {
    operation_id: secondBody.operation_id, note_id: secondBody.note_id,
    content_text: secondBody.content_text,
  };
  const retried = await retryResponse;
  evidence.retry_status = retried.status();
  evidence.retry_result = await retried.json();
  await page.locator('.growth-page__originals pre').filter({ hasText: text })
    .first().waitFor({ timeout: 30_000 });
  evidence.checks = {
    server_committed_before_response_drop: evidence.committed_status === 201,
    input_survives_lost_response: evidence.text_after_lost_response === text,
    identical_operation_and_note_id: evidence.first_request.operation_id
      === evidence.retry_request.operation_id
      && evidence.first_request.note_id === evidence.retry_request.note_id
      && evidence.first_request.content_text === evidence.retry_request.content_text,
    identical_server_result: evidence.retry_status === 201
      && ['note_id', 'revision', 'change_seq', 'trace_id'].every((key) =>
        evidence.committed_result[key] === evidence.retry_result[key]),
  };
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('响应丢失重试未复用原 ID');
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write('S02_LOST_RESPONSE_UI_PASS\n');
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.message : String(error);
  if (page) {
    evidence.page_url = page.url();
    evidence.page_text = (await page.locator('body').innerText().catch(() => '')).slice(0, 3000);
    await page.screenshot({ path: outputPath.replace(/\.json$/, '.png') }).catch(() => {});
  }
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
