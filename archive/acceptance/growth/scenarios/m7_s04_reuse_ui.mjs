// Observe installed S04 code opportunity linking the chat-confirmed topic.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, topicId, outputPath, ...flags] = process.argv.slice(2);
const live = flags.includes('--live');
const providerFlag = flags.find((flag) => flag.startsWith('--provider='));
const providerName = providerFlag?.slice('--provider='.length) || 'local';
if (!executable || !workspace || !credentialsPath || !topicId || !outputPath
  || flags.some((flag) => flag !== '--live' && flag !== providerFlag)) {
  throw new Error('用法：node m7_s04_reuse_ui.mjs <exe> <workspace> <credentials.json> '
    + '<topic-id> <output.json> [--live] [--provider=名称]');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = credentials.cloud_url ?? `http://127.0.0.1:${credentials.port}`;
const digest = createHash('sha256');
for await (const chunk of createReadStream(executable)) digest.update(chunk);
const evidence = {
  mode: live ? 'live_observation' : 'replay_not_live',
  provider_name: providerName, status: 'running', started_at: new Date().toISOString(),
  executable_sha256: digest.digest('hex'), workspace, account_id: credentials.account_id,
  topic_id: topicId, cloud_origin: new URL(cloudUrl).origin,
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const profile = path.join(path.dirname(outputPath), 'electron-reuse-profile');
await mkdir(profile, { recursive: true });
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
  const localState = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`本地状态读取失败：${response.status()}`);
    return response.json();
  };
  const syncPanel = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = syncPanel.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  const sync = async () => {
    const pending = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
    await syncPanel.locator('form:has(#growth-cloud-url) button').click();
    const response = await pending;
    if (response.status() !== 200) throw new Error(`同步失败：${response.status()} ${await response.text()}`);
    return response.json();
  };
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  evidence.initial_sync = await sync();
  if (evidence.initial_sync.account_id !== credentials.account_id) throw new Error('云账号不一致');
  const initial = await localState();
  const matchingBindings = initial.bindings.filter((item) => item.canonical_local_path === workspace);
  const binding = matchingBindings[0];
  const project = initial.projects.find((item) => item.project_id === binding?.project_id);
  const matchingSnapshots = initial.snapshots.filter((item) =>
    item.binding_ids?.includes(binding?.binding_id));
  const snapshot = matchingSnapshots[0];
  if (matchingBindings.length !== 1 || !project || matchingSnapshots.length !== 1
    || (!live && initial.opportunities.length !== 0)) {
    throw new Error('事件捕获后的代码工作区前置不符');
  }
  evidence.project_id = project.project_id;
  evidence.snapshot_id = snapshot.snapshot_id;
  await page.locator('#growth-project-select').selectOption(project.project_id);
  await page.locator('#growth-binding-select').selectOption(binding.binding_id);
  await page.locator('#growth-snapshot-select').selectOption(snapshot.snapshot_id);
  const policy = page.locator('section[aria-labelledby="growth-projects-title"]')
    .locator('form:has(h3)');
  const modelAllowed = policy.getByLabel('允许将批准的内容发送给所选模型');
  if (!await modelAllowed.isChecked()) {
    await modelAllowed.check();
    const pending = page.waitForResponse((response) =>
      match(response, `/projects/${project.project_id}/policy`), { timeout: 30_000 });
    await policy.getByRole('button', { name: '保存项目开关' }).click();
    if ((await pending).status() !== 200) throw new Error('项目模型授权失败');
  }
  const model = page.locator('.growth-page__model').filter({
    has: page.getByRole('heading', { name: '模型生成机会' }),
  });
  await model.locator(`#growth-provider-select option[value="${providerName}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await model.locator('#growth-provider-select').selectOption(providerName);
  const source = model.locator('fieldset.growth-page__untracked label')
    .filter({ hasText: 'src/access.ts' });
  if (await source.count() !== 1) throw new Error('缺少唯一 access.ts 源码选择');
  await source.locator('input[type="checkbox"]').check();
  if (live) {
    const callsite = model.locator('fieldset.growth-page__untracked label')
      .filter({ hasText: 'src/document.ts' });
    if (await callsite.count() !== 1) throw new Error('缺少唯一 document.ts 调用源码');
    await callsite.locator('input[type="checkbox"]').check();
  }
  const previewPending = page.waitForResponse((response) =>
    match(response, `/snapshots/${snapshot.snapshot_id}/model-preview`), { timeout: 30_000 });
  await model.getByRole('button', { name: '预览模型请求' }).click();
  const previewResponse = await previewPending;
  if (previewResponse.status() !== 200) throw new Error(`模型预览失败：${previewResponse.status()}`);
  const preview = await previewResponse.json();
  const shown = await model.locator('pre.growth-page__payload').innerText();
  if (shown !== preview.payload_text || preview.source_ref_ids.length !== (live ? 2 : 1)
    || !shown.includes('access.ts') || (live && !shown.includes('document.ts'))) {
    throw new Error('批准源码和预览正文不一致');
  }
  evidence.preview = { source_ref_ids: preview.source_ref_ids,
    payload_sha256: createHash('sha256').update(shown).digest('hex'),
    approval_hash: preview.approval_hash };
  await save();
  await model.getByLabel('我已核对上述内容，同意发送给所选 provider。').check();
  const analysisPending = page.waitForResponse((response) =>
    match(response, `/snapshots/${snapshot.snapshot_id}/analysis`), { timeout: 30_000 });
  await model.getByRole('button', { name: '发送并生成学习机会' }).click();
  const analysisResponse = await analysisPending;
  if (![200, 202].includes(analysisResponse.status())) {
    throw new Error(`机会提交失败：${analysisResponse.status()} ${await analysisResponse.text()}`);
  }
  const submission = await analysisResponse.json();
  evidence.submission = submission;
  let final;
  let analysis;
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    final = await localState();
    analysis = final.analysis_runs.find((item) => item.analysis_id === submission.analysis_id);
    if (analysis?.status === 'succeeded' || analysis?.status === 'failed') break;
    await page.waitForTimeout(1000);
  }
  if (analysis?.status !== 'succeeded') throw new Error(`机会分析未成功：${analysis?.status ?? 'timeout'}`);
  const opportunities = final.opportunities.filter((item) => item.analysis_id === analysis.analysis_id);
  evidence.opportunity_candidates = opportunities.map((item) => ({
    opportunity_id: item.opportunity_id, title: item.title,
    learning_goal: item.learning_goal, reason: item.reason,
  }));
  await save();
  const opportunity = live ? opportunities.find((item) =>
    /Bob/i.test(item.learning_goal) && /Alice/i.test(item.learning_goal)
      && /所有者|ownerId|canEdit/i.test(`${item.learning_goal} ${item.reason}`)
      && !/重构|类型守卫/.test(item.title))
    : opportunities[0];
  if (!opportunity || (!live && (opportunities.length !== 1
    || !opportunity.learning_goal.includes('Bob')))) {
    throw new Error('代码授权机会不符合 S04');
  }
  const proposal = final.topic_proposals.find((item) =>
    item.opportunity_id === opportunity.opportunity_id && item.status === 'pending'
      && item.title === 'Web 认证与授权');
  if (!proposal || (!live && proposal.title !== 'Web 认证与授权')) {
    throw new Error('目标主题建议缺失');
  }
  evidence.proposal_title = proposal.title;
  evidence.analysis_id = analysis.analysis_id;
  evidence.opportunity_id = opportunity.opportunity_id;
  evidence.proposal_id = proposal.proposal_id;
  await save();
  evidence.analysis_sync = await sync();
  const row = page.locator('.growth-page__opportunities li').filter({
    has: page.locator(`#growth-topic-reuse-${proposal.proposal_id}`),
  });
  await row.waitFor({ timeout: 30_000 });
  const targetOption = row.locator(
    `#growth-topic-reuse-${proposal.proposal_id} option[value="${topicId}"]`);
  await targetOption.waitFor({ state: 'attached', timeout: 30_000 });
  if ((await targetOption.textContent())?.trim() !== 'Web 认证与授权') {
    throw new Error('目标主题 ID 不是已确认的 Web 认证与授权');
  }
  await row.locator(`#growth-topic-reuse-${proposal.proposal_id}`).selectOption(topicId);
  const proposalRow = row.locator(`#growth-topic-reuse-${proposal.proposal_id}`).locator('..');
  evidence.new_topic_confirm_disabled = await proposalRow
    .getByRole('button', { name: '确认主题' }).isDisabled();
  if (!evidence.new_topic_confirm_disabled) throw new Error('同名正式主题仍可重复创建');
  const decisionPending = page.waitForResponse((response) =>
    match(response, `/topic-proposals/${proposal.proposal_id}/decision`), { timeout: 30_000 });
  await proposalRow.getByRole('button', { name: '关联已有主题' }).click();
  const decided = await decisionPending;
  if (decided.status() !== 200) throw new Error(`主题复用失败：${decided.status()} ${await decided.text()}`);
  evidence.topic_decision = await decided.json();
  if (evidence.topic_decision.topic_id !== topicId) throw new Error('复用主题 ID 改变');
  await save();
  const taskRow = page.locator('.growth-page__opportunities li')
    .filter({ hasText: opportunity.learning_goal });
  if (await taskRow.count() !== 1) throw new Error('已确认机会没有唯一卡片入口');
  const taskButton = taskRow.getByRole('button', { name: '创建短卡片' });
  await taskButton.click({ trial: true, timeout: 30_000 });
  const [created] = await Promise.all([
    page.waitForResponse((response) =>
      match(response, `/opportunities/${opportunity.opportunity_id}/tasks`), { timeout: 30_000 }),
    taskButton.click(),
  ]);
  if (created.status() !== 201) throw new Error(`短卡片创建失败：${created.status()} ${await created.text()}`);
  evidence.task_id = (await created.json()).task_id;
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  evidence.card_text = await card.innerText();
  if (!evidence.card_text.includes('Bob') || !evidence.card_text.includes('Alice')) {
    throw new Error('短卡片未展示授权学习目标');
  }
  evidence.final_sync = await sync();
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_REUSE_OBSERVED ${topicId} ${evidence.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
