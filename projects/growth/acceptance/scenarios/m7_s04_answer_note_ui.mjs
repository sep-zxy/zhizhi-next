// Observe installed S04 answer, feedback, completion, and hand-written note.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, taskId, outputPath, modeFlag, resumeFlag] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !taskId || !outputPath
  || (modeFlag && modeFlag !== '--live') || (resumeFlag && resumeFlag !== '--resume')) {
  throw new Error('用法：node m7_s04_answer_note_ui.mjs <exe> <workspace> '
    + '<credentials.json> <task-id> <output.json> [--live]');
}
const A1 = '不可以。登录只说明 Bob 的身份，canEdit 还要求 user.id 等于 doc.ownerId，Bob 不满足，所以会得到 FORBIDDEN。';
const N1 = '我以前把“已经登录”当成“可以操作资源”。文档案例里，识别 Bob 的身份以后，还要比较 Bob 和文档所有者。认证回答是谁，授权回答允许做什么。这个例子只补了所有者检查，没有实现 JWT 验签，也不能代表完整权限系统。';
const resume = resumeFlag === '--resume';
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = { mode: modeFlag === '--live' ? 'live_observation' : 'replay_not_live',
  status: 'running', started_at: new Date().toISOString(),
  executable_sha256: hash.digest('hex'), workspace, account_id: credentials.account_id,
  task_id: taskId, answer_text: A1, note_text: N1 };
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-answer-note-profile');
await mkdir(profile, { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const match = (response, suffix) => new URL(response.url()).pathname.endsWith(suffix);
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${profile}`], env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地凭据引导失败：${response.status}`);
    return (await response.json()).token;
  });
  const headers = { 'X-AhaDiff-Token': token };
  const readChain = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/tasks/${taskId}`, { headers });
    if (response.status() !== 200) throw new Error(`卡片链读取失败：${response.status()}`);
    return response.json();
  };
  const initial = await readChain();
  if (initial.task.task_id !== taskId || initial.notes.length
    || (resume ? initial.attempts.length !== 1 || initial.attempts[0].answer_text !== A1
      : initial.attempts.length !== 0)) {
    throw new Error('S04 卡片回答与笔记前置不为空');
  }
  evidence.topic_id = initial.task.topic_id;
  evidence.source_refs = JSON.parse(initial.opportunity.source_refs_json);
  const syncDetails = page.locator('section[aria-labelledby="growth-sync-title"] details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const sync = async () => {
    const pending = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
    await page.locator('section[aria-labelledby="growth-sync-title"]')
      .locator('form:has(#growth-cloud-url) button').click();
    const response = await pending;
    if (response.status() !== 200) throw new Error(`云同步失败：${response.status()}`);
    return response.json();
  };
  evidence.initial_sync = await sync();
  const stateResponse = await page.request.get(`${origin}/api/growth/local`, { headers });
  const state = await stateResponse.json();
  await page.locator('#growth-project-select').selectOption(state.projects[0].project_id);
  await page.locator('#growth-binding-select').selectOption(state.bindings[0].binding_id);
  await page.locator('#growth-snapshot-select').selectOption(state.snapshots[0].snapshot_id);
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: initial.opportunity.title });
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  if (!(await card.innerText()).includes('Bob')) throw new Error('卡片未展示 Bob 授权问题');
  if (!resume) await card.getByRole('button', { name: '开始或继续' }).click();
  await card.getByRole('button', { name: '暂停' }).waitFor({ timeout: 30_000 });
  if (resume) {
    evidence.attempt_id = initial.attempts[0].attempt_id;
  } else {
    await card.locator('#growth-answer').fill(A1);
    const answerPending = page.waitForResponse((response) =>
      match(response, `/tasks/${taskId}/answers`), { timeout: 30_000 });
    await card.getByRole('button', { name: '保存回答' }).click();
    const answer = await answerPending;
    if (![200, 201].includes(answer.status())) throw new Error(`回答保存失败：${answer.status()}`);
    evidence.attempt_id = (await answer.json()).attempt_id;
  }
  await card.locator('ul li').filter({ hasText: A1 }).first().waitFor({ timeout: 30_000 });
  await save();
  const attemptRow = card.locator('ul').first().locator('li').filter({ hasText: A1 });
  const previewPending = page.waitForResponse((response) =>
    match(response, `/attempts/${evidence.attempt_id}/feedback-preview`), { timeout: 30_000 });
  await attemptRow.getByRole('button', { name: '预览模型反馈请求' }).click();
  const previewResponse = await previewPending;
  if (previewResponse.status() !== 200) throw new Error(`反馈预览失败：${previewResponse.status()}`);
  const preview = await previewResponse.json();
  const shown = await card.locator('pre.growth-page__payload').last().innerText();
  if (shown !== preview.payload_text || !shown.includes(A1)
    || !shown.includes('access.ts')) throw new Error('反馈预览未按批准源码与回答构造');
  evidence.preview = { payload_sha256: createHash('sha256').update(shown).digest('hex'),
    approval_hash: preview.approval_hash, model_name: preview.model_name };
  await save();
  await card.getByLabel('我已核对上述内容，同意发送给所选 provider。').check();
  const feedbackPending = page.waitForResponse((response) =>
    match(response, `/attempts/${evidence.attempt_id}/feedback`), { timeout: 30_000 });
  await card.getByRole('button', { name: '发送并生成反馈' }).click();
  const feedbackResponse = await feedbackPending;
  if (![200, 202].includes(feedbackResponse.status())) {
    throw new Error(`反馈提交失败：${feedbackResponse.status()}`);
  }
  let chain;
  let attempt;
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    chain = await readChain();
    attempt = chain.attempts.find((item) => item.attempt_id === evidence.attempt_id);
    if (attempt?.status === 'feedback_ready' || attempt?.status === 'feedback_failed') break;
    await page.waitForTimeout(1000);
  }
  if (attempt?.status !== 'feedback_ready' || attempt.answer_text !== A1) {
    throw new Error(`反馈未成功或改写回答：${attempt?.status ?? 'timeout'}`);
  }
  evidence.feedback = JSON.parse(attempt.feedback_json);
  const feedbackText = JSON.stringify(evidence.feedback);
  if (!/(?:认证|身份识别|已登录)/.test(feedbackText) || !feedbackText.includes('授权')
    || !feedbackText.includes('FORBIDDEN')) {
    throw new Error('反馈没有区分认证与授权');
  }
  const progressPending = page.waitForResponse((response) =>
    match(response, `/tasks/${taskId}/progress`), { timeout: 30_000 });
  await card.getByRole('button', { name: '完成卡片' }).click();
  const progress = await progressPending;
  if (progress.status() !== 200 || (await progress.json()).status !== 'completed') {
    throw new Error('卡片完成状态未保存');
  }
  if (await card.locator('#growth-note').inputValue() !== '') {
    throw new Error('S04 笔记输入框应为空');
  }
  await card.locator('#growth-note').fill(N1);
  const notePending = page.waitForResponse((response) =>
    match(response, `/tasks/${taskId}/notes`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存笔记' }).click();
  const noteResponse = await notePending;
  if (![200, 201].includes(noteResponse.status())) throw new Error(`笔记保存失败：${noteResponse.status()}`);
  evidence.note_id = (await noteResponse.json()).note_id;
  evidence.final_sync = await sync();
  chain = await readChain();
  if (chain.task.progress !== 'completed' || chain.attempts.length !== 1
    || chain.attempts[0].answer_text !== A1 || chain.notes.length !== 1
    || chain.notes[0].content_text !== N1 || chain.notes[0].topic_id !== evidence.topic_id) {
    throw new Error('回答、反馈或手写笔记未持久化');
  }
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_ANSWER_NOTE_OBSERVED ${evidence.attempt_id} ${evidence.note_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
