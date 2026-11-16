// Installed S03 memory chat continuation. Keep local-cloud runs separate from formal Mac steps.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, preparationPath, outputPath, ...flags] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !preparationPath || !outputPath
    || flags.some((flag) => !['--live', '--read-only'].includes(flag))) {
  throw new Error('用法：node m7_s03_chat_ui.mjs <exe> <workspace> <credentials.json> <preparation.json> <output.json> [--live|--read-only]');
}
const live = flags.includes('--live');
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const preparation = JSON.parse(await readFile(preparationPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (!credentials.access_token || credentials.account_id !== preparation.account_id ||
    !/^(?:localhost|127\.0\.0\.1|\[::1\])$/.test(new URL(cloudUrl).hostname)) {
  throw new Error('聊天预演仅接受来源账号的本机隔离云凭据');
}
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const question = '订单计价先经过 PriceCache，再按需调用 PricingGateway。请结合我的 N1、A1、A2 解释 TTL 到期后是否会有后台线程自动刷新。';
const evidence = {
  mode: live ? 'live_observation' : 'replay_not_live', status: 'running',
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
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  const firstSync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const firstDetails = firstSync.locator('details').first();
  if (!await firstDetails.evaluate((element) => element.open)) {
    await firstDetails.locator('summary').first().click();
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
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const secondSync = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  if ((await secondSync).status() !== 200) throw new Error('重启后云同步失败');
  const n1 = preparation.note_text ?? '订单计价现在先经过 PriceCache，再按需要调用 PricingGateway。这个例子的 TTL 是访问时检查的：到期不会自动刷新，下一次 get 才重新加载。我不能只看到 expiresAt 就推断项目里存在定时清理线程。另外，这个 HashMap 实现是否适合并发使用，需要单独检查。';
  await page.locator('#growth-synced-note')
    .selectOption(`${preparation.account_id}:${preparation.note_id}`);
  await page.locator('.growth-page__originals pre')
    .getByText(n1, { exact: true }).waitFor({ timeout: 30_000 });
  evidence.reopened_note_exact = true;
  await save();
  if (flags.includes('--read-only')) {
    if (preparation.task_id && preparation.attempt_ids?.length) {
      await page.locator('#growth-synced-task')
        .selectOption(`${preparation.account_id}:${preparation.task_id}`);
      const token = await page.evaluate(async () =>
        (await (await fetch('/api/auth/token')).json()).token);
      const workspaceResponse = await page.request.get(
        `${new URL(page.url()).origin}/api/growth/local/sync/workspace`,
        { headers: { 'X-AhaDiff-Token': token } });
      if (workspaceResponse.status() !== 200) throw new Error('第二设备读取学习历史失败');
      const workspaceState = await workspaceResponse.json();
      const task = workspaceState.tasks.find((item) =>
        item.account_id === preparation.account_id && item.task_id === preparation.task_id);
      const actual = task?.attempts ?? [];
      if (preparation.attempt_ids.some((id) => !actual.some((item) => item.attempt_id === id))) {
        throw new Error('第二设备缺少 A1/A2 学习历史');
      }
      if (preparation.attempt_texts?.some((answer) =>
        !actual.some((item) => item.answer_text === answer))) {
        throw new Error('第二设备 A1/A2 原文不一致');
      }
      evidence.reopened_attempt_ids = actual.map((item) => item.attempt_id);
      evidence.reopened_attempt_texts_exact = true;
      await save();
    }
    evidence.status = 'observed_pending_audit';
    evidence.finished_at = new Date().toISOString();
    await save();
    process.stdout.write(`S03_N1_SECOND_DEVICE_OBSERVED ${preparation.note_id}\n`);
  } else {
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
  const contextStart = shown.lastIndexOf('{"conversation_truncated":');
  if (contextStart < 0) throw new Error('预览正文缺少结构化会话上下文');
  const memory = JSON.parse(shown.slice(contextStart)).memory;
  const noteIds = memory.notes.map((item) => item.note_id);
  const attemptIds = memory.attempts.map((item) => item.attempt_id);
  evidence.preview_observed = {
    memory_status: memory.memory_status,
    note_ids: noteIds,
    attempt_ids: attemptIds,
    historical_attempts: memory.attempts.map((item) => item.historical),
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
    provider_host: payload.provider_host,
    model_name: payload.model_name,
    memory_status: memory.memory_status, note_ids: noteIds, attempt_ids: attemptIds,
    historical_attempts: memory.attempts.every((item) => item.historical === true),
  };
  if (live && (payload.provider_host !== 'https://api.deepseek.com'
      || payload.model_name !== 'deepseek-flash')) {
    throw new Error('S03 live chat 未使用真实 DeepSeek provider');
  }
  await save();
  await chat.getByLabel('我已检查完整内容，批准发送给此模型').check();
  const generateResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/generate`),
  { timeout: 120_000 });
  await chat.getByRole('button', { name: '生成回复或重试上传' }).click();
  const generated = await generateResponse;
  if (generated.status() !== 200) throw new Error(`模型回复未获云确认：${generated.status()}`);
  const result = await generated.json();
  if (result.sync_state !== 'acked' || !result.message?.content_text) {
    throw new Error('模型回复缺少云确认或正文');
  }
  evidence.assistant = result.message;
  evidence.sync_state = result.sync_state;
  await chat.locator('[aria-label="对话历史"] pre')
    .filter({ hasText: result.message.content_text }).waitFor({ timeout: 30_000 });
  evidence.pending_topic_suggestions = await chat.getByRole('button',
    { name: '确认并创建主题' }).count();
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_CHAT_REPLAY_OBSERVED ${evidence.session_id}\n`);
  }
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
