// Exercise the deleted-note recovery branch with two installed Windows devices.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [phase, executable, workspace, credentialsPath, priorPath, outputPath] =
  process.argv.slice(2);
if (!['create', 'bootstrap', 'offline-edit', 'delete', 'confirm-delete', 'recover',
  'confirm-recover', 'verify-topic'].includes(phase)
  || !executable || !workspace || !credentialsPath || !priorPath || !outputPath) {
  throw new Error('用法：node m7_s04_delete_ui.mjs <create|bootstrap|offline-edit|delete|confirm-delete|recover|confirm-recover|verify-topic> <exe> <workspace> <credentials.json> <prior.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prior = JSON.parse(await readFile(priorPath, 'utf8'));
if (prior.status !== 'PASS' && !(phase === 'confirm-delete'
  && prior.phase === 'delete' && prior.sync?.uploaded === 1
  && prior.checks?.delete_queued === true)
  && !(phase === 'confirm-recover' && prior.phase === 'recover'
    && prior.checks?.offline_original_in_recovery === true
    && prior.checks?.pending_operation_suppressed === true)) {
  throw new Error('前置步骤未通过');
}
const tempText = '有了 JWT 就不需要资源权限检查。';
const offlineText = `${tempText}待核实。`;
const newNoteText = '我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。';
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const evidence = {
  status: 'running', phase, mode: 'two_windows_devices_local_cloud_rehearsal',
  started_at: new Date().toISOString(), workspace,
  executable_sha256: executableHash.digest('hex'),
  account_id: credentials.account_id,
  temp_note_text: tempText, offline_text: offlineText,
  temp_note_id: prior.temp_note_id ?? null,
  actions: [], checks: {},
};
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const profile = path.join(path.dirname(outputPath), `electron-${phase}`);
await mkdir(profile, { recursive: true });
await save();
let electron;
try {
  if (phase === 'offline-edit') {
    let available = false;
    try {
      const response = await fetch(`${cloudUrl}/v1/account`, {
        signal: AbortSignal.timeout(2000),
      });
      available = response.status > 0;
    } catch { /* A refused connection proves this local cloud is stopped. */ }
    evidence.cloud_available_during_edit = available;
    if (available) throw new Error('离线编辑时云端仍可连接');
  }
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
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
  const topicPanel = page.locator('#growth-topic-create').locator('..');
  const notePanel = page.locator('#growth-synced-note').locator('..');
  const noteValue = () => `${credentials.account_id}:${evidence.temp_note_id}`;
  const clickRequest = async (button, pathname, status) => {
    const promise = page.waitForResponse((response) =>
      new URL(response.url()).pathname === pathname, { timeout: 60_000 });
    await button.click();
    const response = await promise;
    const body = await response.json();
    evidence.actions.push({ path: pathname, status: response.status(), body });
    await save();
    if (response.status() !== status) {
      throw new Error(`${pathname}: ${response.status()} ${JSON.stringify(body)}`);
    }
    return body;
  };
  const connect = async () => {
    await page.locator('#growth-cloud-url').fill(cloudUrl);
    await page.locator('#growth-cloud-token').fill(credentials.access_token);
    const response = await clickRequest(
      sync.locator('form:has(#growth-cloud-url) button'),
      '/api/growth/local/sync', 200,
    );
    evidence.device_id = response.device_id;
    evidence.sync = response;
    return response;
  };
  const selectNote = async (expectedText) => {
    await page.locator(`#growth-synced-note option[value="${noteValue()}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-synced-note').selectOption(noteValue());
    await notePanel.locator('.growth-page__originals pre').filter({ hasText: expectedText })
      .first().waitFor({ timeout: 30_000 });
  };
  const waitDeletedLabel = async () => {
    await page.waitForFunction((value) => {
      const option = [...document.querySelectorAll('#growth-synced-note option')]
        .find((item) => item.value === value);
      return option?.textContent?.includes('已删除') ?? false;
    }, noteValue(), { timeout: 30_000 });
  };
  if (phase === 'create') {
    await connect();
    evidence.topic_id = prior.new_topic_id;
    await page.locator(`#growth-topic-select option[value="${evidence.topic_id}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-select').selectOption(evidence.topic_id);
    await page.locator('#growth-topic-note').fill(tempText);
    const created = await clickRequest(topicPanel.getByRole('button', { name: '保存笔记' }),
      `/api/growth/local/cloud/topics/${evidence.topic_id}/notes`, 201);
    evidence.temp_note_id = created.note_id;
    await connect();
    await selectNote(tempText);
    evidence.checks = {
      temp_note_created: !!created.note_id,
      note_cached_on_device_a: await notePanel.locator('.growth-page__originals pre')
        .filter({ hasText: tempText }).count() > 0,
    };
  } else if (phase === 'bootstrap') {
    await connect();
    await selectNote(tempText);
    evidence.checks = {
      second_device_registered: !!evidence.device_id
        && evidence.device_id !== prior.device_id,
      temp_note_pulled: await notePanel.locator('.growth-page__originals pre')
        .filter({ hasText: tempText }).count() > 0,
    };
  } else if (phase === 'offline-edit') {
    await selectNote(tempText);
    await page.locator('#growth-sync-note-edit').fill(offlineText);
    const queued = await clickRequest(notePanel.getByRole('button', {
      name: '保存修订并等待同步',
    }), `/api/growth/local/sync/notes/${evidence.temp_note_id}/revisions`, 201);
    evidence.operation_id = queued.operation_id;
    evidence.device_id = prior.device_id;
    if (queued.state === 'pending') {
      await notePanel.getByText('本地版本状态：pending').waitFor({ timeout: 30_000 });
    }
    evidence.checks = {
      cloud_unreachable: evidence.cloud_available_during_edit === false,
      offline_operation_pending: queued.state === 'pending'
        && await notePanel.getByText('本地版本状态：pending').count() > 0,
      original_not_overwritten: await notePanel.locator('.growth-page__originals pre')
        .filter({ hasText: tempText }).count() > 0,
    };
  } else if (phase === 'delete') {
    await connect();
    await selectNote(tempText);
    await notePanel.getByLabel('我确认要删除此笔记并传播到其他设备').check();
    const queued = await clickRequest(notePanel.getByRole('button', {
      name: '将删除加入同步队列',
    }), `/api/growth/local/sync/notes/${evidence.temp_note_id}`, 201);
    evidence.operation_id = queued.operation_id;
    await connect();
    await selectNote(tempText);
    await waitDeletedLabel();
    const label = await page.locator(`#growth-synced-note option[value="${noteValue()}"]`)
      .textContent();
    evidence.checks = {
      delete_queued: queued.state === 'pending',
      tombstone_visible_on_device_a: label?.includes('已删除') ?? false,
    };
  } else if (phase === 'confirm-delete') {
    await connect();
    await selectNote(tempText);
    await waitDeletedLabel();
    evidence.operation_id = prior.operation_id;
    evidence.checks = {
      prior_delete_uploaded: prior.sync.uploaded === 1,
      tombstone_visible_on_device_a: await page.locator(
        `#growth-synced-note option[value="${noteValue()}"]`,
      ).textContent().then((value) => value?.includes('已删除') ?? false),
    };
  } else if (phase === 'verify-topic') {
    await connect();
    evidence.topic_id = prior.new_topic_id;
    await page.locator(`#growth-topic-select option[value="${prior.auth_topic_id}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-select').selectOption(prior.auth_topic_id);
    const notes = topicPanel.locator('.growth-page__originals pre');
    await notes.filter({ hasText: '登录成功说明应用识别了用户身份。' })
      .first().waitFor({ timeout: 30_000 });
    const authTexts = await notes.allTextContents();
    await page.locator('#growth-topic-select').selectOption(prior.permission_topic_id);
    await notes.filter({ hasText: newNoteText }).first().waitFor({ timeout: 30_000 });
    const permissionTexts = await notes.allTextContents();
    await page.locator('#growth-topic-select').selectOption(evidence.topic_id);
    await page.waitForTimeout(200);
    const parentTexts = await notes.allTextContents();
    evidence.visible_normal_note_texts = {
      auth: authTexts, permission: permissionTexts, parent: parentTexts,
    };
    evidence.checks = {
      old_note_visible: authTexts.includes('登录成功说明应用识别了用户身份。'),
      valid_new_note_visible: permissionTexts.includes(newNoteText),
      deleted_temp_not_normal_note: ![...authTexts, ...permissionTexts, ...parentTexts]
        .includes(tempText),
    };
  } else {
    const result = await connect();
    await selectNote(tempText);
    await waitDeletedLabel();
    const label = await page.locator(`#growth-synced-note option[value="${noteValue()}"]`)
      .textContent();
    const recovery = notePanel.locator('.growth-page__originals')
      .filter({ hasText: '已删除笔记的本地恢复副本' });
    await recovery.locator('pre').filter({ hasText: offlineText }).first()
      .waitFor({ timeout: 30_000 });
    evidence.checks = {
      tombstone_pulled: label?.includes('已删除') ?? false,
      pending_operation_suppressed: result.outbox.deleted >= 1
        && result.outbox.pending === 0,
      offline_original_in_recovery: await recovery.locator('pre')
        .filter({ hasText: offlineText }).count() > 0,
      valid_notes_still_visible: await page.locator('#growth-synced-note option').count() >= 3,
    };
  }
  evidence.status = Object.values(evidence.checks).every(Boolean) ? 'PASS' : 'FAIL';
  evidence.finished_at = new Date().toISOString();
  await save();
  if (evidence.status !== 'PASS') throw new Error(`离线删除断言失败：${JSON.stringify(evidence.checks)}`);
  process.stdout.write(`S04_DELETE_${phase.toUpperCase()}_PASS ${evidence.temp_note_id}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
