// Observe an installed Windows app receiving and capturing a targeted event.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, eventPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !eventPath || !outputPath) {
  throw new Error('用法：node m7_s04_event_ui.mjs <exe> <workspace> <credentials.json> <event.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const event = JSON.parse(await readFile(eventPath, 'utf8'));
if (event.status !== 'PASS') throw new Error('外部事件提交尚未通过');
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const evidence = {
  status: 'running',
  mode: 'installed_windows_local_cloud_rehearsal',
  started_at: new Date().toISOString(),
  executable_sha256: executableHash.digest('hex'),
  workspace,
  event_id: event.event_id,
  expected_head_sha: event.head_sha,
  target_device_id: event.device_id,
  binding_id: event.binding_id,
  account_id: event.account_id,
  responses: [],
};
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
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
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  await page.waitForURL(/#\/growth\?view=all$/, { timeout: 30_000 });
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const syncResponse = await syncPromise;
  evidence.responses.push({ path: '/api/growth/local/sync', status: syncResponse.status(),
    body: await syncResponse.json() });
  if (syncResponse.status() !== 200) throw new Error('安装版云同步失败');
  const option = page.locator(`#growth-event-select option[value="${event.event_id}"]`);
  await option.waitFor({ state: 'attached', timeout: 30_000 });
  await page.locator('#growth-event-select').selectOption(event.event_id);
  await page.waitForFunction((head) => {
    const select = document.querySelector('#growth-event-select');
    const section = select?.parentElement;
    return select instanceof HTMLSelectElement && !!section
      && section.textContent?.includes(head);
  }, event.head_sha, { timeout: 30_000 });
  await page.locator('#growth-event-binding').selectOption(event.binding_id);
  const binding = await page.locator('#growth-event-binding').inputValue();
  if (binding !== event.binding_id) throw new Error(`事件绑定不匹配：${binding}`);
  const captureButton = sync.getByRole('button', { name: '核对并捕获事件代码' });
  const captures = [];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const responsePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname
        === `/api/growth/local/sync/events/${event.event_id}/capture`,
    { timeout: 60_000 });
    await captureButton.click();
    const response = await responsePromise;
    const body = await response.json();
    captures.push({ status: response.status(), body });
    evidence.responses.push({ path: new URL(response.url()).pathname,
      status: response.status(), body });
    await save();
    if (attempt === 0) {
      await page.getByText('代码快照已捕获。请在下方检查模型请求并批准一次关联分析。')
        .waitFor({ timeout: 30_000 });
    }
  }
  const [first, repeated] = captures;
  evidence.checks = {
    expected_head_visible: await page.locator('#growth-event-select').inputValue()
      === event.event_id,
    first_capture: first.status === 201 && first.body.reused === false
      && first.body.head_sha === event.head_sha && !!first.body.snapshot_id,
    second_capture_reused: repeated.status === 200 && repeated.body.reused === true
      && repeated.body.snapshot_id === first.body.snapshot_id,
    snapshot_selected: await page.locator('#growth-snapshot-select').inputValue()
      === first.body.snapshot_id,
  };
  evidence.snapshot_id = first.body.snapshot_id;
  evidence.status = Object.values(evidence.checks).every(Boolean) ? 'PASS' : 'FAIL';
  evidence.finished_at = new Date().toISOString();
  await save();
  if (evidence.status !== 'PASS') throw new Error(`事件捕获断言失败：${JSON.stringify(evidence.checks)}`);
  process.stdout.write(`S04_EVENT_UI_PASS ${event.event_id} ${evidence.snapshot_id}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
