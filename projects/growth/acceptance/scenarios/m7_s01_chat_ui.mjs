// Installed S01 memory chat rehearsal against a local cloud; the provider may be real.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, preparationPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !preparationPath || !outputPath) {
  throw new Error('用法：node m7_s01_chat_ui.mjs <exe> <workspace> <credentials.json> <preparation.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const preparation = JSON.parse(await readFile(preparationPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (!credentials.access_token || credentials.account_id !== preparation.account_id ||
    !/^(?:localhost|127\.0\.0\.1|\[::1\])$/.test(new URL(cloudUrl).hostname)) {
  throw new Error('聊天预演仅接受来源账号的本机隔离云凭据');
}
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const question = preparation.question ?? '我对 CompletableFuture 修正过什么误解？';
const evidence = {
  mode: preparation.mode ?? 'replay_not_live', status: 'running',
  executable_sha256: executableHash.digest('hex'), workspace,
  account_id: credentials.account_id, cloud_origin: new URL(cloudUrl).origin,
  question, started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
const launch = () => _electron.launch({
  executablePath: executable,
  args: [`--user-data-dir=${profile}`],
  env: { ...process.env, GROWTH_WORKSPACE: workspace },
  timeout: 90_000,
});
let electron;
try {
  electron = await launch();
  let page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  const firstSync = page.locator('section[aria-labelledby="growth-sync-title"]');
  if (!await firstSync.locator('details').first().evaluate((element) => element.open)) {
    await firstSync.locator('details summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const syncResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await firstSync.locator('form:has(#growth-cloud-url) button').click();
  const synced = await syncResponse;
  if (synced.status() !== 200) throw new Error(`隔离账号同步失败：${synced.status()}`);
  evidence.sync = await synced.json();
  if (evidence.sync.account_id !== credentials.account_id || evidence.sync.outbox.pending !== 0) {
    throw new Error('同步账号或 outbox 不符合来源云账本');
  }
  await electron.close();
  electron = null;
  evidence.reopened_after_projection = true;
  await save();

  electron = await launch();
  page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  if (!await sync.locator('details').first().evaluate((element) => element.open)) {
    await sync.locator('details summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const secondSync = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  if ((await secondSync).status() !== 200) throw new Error('重启后云同步失败');
  const chat = sync.getByRole('heading', { name: '成长对话' }).locator('..');
  const startResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/chats/start'), { timeout: 30_000 });
  await chat.getByRole('button', { name: '新建对话' }).click();
  const started = await startResponse;
  if (started.status() !== 201) throw new Error(`新建对话失败：${started.status()}`);
  evidence.session_id = (await started.json()).session_id;
  await page.locator('#growth-chat-message').fill(question);
  const sendResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/messages`),
  { timeout: 30_000 });
  await chat.getByRole('button', { name: '发送', exact: true }).click();
  if ((await sendResponse).status() !== 201) throw new Error('用户问题未获云确认');
  await chat.locator('[aria-label="对话历史"] pre').filter({ hasText: question })
    .waitFor({ timeout: 30_000 });
  await page.locator('#growth-chat-provider').fill(preparation.provider_name);
  const previewResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/preview`),
  { timeout: 30_000 });
  await chat.getByRole('button', { name: '预览模型请求' }).click();
  const preview = await previewResponse;
  if (preview.status() !== 200) throw new Error(`模型预览失败：${preview.status()}`);
  const payload = await preview.json();
  const shown = await chat.locator('pre.growth-page__payload').innerText();
  if (shown !== payload.payload_text) throw new Error('界面预览与批准正文不一致');
  const dataStart = shown.lastIndexOf('{"conversation_truncated":');
  if (dataStart < 0) throw new Error('批准请求缺少结构化对话上下文');
  const memory = JSON.parse(shown.slice(dataStart)).memory;
  const noteIds = memory.notes.map((item) => item.note_id);
  const attemptIds = memory.attempts.map((item) => item.attempt_id);
  evidence.preview_observed = {
    memory_status: memory.memory_status, note_ids: noteIds,
    attempt_ids: attemptIds,
    historical: memory.attempts.map((item) => item.historical),
    expected_note_id: preparation.note_id,
    expected_attempt_ids: preparation.attempt_ids,
  };
  await save();
  if (memory.memory_status !== 'current' || !noteIds.includes(preparation.note_id) ||
      preparation.attempt_ids.some((id) => !attemptIds.includes(id)) ||
      memory.attempts.some((item) => item.historical !== true)) {
    throw new Error('批准请求未包含当前 N1/A1/A2 及历史回答标记');
  }
  evidence.preview = {
    payload_sha256: createHash('sha256').update(shown).digest('hex'),
    approval_hash: payload.approval_hash,
    memory_status: memory.memory_status, note_ids: noteIds, attempt_ids: attemptIds,
    historical_attempts: memory.attempts.every((item) => item.historical === true),
  };
  await save();
  await chat.getByLabel('我已检查完整内容，批准发送给此模型').check();
  let generated;
  const attempts = [];
  for (let retry = 0; retry < 3; retry++) {
    const generateResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/generate`),
    { timeout: 120_000 });
    await chat.getByRole('button', { name: '生成回复或重试上传' }).click();
    generated = await generateResponse;
    const submitted = generated.request().postDataJSON();
    attempts.push({ status: generated.status(), request_id: submitted.request_id,
      message_id: submitted.message_id });
    evidence.generate_attempts = attempts;
    await save();
    if (generated.status() === 200) break;
    if (generated.status() !== 502) throw new Error(`模型回复未获云确认：${generated.status()}`);
    await chat.getByRole('button', { name: '放弃本次请求并重新预览' }).click();
    const newPreview = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/preview`),
    { timeout: 30_000 });
    await chat.getByRole('button', { name: '预览模型请求' }).click();
    const refreshed = await newPreview;
    if (refreshed.status() !== 200
        || await chat.locator('pre.growth-page__payload').innerText() !== shown) {
      throw new Error('模型失败后重新批准的请求范围发生变化');
    }
    await chat.getByLabel('我已检查完整内容，批准发送给此模型').check();
    await page.waitForTimeout(2000 * (retry + 1));
  }
  if (generated.status() !== 200) throw new Error(`模型回复三次仍未获云确认：${generated.status()}`);
  if (new Set(attempts.map(item => item.request_id)).size !== attempts.length
      || new Set(attempts.map(item => item.message_id)).size !== attempts.length) {
    throw new Error('已失败模型请求重试没有使用新的请求和消息 ID');
  }
  const result = await generated.json();
  if (result.sync_state !== 'acked' || !result.message?.content_text) {
    throw new Error('模型回复缺少云确认或正文');
  }
  evidence.assistant = result.message;
  evidence.sync_state = result.sync_state;
  await chat.locator('[aria-label="对话历史"] pre')
    .filter({ hasText: result.message.content_text }).waitFor({ timeout: 30_000 });
  if (await chat.getByRole('button', { name: '确认并创建主题' }).count()
    && !result.message.suggestion) {
    throw new Error('界面出现了没有来源的主题确认按钮');
  }
  evidence.pending_suggestion = result.message.suggestion ?? null;
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_CHAT_OBSERVED ${evidence.mode} ${evidence.session_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
