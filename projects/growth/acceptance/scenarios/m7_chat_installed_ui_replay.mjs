// Isolated installed-client chat rehearsal. Never count as live S04 evidence.
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !outputPath) {
  throw new Error('用法：node m7_chat_installed_ui_replay.mjs <exe> <workspace> <credentials.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
const u1 = '我最近总看到 JWT，它是不是说明登录以后就什么接口都能调用？我有点分不清。';
const u2 = '那我更想搞清楚“你是谁”和“你能改什么”怎么分开。';
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
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
  const origin = new URL(page.url()).origin;
  const responses = [];
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname.startsWith('/api/growth/local/cloud/chats')) {
      responses.push({ path: pathname, status: response.status() });
    }
  });
  await page.goto(`${origin}/#/growth`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  await sync.locator('form:has(#growth-cloud-url) button').click();
  await sync.locator(':scope > p[role="status"]').waitFor({ timeout: 120_000 });
  const chat = sync.getByRole('heading', { name: '成长对话' }).locator('..');
  await chat.getByRole('button', { name: '新建对话' }).click();
  await page.locator('#growth-chat-message').waitFor({ timeout: 30_000 });
  const replies = [
    '认证说明请求属于谁，JWT 本身不代表所有接口权限。',
    '认证回答你是谁；授权再判断你能否修改指定文档。',
  ];
  const previewTexts = [];
  for (const [index, message] of [u1, u2].entries()) {
    await page.locator('#growth-chat-message').fill(message);
    await chat.getByRole('button', { name: '发送', exact: true }).click();
    await chat.locator('[aria-label="对话历史"] pre').filter({ hasText: message })
      .waitFor({ timeout: 30_000 });
    await page.locator('#growth-chat-provider').fill('local');
    await chat.getByRole('button', { name: '预览模型请求' }).click();
    const payload = await chat.locator('pre.growth-page__payload').innerText();
    if (!payload.includes(message) || (index === 1 && !payload.includes(u1))) {
      throw new Error(`第 ${index + 1} 轮模型预览缺少对话原文`);
    }
    previewTexts.push(payload);
    await chat.getByLabel('我已检查完整内容，批准发送给此模型').check();
    await chat.getByRole('button', { name: '生成回复或重试上传' }).click();
    await chat.locator('[aria-label="对话历史"] pre').filter({ hasText: replies[index] })
      .waitFor({ timeout: 90_000 });
    if (index === 0 && await chat.getByRole('button', { name: '确认并创建主题' }).count()) {
      throw new Error('首轮未出现学习方向时不得生成主题建议');
    }
  }
  const sessionId = await page.locator('#growth-chat-session').inputValue();
  await chat.getByText('Web 认证与授权', { exact: false }).waitFor({ timeout: 90_000 });
  const history = await chat.locator('[aria-label="对话历史"]').innerText();
  if (replies.some((reply) => !history.includes(reply))) {
    throw new Error('安装版未显示两轮模型回复');
  }
  const topicCount = await page.locator('#growth-topic-select option').count();
  if (topicCount !== 1) throw new Error(`主题建议未经确认却改变主题数：${topicCount}`);
  if (!(await chat.getByRole('button', { name: '确认并创建主题' }).isVisible())) {
    throw new Error('待确认主题建议未显示');
  }
  const evidence = {
    mode: 'replay_not_live',
    platform: 'win32-x64',
    cloud_kind: 'isolated_local_postgresql',
    workspace,
    account_id: credentials.account_id,
    session_id: sessionId,
    sent_messages: [u1, u2],
    preview_contains_current_message: previewTexts.map((payload, index) =>
      payload.includes([u1, u2][index])),
    second_preview_contains_first_turn: previewTexts[1].includes(u1)
      && previewTexts[1].includes(replies[0]),
    assistant_replies_visible: replies.length,
    suggestion_pending: true,
    topic_count_before_decision: topicCount,
    responses,
    finished_at: new Date().toISOString(),
  };
  await writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
  process.stdout.write(`CHAT_UI_REPLAY_PASS ${sessionId}\n`);
} finally {
  if (electron) await electron.close();
}
