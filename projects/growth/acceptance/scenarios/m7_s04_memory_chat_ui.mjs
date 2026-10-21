// Reopen the installed S04 app and ask the exact post-deletion memory question.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, runDir, credentialsPath, outputPath, sourceMode] = process.argv.slice(2);
if (!executable || !runDir || !credentialsPath || !outputPath) {
  throw new Error('用法：node m7_s04_memory_chat_ui.mjs <exe> <run-dir> <credentials.json> <output.json>');
}
const formalSource = sourceMode === '--formal-source';
const windowsLive = sourceMode === '--windows-live';
const run = path.resolve(runDir);
const workspace = path.join(run, 'fixture', 'repo');
const artifacts = path.join(run, 'artifacts');
const [credentials, answer, created, setup] = await Promise.all([
  readFile(credentialsPath, 'utf8').then(JSON.parse),
  (formalSource
    ? readFile(path.join(artifacts, 's04-07-answer-note-finish-ui.json'), 'utf8')
      .catch(() => readFile(path.join(artifacts, 's04-07-answer-note-live-ui.json'), 'utf8'))
    : readFile(path.join(artifacts, 'answer-note-ui-resume.json'), 'utf8')
      .catch(() => readFile(path.join(artifacts, 'answer-note-ui.json'), 'utf8'))).then(JSON.parse),
  readFile(path.join(artifacts, formalSource
    ? 's04-12-windows-temp-create.json'
    : windowsLive ? 'delete-create.json' : 'temp-create.json'), 'utf8').then(JSON.parse),
  readFile(path.join(artifacts, formalSource
    ? 's04-13-export-setup.json'
    : windowsLive ? 'initial-ui.json' : 'cloned-s04-setup.json'), 'utf8').then(JSON.parse),
]);
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (credentials.account_id !== setup.account_id ||
    !/^(?:localhost|127\.0\.0\.1|\[::1\])$/.test(new URL(cloudUrl).hostname)) {
  throw new Error('记忆对话预演需要同账号本机隔离云');
}
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const question = '我现在怎样区分登录和权限？';
const evidence = {
  mode: formalSource || windowsLive ? 'live_model_windows_local_cloud_rehearsal_not_formal'
    : 'replay_not_live', status: 'running', started_at: new Date().toISOString(),
  executable_sha256: executableHash.digest('hex'), workspace,
  account_id: credentials.account_id, question,
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
const profile = path.join(artifacts, 'electron-memory-chat');
await mkdir(profile, { recursive: true });
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable, args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
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
  const panel = page.locator('#growth-topic-create').locator('..');
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncWait = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const syncResponse = await syncWait;
  if (syncResponse.status() !== 200) throw new Error('重装后同步失败');
  evidence.sync = await syncResponse.json();
  await page.locator('#growth-topic-select').selectOption(
    (await readFile(path.join(artifacts, formalSource
      ? 's04-09-11-windows-topic-rehearsal.json' : 'topic-maintenance-ui.json'), 'utf8')
      .then(JSON.parse)).permission_topic_id,
  );
  await panel.locator('.growth-page__originals pre')
    .filter({ hasText: answer.note_text }).first().waitFor({ timeout: 30_000 });
  evidence.reopened_note_exact = true;
  const chat = sync.getByRole('heading', { name: '成长对话' }).locator('..');
  const startedWait = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/chats/start'), { timeout: 30_000 });
  await chat.getByRole('button', { name: '新建对话' }).click();
  const started = await startedWait;
  if (started.status() !== 201) throw new Error('新建会话失败');
  evidence.session_id = (await started.json()).session_id;
  await page.locator('#growth-chat-message').fill(question);
  const sentWait = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/messages`),
  { timeout: 30_000 });
  await chat.getByRole('button', { name: '发送', exact: true }).click();
  if ((await sentWait).status() !== 201) throw new Error('用户问题未获云确认');
  await chat.locator('[aria-label="对话历史"] pre').filter({ hasText: question })
    .waitFor({ timeout: 30_000 });
  await page.locator('#growth-chat-provider').fill(formalSource || windowsLive ? 'deepseek' : 'local');
  const previewWait = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/preview`),
  { timeout: 30_000 });
  await chat.getByRole('button', { name: '预览模型请求' }).click();
  const previewResponse = await previewWait;
  if (previewResponse.status() !== 200) throw new Error('记忆请求预览失败');
  const preview = await previewResponse.json();
  const shown = await chat.locator('pre.growth-page__payload').innerText();
  if (shown !== preview.payload_text) throw new Error('界面预览正文不一致');
  const contextStart = shown.lastIndexOf('{"conversation_truncated":');
  if (contextStart < 0) throw new Error('预览正文缺少结构化会话上下文');
  const memory = JSON.parse(shown.slice(contextStart)).memory;
  const noteIds = memory.notes.map((item) => item.note_id);
  const attemptIds = memory.attempts.map((item) => item.attempt_id);
  if (memory.memory_status !== 'current' || !noteIds.includes(answer.note_id) ||
      !attemptIds.includes(answer.attempt_id) || noteIds.includes(created.temp_note_id) ||
      !memory.attempts.every((item) => item.historical === true)) {
    throw new Error('批准前上下文缺少 N1/A1，或包含 N-temp');
  }
  evidence.preview = {
    payload_sha256: createHash('sha256').update(shown).digest('hex'),
    approval_hash: preview.approval_hash,
    memory_status: memory.memory_status, note_ids: noteIds, attempt_ids: attemptIds,
    historical_attempts: true,
  };
  await save();
  await chat.getByLabel('我已检查完整内容，批准发送给此模型').check();
  const generatedWait = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/generate`),
  { timeout: 180_000 });
  await chat.getByRole('button', { name: '生成回复或重试上传' }).click();
  const generated = await generatedWait;
  if (generated.status() !== 200) throw new Error(`模型回复失败：${generated.status()}`);
  const result = await generated.json();
  if (result.sync_state !== 'acked' || !result.message?.content_text) {
    throw new Error('模型回复未获云确认');
  }
  evidence.assistant = result.message;
  evidence.sync_state = result.sync_state;
  await chat.locator('[aria-label="对话历史"] pre')
    .filter({ hasText: result.message.content_text }).waitFor({ timeout: 30_000 });
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_MEMORY_CHAT_OBSERVED ${evidence.session_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
