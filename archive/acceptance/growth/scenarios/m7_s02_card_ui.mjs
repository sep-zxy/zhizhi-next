// Confirm the real model opportunity and open its S02 short card in the installed app.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, modelPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !modelPath || !outputPath) {
  throw new Error('用法：node m7_s02_card_ui.mjs <exe> <workspace> <model.json> <output.json>');
}
const model = JSON.parse(await readFile(modelPath, 'utf8'));
if (model.mode !== 'live_observation' || model.status !== 'observed_pending_semantic_review'
  || model.preview?.provider_host !== 'https://api.deepseek.com'
  || !Array.isArray(model.opportunities) || model.opportunities.length < 1
  || model.opportunities.length > 3 || !Array.isArray(model.topic_proposals)) {
  throw new Error('S02 真实模型机会不符合 1–3 条结构约束');
}
const opportunity = model.opportunities.find((item) =>
  item.title.includes('requestId') && item.title.includes('latestRequest'))
  ?? model.opportunities.find((item) => item.reason.includes('requestId === latestRequest'));
const proposal = model.topic_proposals.find((item) =>
  item.opportunity_id === opportunity?.opportunity_id);
if (!opportunity || !proposal || proposal.status !== 'pending') {
  throw new Error('S02 主题建议与机会不一致');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: 'live_observation', status: 'running',
  executable_sha256: hash.digest('hex'), workspace,
  opportunity_id: opportunity.opportunity_id, proposal_id: proposal.proposal_id,
  analysis_id: model.analysis.analysis_id, started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-profile-s02-card')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const local = await (await page.request.get(`${origin}/api/growth/local`, { headers })).json();
  const currentProposal = local.topic_proposals.find((item) =>
    item.proposal_id === proposal.proposal_id);
  const existingTask = local.growth_tasks.find((item) =>
    item.opportunity_id === opportunity.opportunity_id);
  if (!currentProposal || local.growth_tasks.length > 1
    || (existingTask && currentProposal.status !== 'confirmed')
    || (!existingTask && currentProposal.status !== 'pending')) {
    throw new Error('S02 本机任务或建议状态不正确');
  }
  await page.locator('#growth-project-select').selectOption(model.project_id);
  await page.locator('#growth-binding-select').selectOption(model.binding_id);
  await page.locator('#growth-snapshot-select').selectOption(model.snapshot_id);
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: opportunity.title });
  await row.waitFor({ timeout: 30_000 });
  if (existingTask) {
    evidence.continued_after_driver_assertion = true;
    evidence.topic_id = currentProposal.topic_id;
    evidence.task_id = existingTask.task_id;
    await row.getByRole('button', { name: /打开卡片/ }).click();
  } else {
    const confirmedResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/topic-proposals/${proposal.proposal_id}/decision`),
    { timeout: 30_000 });
    await row.getByRole('button', { name: '确认主题' }).click();
    const confirmed = await confirmedResponse;
    if (confirmed.status() !== 200) throw new Error(`S02 确认主题失败 ${confirmed.status()}`);
    evidence.topic_id = (await confirmed.json()).topic_id;
    await save();
    const createdResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/opportunities/${opportunity.opportunity_id}/tasks`),
    { timeout: 30_000 });
    await row.getByRole('button', { name: '创建短卡片' }).click();
    const created = await createdResponse;
    if (created.status() !== 201) throw new Error(`S02 创建卡片失败 ${created.status()}`);
    evidence.task_id = (await created.json()).task_id;
  }
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  evidence.card_text = await card.innerText();
  const taskResponse = await page.request.get(
    `${origin}/api/growth/local/tasks/${evidence.task_id}`, { headers });
  if (taskResponse.status() !== 200) throw new Error('S02 本机卡片不可读取');
  evidence.task_chain = await taskResponse.json();
  if (!/请求|响应|结果|竞态|request/i.test(evidence.task_chain.task.question)
    || evidence.task_chain.task.question !== evidence.task_chain.knowledge_card?.front_question
    || !evidence.card_text.includes(evidence.task_chain.task.question)
    || !evidence.task_chain.card.expected_answer.includes('latestRequest')
    || evidence.task_chain.attempts.length !== 0) {
    throw new Error('S02 卡片题目与真实机会或空回答前置不一致');
  }
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_CARD_OBSERVED ${evidence.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
