// Observe the installed S04 two-turn chat path. A separate review records PASS.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, providerName, outputPath, modeFlag] =
  process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !providerName || !outputPath
    || (modeFlag && modeFlag !== '--replay')) {
  throw new Error('用法：node m7_s04_chat_ui.mjs <exe> <workspace> <credentials.json> <provider> <output.json> [--replay]');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
if (!credentials.access_token || !credentials.account_id || !/^https?:\/\//.test(cloudUrl)) {
  throw new Error('云端凭据不完整');
}
const mode = modeFlag === '--replay' ? 'replay_not_live' : 'live_observation';
const executableHash = createHash('sha256');
for await (const chunk of createReadStream(executable)) executableHash.update(chunk);
const messages = [
  '我最近总看到 JWT，它是不是说明登录以后就什么接口都能调用？我有点分不清。',
  '那我更想搞清楚“你是谁”和“你能改什么”怎么分开。',
];
const evidence = {
  mode,
  status: 'running',
  platform: 'win32-x64',
  executable,
  executable_sha256: executableHash.digest('hex'),
  workspace,
  account_id: credentials.account_id,
  cloud_origin: new URL(cloudUrl).origin,
  provider_name: providerName,
  started_at: new Date().toISOString(),
  turns: [],
};
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
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
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  await sync.locator('form:has(#growth-cloud-url) button').click();
  await sync.locator('details > p[role="status"]').waitFor({ timeout: 120_000 });
  const chat = sync.getByRole('heading', { name: '成长对话' }).locator('..');
  const topics = async () => page.locator('#growth-topic-select option').evaluateAll(
    (options) => options.map((option) => ({ id: option.value, title: option.textContent })),
  );
  await page.locator('#growth-topic-select option').first().waitFor({
    state: 'attached', timeout: 30_000,
  });
  evidence.topics_before = await topics();
  if (evidence.topics_before.length !== 1) throw new Error('S04 前置正式主题不是唯一旧主题');
  const startPromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/chats/start'), { timeout: 30_000 });
  await chat.getByRole('button', { name: '新建对话' }).click();
  const started = await startPromise;
  if (started.status() !== 201) throw new Error(`新建对话失败：${started.status()}`);
  evidence.session_id = (await started.json()).session_id;
  await page.waitForFunction((id) => {
    const selected = document.querySelector('#growth-chat-session');
    const message = document.querySelector('#growth-chat-message');
    const history = document.querySelector('[aria-label="对话历史"]');
    return selected instanceof HTMLSelectElement && selected.value === id
      && message instanceof HTMLTextAreaElement && history
      && history.querySelectorAll('pre').length === 0;
  }, evidence.session_id, { timeout: 30_000 });
  await save();

  for (const [index, message] of messages.entries()) {
    const turn = { index: index + 1, user_message: message, started_at: new Date().toISOString() };
    evidence.turns.push(turn);
    await save();
    await page.locator('#growth-chat-message').fill(message);
    const sentPromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/messages`),
    { timeout: 30_000 });
    await chat.getByRole('button', { name: '发送', exact: true }).click();
    const sent = await sentPromise;
    if (sent.status() !== 201) throw new Error(`第 ${index + 1} 轮消息上传失败：${sent.status()}`);
    turn.user_receipt = await sent.json();
    await chat.locator('[aria-label="对话历史"] pre').filter({ hasText: message })
      .waitFor({ timeout: 30_000 });
    await page.locator('#growth-chat-provider').fill(providerName);
    const previewPromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/preview`),
    { timeout: 30_000 });
    await chat.getByRole('button', { name: '预览模型请求' }).click();
    const previewResponse = await previewPromise;
    if (previewResponse.status() !== 200) {
      throw new Error(`第 ${index + 1} 轮预览失败：${previewResponse.status()}`);
    }
    const preview = await previewResponse.json();
    const shown = await chat.locator('pre.growth-page__payload').innerText();
    if (shown !== preview.payload_text || !shown.includes(message)) {
      throw new Error(`第 ${index + 1} 轮预览内容不匹配`);
    }
    if (index === 1) {
      const context = JSON.parse(shown.slice(shown.indexOf('\n') + 1));
      if (context.messages.length !== 3
        || context.messages[0].role !== 'user'
        || context.messages[0].content !== messages[0]
        || context.messages[1].role !== 'assistant'
        || context.messages[1].content !== evidence.turns[0].assistant_receipt.content_text
        || context.messages[2].role !== 'user'
        || context.messages[2].content !== message) {
        throw new Error('第二轮预览缺少上一轮用户消息或助手回复');
      }
    }
    if (mode === 'live_observation' && /(?:acceptance|fake|replay)/i.test(preview.model_name)) {
      throw new Error('live 观察拒绝固定响应模型');
    }
    turn.preview = preview;
    await save();
    await chat.getByLabel('我已检查完整内容，批准发送给此模型').check();
    const generatePromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/chats/${evidence.session_id}/generate`),
    { timeout: 120_000 });
    await chat.getByRole('button', { name: '生成回复或重试上传' }).click();
    const generated = await generatePromise;
    if (generated.status() !== 200) {
      throw new Error(`第 ${index + 1} 轮生成未获云确认：${generated.status()} ${await generated.text()}`);
    }
    const generation = await generated.json();
    if (generation.sync_state !== 'acked' || !generation.message?.content_text) {
      throw new Error(`第 ${index + 1} 轮缺少云确认或回复正文`);
    }
    turn.assistant_receipt = generation.message;
    turn.sync_state = generation.sync_state;
    turn.finished_at = new Date().toISOString();
    await chat.locator('[aria-label="对话历史"] pre')
      .filter({ hasText: generation.message.content_text }).waitFor({ timeout: 30_000 });
    await save();
  }
  const history = await chat.locator('[aria-label="对话历史"] pre').allInnerTexts();
  if (history.length !== 4) throw new Error(`双轮对话历史条数不符：${history.length}`);
  evidence.topics_after = await topics();
  if (JSON.stringify(evidence.topics_after) !== JSON.stringify(evidence.topics_before)) {
    throw new Error('未确认建议却改变正式主题列表');
  }
  evidence.pending_suggestion_visible = await chat.getByRole('button', {
    name: '确认并创建主题',
  }).count() > 0;
  if (!evidence.pending_suggestion_visible) throw new Error('两轮对话后未显示待确认主题建议');
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_CHAT_OBSERVED ${mode} ${evidence.session_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
