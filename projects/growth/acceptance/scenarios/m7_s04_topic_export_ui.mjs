// Exercise topic maintenance, export, and post-reinstall reading in the installed app.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [phase, executable, workspace, credentialsPath, setupPath, previousPath, outputPath] =
  process.argv.slice(2);
if (!['before', 'after'].includes(phase) || !executable || !workspace || !credentialsPath
  || !setupPath || !previousPath || !outputPath) {
  throw new Error('用法：node m7_s04_topic_export_ui.mjs <before|after> <exe> <workspace> <credentials.json> <setup.json> <previous.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const setup = JSON.parse(await readFile(setupPath, 'utf8'));
const previous = JSON.parse(await readFile(previousPath, 'utf8'));
const oldNote = setup.note_text ?? '登录成功说明应用识别了用户身份。';
const newNote = previous.new_note_text
  ?? '我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。';
const oldTopicId = setup.topic_id ?? previous.old_topic_id;
const oldNoteId = setup.note_id ?? previous.old_note_id;
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const evidence = {
  status: 'running', phase, mode: 'installed_windows_local_cloud_rehearsal',
  started_at: new Date().toISOString(), workspace,
  executable_sha256: executableHash.digest('hex'),
  account_id: setup.account_id, old_topic_id: oldTopicId,
  old_note_id: oldNoteId, old_note_text: oldNote,
  new_note_text: newNote, actions: [], checks: {},
};
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const profile = path.join(path.dirname(outputPath), `electron-${phase}`);
await mkdir(profile, { recursive: true });
await save();
let electron;
try {
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
  const panel = page.locator('#growth-topic-create').locator('..');
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const syncResponse = await syncPromise;
  if (syncResponse.status() !== 200) throw new Error(`云同步失败：${syncResponse.status()}`);
  evidence.actions.push({ action: 'sync', status: syncResponse.status(),
    cursor: (await syncResponse.json()).cursor });
  const topicOption = (id) => page.locator(`#growth-topic-select option[value="${id}"]`);
  const selectTopic = async (id, expectedText) => {
    await topicOption(id).waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-select').selectOption(id);
    if (expectedText) await panel.locator('.growth-page__originals pre')
      .filter({ hasText: expectedText }).first().waitFor({ timeout: 30_000 });
  };
  const clickRequest = async (button, pathname, expectedStatus) => {
    const promise = page.waitForResponse((response) =>
      new URL(response.url()).pathname === pathname, { timeout: 60_000 });
    await button.click();
    const response = await promise;
    const body = await response.json();
    evidence.actions.push({ action: pathname, status: response.status(),
      body: pathname === '/api/growth/local/cloud/exports'
        ? { export_id: body.export_id } : body });
    await save();
    if (response.status() !== expectedStatus) {
      throw new Error(`${pathname}: ${response.status()} ${JSON.stringify(body)}`);
    }
    return body;
  };
  if (phase === 'before') {
    evidence.event_id = previous.event_id;
    evidence.snapshot_id = previous.snapshot_id;
    await topicOption(setup.topic_id).waitFor({ state: 'attached', timeout: 30_000 });
    const initialTopics = await page.locator('#growth-topic-select option').count();
    if (initialTopics !== 1) throw new Error(`旧主题数量不为 1：${initialTopics}`);
    await page.locator('#growth-topic-create').fill('Web 认证与授权');
    const created = await clickRequest(panel.getByRole('button', { name: '创建主题' }),
      '/api/growth/local/cloud/topics/create', 201);
    evidence.new_topic_id = created.topic_id;
    await selectTopic(created.topic_id);
    await page.locator('#growth-topic-note').fill(newNote);
    const noted = await clickRequest(panel.getByRole('button', { name: '保存笔记' }),
      `/api/growth/local/cloud/topics/${created.topic_id}/notes`, 201);
    evidence.new_note_id = noted.note_id;
    await selectTopic(oldTopicId, oldNote);
    await page.locator('#growth-topic-target').selectOption(created.topic_id);
    await panel.getByLabel('我已核对来源与目标，确认合并').check();
    const merged = await clickRequest(panel.getByRole('button', { name: '执行合并' }),
      `/api/growth/local/cloud/topics/${setup.topic_id}/merge`, 200);
    evidence.merge = merged;
    await panel.getByText('已合并到正式主题').waitFor({ timeout: 30_000 });
    await selectTopic(created.topic_id, newNote);
    await panel.locator('.growth-page__originals pre').filter({ hasText: oldNote })
      .first().waitFor({ timeout: 30_000 });
    await page.locator('#growth-topic-child-a').fill('身份认证');
    await page.locator('#growth-topic-child-b').fill('资源授权');
    await panel.getByRole('combobox', { name: `关联位置 ${oldNote}` }).selectOption('a');
    await panel.getByRole('combobox', { name: `关联位置 ${newNote}` }).selectOption('b');
    const split = await clickRequest(panel.getByRole('button', { name: '执行拆分' }),
      `/api/growth/local/cloud/topics/${created.topic_id}/split`, 201);
    evidence.split = split;
    evidence.auth_topic_id = split.children.find((child) => child.title === '身份认证')?.topic_id;
    evidence.permission_topic_id = split.children.find((child) => child.title === '资源授权')?.topic_id;
    if (!evidence.auth_topic_id || !evidence.permission_topic_id) {
      throw new Error('拆分未返回两个预期子主题');
    }
    await selectTopic(evidence.auth_topic_id, oldNote);
    await selectTopic(evidence.permission_topic_id, newNote);
    const archived = await clickRequest(panel.getByRole('button', { name: '归档主题' }),
      `/api/growth/local/cloud/topics/${evidence.permission_topic_id}/update`, 200);
    const active = await clickRequest(panel.getByRole('button', { name: '重新激活主题' }),
      `/api/growth/local/cloud/topics/${evidence.permission_topic_id}/update`, 200);
    evidence.archive = archived;
    evidence.reactivate = active;
    const exported = await clickRequest(sync.getByRole('button', { name: '生成导出快照' }),
      '/api/growth/local/cloud/exports', 200);
    const exportJson = JSON.parse(exported.json_text);
    const exportDir = path.dirname(outputPath);
    await writeFile(path.join(exportDir, 'growth-export.json'),
      `${JSON.stringify(exportJson, null, 2)}\n`, 'utf8');
    await writeFile(path.join(exportDir, 'growth-export.md'), exported.markdown_text, 'utf8');
    const notes = Object.fromEntries(exportJson.tables.notes.map((item) => [item.note_id, item]));
    evidence.export_id = exported.export_id;
    evidence.export_json_sha256 = createHash('sha256').update(exported.json_text).digest('hex');
    evidence.export_markdown_sha256 = createHash('sha256')
      .update(exported.markdown_text).digest('hex');
    evidence.checks = {
      old_alias: merged.target_topic_id === created.topic_id,
      split_links: split.children.length === 2,
      archive_reactivate_same_id: archived.topic_id === active.topic_id
        && active.topic_id === evidence.permission_topic_id
        && archived.status === 'archived' && active.status === 'active',
      export_both_notes: notes[setup.note_id]?.content_text === oldNote
        && notes[noted.note_id]?.content_text === newNote,
      export_relationships: exportJson.tables.topic_aliases.some((item) =>
        item.alias_topic_id === setup.topic_id && item.canonical_topic_id === created.topic_id)
        && exportJson.tables.topic_links.length >= 2,
      export_markdown: exported.markdown_text.includes(oldNote)
        && exported.markdown_text.includes(newNote),
    };
  } else {
    if (previous.status !== 'PASS' || !previous.new_topic_id) {
      throw new Error('重装前主题证据不完整');
    }
    await selectTopic(oldTopicId, oldNote);
    const alias = await panel.getByText('已合并到正式主题').isVisible();
    await selectTopic(previous.auth_topic_id, oldNote);
    const oldReadable = await panel.locator('.growth-page__originals pre')
      .filter({ hasText: oldNote }).count() === 1;
    await selectTopic(previous.permission_topic_id, newNote);
    const newReadable = await panel.locator('.growth-page__originals pre')
      .filter({ hasText: newNote }).count() === 1;
    const permissionOption = await topicOption(previous.permission_topic_id).textContent();
    const eventId = previous.event_id;
    let captureVisible = false;
    if (eventId) {
      await page.locator(`#growth-event-select option[value="${eventId}"]`)
        .waitFor({ state: 'attached', timeout: 30_000 });
      await page.locator('#growth-event-select').selectOption(eventId);
      captureVisible = await sync.getByText(previous.snapshot_id).count() > 0;
    }
    const exported = await clickRequest(sync.getByRole('button', { name: '生成导出快照' }),
      '/api/growth/local/cloud/exports', 200);
    const artifactDir = path.dirname(outputPath);
    await electron.evaluate(({ session }, saveDir) => {
      globalThis.__growthDownloadEvidence = [];
      session.defaultSession.on('will-download', (_event, item) => {
        const filename = item.getFilename();
        item.setSavePath(`${saveDir}\\${filename}`);
        const entry = { filename, state: 'started' };
        globalThis.__growthDownloadEvidence.push(entry);
        item.on('done', (_doneEvent, state) => { entry.state = state; });
      });
    }, artifactDir);
    const downloads = [];
    for (const label of ['保存 JSON', '保存 Markdown']) {
      await sync.getByRole('button', { name: label }).click();
      let observed;
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const events = await electron.evaluate(() => globalThis.__growthDownloadEvidence);
        observed = events[downloads.length];
        if (observed?.state === 'completed') break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      if (observed?.state !== 'completed') {
        throw new Error(`Electron 下载未完成：${label} ${JSON.stringify(observed)}`);
      }
      downloads.push({ filename: observed.filename,
        target: path.join(artifactDir, observed.filename) });
    }
    const downloadedJson = await readFile(downloads[0].target, 'utf8');
    const downloadedMarkdown = await readFile(downloads[1].target, 'utf8');
    evidence.event_id = eventId;
    evidence.snapshot_id = previous.snapshot_id;
    evidence.download_export_id = exported.export_id;
    evidence.download_filenames = downloads.map((item) => item.filename);
    evidence.download_json_sha256 = createHash('sha256').update(downloadedJson).digest('hex');
    evidence.download_markdown_sha256 = createHash('sha256')
      .update(downloadedMarkdown).digest('hex');
    evidence.new_topic_id = previous.new_topic_id;
    evidence.new_note_id = previous.new_note_id;
    evidence.auth_topic_id = previous.auth_topic_id;
    evidence.permission_topic_id = previous.permission_topic_id;
    evidence.checks = {
      alias_restored: alias, old_note_restored: oldReadable,
      new_note_restored: newReadable,
      active_topic_restored: permissionOption?.includes('active') ?? false,
      ...(eventId ? { event_capture_restored: captureVisible } : {}),
      installed_executable_unchanged: evidence.executable_sha256
        === previous.executable_sha256,
      json_download: downloads[0].filename === `growth-${exported.export_id}.json`
        && JSON.stringify(JSON.parse(downloadedJson))
        === JSON.stringify(JSON.parse(exported.json_text)),
      markdown_download: downloads[1].filename === `growth-${exported.export_id}.md`
        && downloadedMarkdown === exported.markdown_text,
    };
  }
  evidence.status = Object.values(evidence.checks).every(Boolean) ? 'PASS' : 'FAIL';
  evidence.finished_at = new Date().toISOString();
  await save();
  if (evidence.status !== 'PASS') throw new Error(`主题与导出断言失败：${JSON.stringify(evidence.checks)}`);
  process.stdout.write(`S04_TOPIC_EXPORT_${phase.toUpperCase()}_PASS\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
