// Installed S04: two real model turns precede the authorization commit.
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, projectId, outputPath] = process.argv.slice(2);
if (![executable, workspace, projectId, outputPath].every(Boolean)) {
  throw new Error('用法：node m7_s04_explore_ui.mjs <exe> <repo> <project> <output>');
}
const evidence = { status: 'running', project_id: projectId, checks: {}, turns: [] };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
let page;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-s04-explore')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  page = await electron.firstWindow({ timeout: 90_000 });
  evidence.network = [];
  page.on('response', response => {
    const pathname = new URL(response.url()).pathname;
    if (pathname.includes('/explore/')) evidence.network.push({ pathname, status: response.status() });
  });
  page.on('pageerror', error => {
    evidence.page_errors = [...(evidence.page_errors ?? []), error.message];
  });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  await page.goto(`${origin}/#/growth?view=all&project=${projectId}`);
  const policy = page.locator('section[aria-labelledby="growth-projects-title"] form:has(h3)');
  const allowed = policy.getByLabel('允许将批准的内容发送给所选模型');
  if (!await allowed.isChecked()) {
    await allowed.check();
    const pending = page.waitForResponse(response => new URL(response.url()).pathname ===
      `/api/growth/local/projects/${projectId}/policy`, { timeout: 30_000 });
    await policy.getByRole('button', { name: '保存项目开关' }).click();
    if ((await pending).status() !== 200) throw new Error('S04 模型权限保存失败');
  }
  const local = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`本机状态 HTTP ${response.status()}`);
    return response.json();
  };
  const initialTopics = (await local()).local_topics.length;
  await page.goto(`${origin}/#/growth?view=topics&project=${projectId}`);
  const questions = [
    '我最近总看到 JWT，它是不是说明登录以后就什么接口都能调用？我有点分不清。',
    '那我更想搞清楚“你是谁”和“你能改什么”怎么分开。',
  ];
  for (const question of questions) {
    await page.getByRole('textbox', { name: '探讨问题' }).fill(question);
    await page.getByRole('button', { name: '预览并发送探讨' }).click();
    await page.getByText('确认发送范围').waitFor({ timeout: 30_000 });
    await page.locator('label').filter({ hasText: '我确认发送这些内容' })
      .locator('input').check();
    const generated = page.waitForResponse(response => new URL(response.url()).pathname ===
      `/api/growth/local/projects/${projectId}/explore`,
      { timeout: 180_000 });
    await page.getByRole('button', { name: '发送并继续对话' }).click();
    const response = await generated;
    if (response.status() !== 200) throw new Error(`S04 探讨模型 HTTP ${response.status()}`);
    const body = await response.json();
    evidence.turns.push({ question, suggestion: body.suggestion, assistant: body.messages.at(-1)?.content_text });
    evidence.session_id = body.session_id;
    await save();
    if (!body.messages.at(-1)?.content_text) throw new Error('S04 探讨回复为空');
    await page.getByText('确认发送范围').waitFor({ state: 'hidden', timeout: 30_000 });
  }
  evidence.checks.two_live_turns = evidence.turns.length === 2 &&
    evidence.turns.every(turn => !!turn.assistant);
  evidence.checks.topic_unconfirmed_before_click = (await local()).local_topics.length === initialTopics;
  if (!evidence.turns.at(-1)?.suggestion) throw new Error('S04 U2 未产生待确认主题建议');
  const confirmButton = page.getByRole('button', { name: '确认保留主题' });
  if (!await confirmButton.isVisible()) {
    await page.getByRole('button', { name: '查看候选建议' }).click();
    await confirmButton.waitFor({ state: 'visible', timeout: 30_000 });
  }
  evidence.confirm_enabled = await confirmButton.isEnabled();
  const [confirmed] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/topic'),
      { timeout: 60_000 }),
    confirmButton.click(),
  ]);
  if (confirmed.status() !== 200) throw new Error(`S04 主题确认 HTTP ${confirmed.status()}`);
  evidence.topic_id = (await confirmed.json()).topic_id;
  const state = await local();
  const session = state.local_explorations.find(item => item.session_id === evidence.session_id);
  evidence.checks.confirmed_once = session?.topic_id === evidence.topic_id &&
    state.local_topics.filter(item => item.topic_id === evidence.topic_id).length === 1;
  evidence.checks.session_title_persisted = typeof session?.title === 'string' &&
    session.title.trim().length > 0;
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('S04 探讨阶段断言未通过');
  evidence.status = 'PASS'; await save();
  process.stdout.write(`S04_EXPLORE_PASS ${evidence.session_id} ${evidence.topic_id}\n`);
} catch (error) {
  evidence.status = 'FAIL'; evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  if (page && !page.isClosed()) {
    evidence.last_url = page.url();
    evidence.alerts = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
  }
  await save(); throw error;
} finally {
  if (electron) await electron.close();
}
