// Exercise S04 merge, explicit split, archive/reactivation and export in the installed app.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, newTopicId, taskId, outputPath] =
  process.argv.slice(2);
if (![executable, workspace, credentialsPath, newTopicId, taskId, outputPath].every(Boolean)) {
  throw new Error('用法：node m7_s04_topic_maintenance_ui.mjs <exe> <workspace> <credentials.json> <new-topic> <task> <output.json>');
}
const oldNote = '登录成功说明应用识别了用户身份。';
const newNote = '我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。';
const answer = '先确认 actor 是否存在；actor 存在但与 document.ownerId 不同，新增的所有者条件会返回 403。身份识别只回答是谁，修改文档仍要检查该资源的授权。';
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = { mode: 'installed_windows_local_cloud_rehearsal', status: 'running',
  started_at: new Date().toISOString(), executable_sha256: hash.digest('hex'), workspace,
  account_id: credentials.account_id, old_topic_id: null, new_topic_id: newTopicId,
  old_note_id: null, new_note_id: null, task_id: taskId, actions: [] };
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-topic-maintenance-profile');
await mkdir(profile, { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${profile}`], env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  const panel = page.locator('#growth-topic-create').locator('..');
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncPending = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const syncResponse = await syncPending;
  if (syncResponse.status() !== 200) throw new Error(`云同步失败：${syncResponse.status()}`);
  evidence.actions.push({ action: 'sync', cursor: (await syncResponse.json()).cursor });
  const selectTopic = async (id, expectedText) => {
    await page.locator(`#growth-topic-select option[value="${id}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-select').selectOption(id);
    if (expectedText) await panel.locator('.growth-page__originals pre')
      .filter({ hasText: expectedText }).first().waitFor({ timeout: 30_000 });
  };
  const clickRequest = async (button, pathname, expectedStatus) => {
    const pending = page.waitForResponse((response) =>
      new URL(response.url()).pathname === pathname, { timeout: 60_000 });
    await button.click();
    const response = await pending;
    const body = await response.json();
    evidence.actions.push({ path: pathname, status: response.status(),
      body: pathname === '/api/growth/local/cloud/exports'
        ? { export_id: body.export_id } : body });
    await save();
    if (response.status() !== expectedStatus) {
      throw new Error(`${pathname}: ${response.status()} ${JSON.stringify(body)}`);
    }
    return body;
  };
  await page.locator(`#growth-synced-task option[value="${credentials.account_id}:${taskId}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await page.locator('#growth-synced-task')
    .selectOption(`${credentials.account_id}:${taskId}`);
  await page.locator('#growth-sync-task-answer').fill(answer);
  const queuedAnswer = await clickRequest(
    page.getByRole('button', { name: '提交离线正式回答' }),
    `/api/growth/local/sync/tasks/${taskId}/attempts`, 201,
  );
  evidence.answer_attempt_id = queuedAnswer.attempt_id;
  const answerSyncPending = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const answerSync = await answerSyncPending;
  if (answerSync.status() !== 200 || (await answerSync.json()).uploaded < 1) {
    throw new Error('A1 未上传到隔离云');
  }
  await selectTopic(newTopicId);
  await page.locator('#growth-topic-note-task').selectOption(taskId);
  await page.locator('#growth-topic-note').fill(newNote);
  const createdNewNote = await clickRequest(panel.getByRole('button', { name: '保存笔记' }),
    `/api/growth/local/cloud/topics/${newTopicId}/notes`, 201);
  evidence.new_note_id = createdNewNote.note_id;
  await page.locator('#growth-topic-create').fill('登录机制');
  const createdOldTopic = await clickRequest(panel.getByRole('button', { name: '创建主题' }),
    '/api/growth/local/cloud/topics/create', 201);
  evidence.old_topic_id = createdOldTopic.topic_id;
  await selectTopic(evidence.old_topic_id);
  await page.locator('#growth-topic-note').fill(oldNote);
  const createdOldNote = await clickRequest(panel.getByRole('button', { name: '保存笔记' }),
    `/api/growth/local/cloud/topics/${evidence.old_topic_id}/notes`, 201);
  evidence.old_note_id = createdOldNote.note_id;
  await save();
  const oldTopicId = evidence.old_topic_id;
  const oldNoteId = evidence.old_note_id;
  const newNoteId = evidence.new_note_id;
  await selectTopic(oldTopicId, oldNote);
  await page.locator('#growth-topic-target').selectOption(newTopicId);
  await panel.getByLabel('我已核对来源与目标，确认合并').check();
  evidence.merge = await clickRequest(panel.getByRole('button', { name: '执行合并' }),
    `/api/growth/local/cloud/topics/${oldTopicId}/merge`, 200);
  await panel.getByText('已合并到正式主题').waitFor({ timeout: 30_000 });
  await selectTopic(newTopicId, newNote);
  await panel.locator('.growth-page__originals pre').filter({ hasText: oldNote })
    .first().waitFor({ timeout: 30_000 });
  await page.locator('#growth-topic-child-a').fill('身份认证');
  await page.locator('#growth-topic-child-b').fill('资源授权');
  await panel.getByRole('combobox', { name: `关联位置 ${oldNote}` }).selectOption('a');
  await panel.getByRole('combobox', { name: `关联位置 ${newNote.slice(0, 80)}` })
    .selectOption('b');
  await panel.locator('select[aria-label^="关联位置"]').last().selectOption('b');
  evidence.split = await clickRequest(panel.getByRole('button', { name: '执行拆分' }),
    `/api/growth/local/cloud/topics/${newTopicId}/split`, 201);
  evidence.auth_topic_id = evidence.split.children.find((item) =>
    item.title === '身份认证')?.topic_id;
  evidence.permission_topic_id = evidence.split.children.find((item) =>
    item.title === '资源授权')?.topic_id;
  if (!evidence.auth_topic_id || !evidence.permission_topic_id) {
    throw new Error('拆分未返回指定子主题');
  }
  await selectTopic(evidence.auth_topic_id, oldNote);
  await selectTopic(evidence.permission_topic_id, newNote);
  evidence.archive = await clickRequest(panel.getByRole('button', { name: '归档主题' }),
    `/api/growth/local/cloud/topics/${evidence.permission_topic_id}/update`, 200);
  evidence.reactivate = await clickRequest(panel.getByRole('button', { name: '重新激活主题' }),
    `/api/growth/local/cloud/topics/${evidence.permission_topic_id}/update`, 200);
  const exported = await clickRequest(sync.getByRole('button', { name: '生成导出快照' }),
    '/api/growth/local/cloud/exports', 200);
  const jsonExport = JSON.parse(exported.json_text);
  const artifactDir = path.dirname(outputPath);
  await writeFile(path.join(artifactDir, 'growth-export-pre-delete.json'),
    `${JSON.stringify(jsonExport, null, 2)}\n`, 'utf8');
  await writeFile(path.join(artifactDir, 'growth-export-pre-delete.md'),
    exported.markdown_text, 'utf8');
  const notes = Object.fromEntries(jsonExport.tables.notes.map((item) => [item.note_id, item]));
  evidence.export_id = exported.export_id;
  evidence.checks = {
    old_alias: evidence.merge.target_topic_id === newTopicId,
    explicit_note_assignment: evidence.split.children.length === 2,
    same_child_reactivation: evidence.archive.topic_id === evidence.reactivate.topic_id
      && evidence.reactivate.topic_id === evidence.permission_topic_id
      && evidence.archive.status === 'archived' && evidence.reactivate.status === 'active',
    exported_original_notes: notes[oldNoteId]?.content_text === oldNote
      && notes[newNoteId]?.content_text === newNote,
    exported_answer_and_card: jsonExport.tables.attempts?.some((item) =>
      item.task_id === taskId && item.attempt_id === evidence.answer_attempt_id
      && item.answer_text === answer) && jsonExport.tables.tasks?.some((item) =>
      item.task_id === taskId),
  };
  evidence.status = Object.values(evidence.checks).every(Boolean) ? 'PASS' : 'FAIL';
  evidence.finished_at = new Date().toISOString();
  await save();
  if (evidence.status !== 'PASS') throw new Error(`主题维护断言失败：${JSON.stringify(evidence.checks)}`);
  process.stdout.write(`S04_TOPIC_MAINTENANCE_PASS ${evidence.auth_topic_id} ${evidence.permission_topic_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
