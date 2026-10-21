// Finish S04's live feedback attempt in the installed app after a driver interruption.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, taskId, priorPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !taskId || !priorPath || !outputPath) {
  throw new Error('用法：node m7_s04_finish_answer_note_ui.mjs <exe> <workspace> '
    + '<credentials.json> <task-id> <prior-ui.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prior = JSON.parse(await readFile(priorPath, 'utf8'));
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const A1 = '不可以。登录只说明 Bob 的身份，canEdit 还要求 user.id 等于 doc.ownerId，Bob 不满足，所以会得到 FORBIDDEN。';
const N1 = '我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。';
const evidence = {
  mode: 'live_observation', status: 'running', started_at: new Date().toISOString(),
  executable_sha256: hash.digest('hex'), workspace, account_id: credentials.account_id,
  task_id: taskId, attempt_id: prior.attempt_id, answer_text: A1, note_text: N1,
  prior_attempt: path.basename(priorPath),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const profile = path.join(path.dirname(outputPath), 'electron-answer-note-finish-profile');
await mkdir(profile, { recursive: true });
await save();
let electron;
try {
  if (prior.mode !== 'live_observation' || prior.workspace !== workspace
    || prior.account_id !== credentials.account_id || prior.task_id !== taskId
    || prior.executable_sha256 !== evidence.executable_sha256
    || prior.answer_text !== A1 || !prior.preview?.approval_hash) {
    throw new Error('原回答和模型批准记录与同一安装版、账号或卡片不匹配');
  }
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${profile}`], env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth`);
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地凭据失败：${response.status}`);
    return (await response.json()).token;
  });
  const headers = { 'X-AhaDiff-Token': token };
  const readChain = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/tasks/${taskId}`, { headers });
    if (response.status() !== 200) throw new Error(`卡片链读取失败：${response.status()}`);
    return response.json();
  };
  const initial = await readChain();
  const attempt = initial.attempts.find((item) => item.attempt_id === prior.attempt_id);
  if (initial.task.task_id !== taskId || initial.notes.length !== 0
    || initial.attempts.length !== 1 || attempt?.status !== 'feedback_ready'
    || attempt.answer_text !== A1) {
    throw new Error('原卡片回答或反馈未按预期持久化');
  }
  evidence.topic_id = initial.task.topic_id;
  evidence.source_refs = JSON.parse(initial.opportunity.source_refs_json);
  evidence.feedback = JSON.parse(attempt.feedback_json);
  const feedbackText = JSON.stringify(evidence.feedback);
  if (!/(?:认证|身份识别|已登录)/.test(feedbackText) || !feedbackText.includes('授权')
    || !feedbackText.includes('FORBIDDEN')) {
    throw new Error('真实反馈没有区分认证与授权');
  }
  const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const sync = async () => {
    const pending = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 90_000 });
    await page.locator('section[aria-labelledby="growth-sync-title"]')
      .locator('form:has(#growth-cloud-url) button').click();
    const response = await pending;
    if (response.status() !== 200) throw new Error(`云同步失败：${response.status()}`);
    return response.json();
  };
  evidence.initial_sync = await sync();
  const response = await page.request.get(`${origin}/api/growth/local`, { headers });
  if (response.status() !== 200) throw new Error(`本机状态失败：${response.status()}`);
  const state = await response.json();
  await page.locator('#growth-project-select').selectOption(state.projects[0].project_id);
  await page.locator('#growth-binding-select').selectOption(state.bindings[0].binding_id);
  await page.locator('#growth-snapshot-select').selectOption(state.snapshots[0].snapshot_id);
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: initial.opportunity.learning_goal });
  if (await row.count() !== 1) throw new Error('原卡片机会在界面中不唯一');
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  if (!(await card.innerText()).includes('Bob')) throw new Error('原授权卡片未展示');
  const progressPending = page.waitForResponse((item) =>
    new URL(item.url()).pathname.endsWith(`/tasks/${taskId}/progress`), { timeout: 30_000 });
  await card.getByRole('button', { name: '完成卡片' }).click();
  const progress = await progressPending;
  if (progress.status() !== 200 || (await progress.json()).status !== 'completed') {
    throw new Error('卡片完成状态未保存');
  }
  if (await card.locator('#growth-note').inputValue() !== '') {
    throw new Error('笔记输入框不是空白');
  }
  await card.locator('#growth-note').fill(N1);
  const notePending = page.waitForResponse((item) =>
    new URL(item.url()).pathname.endsWith(`/tasks/${taskId}/notes`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存笔记' }).click();
  const note = await notePending;
  if (![200, 201].includes(note.status())) throw new Error(`笔记保存失败：${note.status()}`);
  evidence.note_id = (await note.json()).note_id;
  evidence.final_sync = await sync();
  const final = await readChain();
  if (final.task.progress !== 'completed' || final.attempts.length !== 1
    || final.attempts[0].answer_text !== A1 || final.notes.length !== 1
    || final.notes[0].content_text !== N1 || final.notes[0].topic_id !== evidence.topic_id) {
    throw new Error('回答、完成状态和笔记未持久化');
  }
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_ANSWER_NOTE_FINISHED ${evidence.attempt_id} ${evidence.note_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
