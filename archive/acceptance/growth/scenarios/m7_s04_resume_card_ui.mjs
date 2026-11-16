// Continue a live S04 opportunity after its topic was confirmed in the installed app.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, attemptPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !attemptPath || !outputPath) {
  throw new Error('用法：node m7_s04_resume_card_ui.mjs <exe> <workspace> '
    + '<credentials.json> <prior-attempt.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prior = JSON.parse(await readFile(attemptPath, 'utf8'));
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: 'live_observation', status: 'running', started_at: new Date().toISOString(),
  executable_sha256: hash.digest('hex'), workspace,
  account_id: credentials.account_id, analysis_id: prior.analysis_id,
  opportunity_id: prior.opportunity_id, proposal_id: prior.proposal_id,
  topic_id: prior.topic_id, prior_attempt: path.basename(attemptPath), responses: [],
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const profile = path.join(path.dirname(outputPath), 'electron-card-resume-profile');
await mkdir(profile, { recursive: true });
await save();
let electron;
try {
  if (prior.provider_name !== 'deepseek' || prior.mode !== 'live_observation'
    || prior.workspace !== workspace || prior.account_id !== credentials.account_id
    || prior.executable_sha256 !== evidence.executable_sha256) {
    throw new Error('先前真实分析与本次安装版、工作区或账号不一致');
  }
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${profile}`], env: { ...process.env, GROWTH_WORKSPACE: workspace },
    timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth`);
  await page.locator('#growth-cloud-url').waitFor({ state: 'visible', timeout: 30_000 });
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地凭据失败：${response.status}`);
    return (await response.json()).token;
  });
  const headers = { 'X-AhaDiff-Token': token };
  const localState = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`本机状态读取失败：${response.status()}`);
    return response.json();
  };
  const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const sync = async () => {
    const pending = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 90_000 });
    await page.locator('section[aria-labelledby="growth-sync-title"]')
      .locator('form:has(#growth-cloud-url) button').click();
    const response = await pending;
    if (response.status() !== 200) throw new Error(`同步失败：${response.status()}`);
    return response.json();
  };
  evidence.initial_sync = await sync();
  const state = await localState();
  const opportunity = state.opportunities.find((item) =>
    item.opportunity_id === prior.opportunity_id && item.analysis_id === prior.analysis_id);
  const proposal = state.topic_proposals.find((item) => item.proposal_id === prior.proposal_id);
  if (!opportunity || !proposal || proposal.status !== 'confirmed'
    || proposal.topic_id !== prior.topic_id || state.growth_tasks?.some((item) =>
      item.opportunity_id === prior.opportunity_id)) {
    throw new Error('已确认主题与待创建短卡的机会不匹配');
  }
  if (!/Bob/.test(opportunity.learning_goal) || !/Alice/.test(opportunity.learning_goal)) {
    throw new Error('学习目标未明确 Bob/Alice 资源授权情境');
  }
  const project = state.projects.find((item) => item.project_id === prior.project_id);
  const binding = state.bindings.find((item) => item.binding_id === prior.binding_id)
    ?? state.bindings[0];
  const snapshot = state.snapshots.find((item) => item.snapshot_id === prior.snapshot_id);
  if (!project || !binding || !snapshot) throw new Error('原分析项目、仓库或快照缺失');
  await page.locator('#growth-project-select').selectOption(project.project_id);
  await page.locator('#growth-binding-select').selectOption(binding.binding_id);
  await page.locator('#growth-snapshot-select').selectOption(snapshot.snapshot_id);
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: opportunity.learning_goal });
  await row.waitFor({ timeout: 30_000 });
  if (await row.count() !== 1) throw new Error('目标学习机会未在界面中唯一显示');
  const button = row.getByRole('button', { name: '创建短卡片' });
  await button.waitFor({ state: 'visible', timeout: 30_000 });
  const taskPath = `/opportunities/${opportunity.opportunity_id}/tasks`;
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname.endsWith(taskPath)) {
      evidence.responses.push({ path: pathname, status: response.status() });
    }
  });
  const pending = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(taskPath), { timeout: 30_000 });
  await button.click();
  const created = await pending;
  if (created.status() !== 201) throw new Error(`短卡片创建失败：${created.status()} ${await created.text()}`);
  evidence.task_id = (await created.json()).task_id;
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  evidence.card_text = await card.innerText();
  if (!evidence.card_text.includes('Bob') || !evidence.card_text.includes('Alice')) {
    throw new Error('短卡片未展示 Bob/Alice 授权问题');
  }
  evidence.final_sync = await sync();
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_CARD_RESUMED ${evidence.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
