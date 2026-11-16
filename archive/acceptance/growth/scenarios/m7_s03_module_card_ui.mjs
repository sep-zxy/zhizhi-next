// Drive an installed S03 module card with either a live or a replay provider.
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
    || flags.some((flag) => flag !== '--live')) {
  throw new Error('用法：node m7_s03_module_card_ui.mjs <exe> <workspace> <credentials.json> <preparation.json> <output.json> [--live]');
}
const live = flags.includes('--live');
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
if (credentials.account_id !== prepared.account_id || cloudUrl !== prepared.cloud_origin) {
  throw new Error('隔离账号与已审计模块来源不一致');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: live ? 'live_observation' : 'replay_not_live', status: 'running',
  executable_sha256: hash.digest('hex'),
  workspace, account_id: credentials.account_id, cloud_origin: cloudUrl,
  module_id: prepared.module_id, module_index_revision: prepared.module_index_revision,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const match = (response, suffix) => new URL(response.url()).pathname.endsWith(suffix);
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable, args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  await page.waitForURL(/#\/growth\?view=all$/, { timeout: 30_000 });
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
  const initial = await localState();
  if (initial.projects.length !== 1 || initial.bindings.length !== 1 ||
      initial.snapshots.length !== 1 || initial.opportunities.length !== 0 ||
      initial.growth_tasks.length !== 0) {
    throw new Error('S03 隔离工作区前置不是唯一模块快照且无卡片');
  }
  const project = initial.projects[0];
  const binding = initial.bindings[0];
  const snapshot = initial.snapshots[0];
  if (project.project_id !== prepared.project_id || snapshot.snapshot_id !== prepared.snapshot_id) {
    throw new Error('S03 模块与本地快照 ID 不一致');
  }
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const firstSync = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const connected = await firstSync;
  if (connected.status() !== 200) throw new Error(`模块账号同步失败：${connected.status()}`);
  evidence.initial_sync = await connected.json();
  if (evidence.initial_sync.account_id !== prepared.account_id) throw new Error('同步账号不一致');
  evidence.after_sync_ui = {
    url: page.url(),
    project_select_count: await page.locator('#growth-project-select').count(),
    headings: await page.locator('h1,h2').allInnerTexts(),
    sync_open: await syncDetails.evaluate((element) => element.open),
  };
  await save();
  await page.locator('#growth-project-select').selectOption(project.project_id);
  await page.locator('#growth-binding-select').selectOption(binding.binding_id);
  await page.locator('#growth-snapshot-select').selectOption(snapshot.snapshot_id);

  const policy = page.locator('section[aria-labelledby="growth-projects-title"]')
    .locator('form:has(h3)');
  await page.waitForFunction(() => {
    const form = document.querySelector('section[aria-labelledby="growth-projects-title"] form:has(h3)');
    const inputs = form?.querySelectorAll('input[type="checkbox"]');
    return inputs?.length === 3 && inputs[2].checked;
  }, null, { timeout: 30_000 });
  const modelAllowed = policy.getByLabel('允许将批准的内容发送给所选模型');
  evidence.policy_before = {
    model_allowed: await modelAllowed.isChecked(),
    cloud_allowed: await policy.getByLabel('允许同步到成长云').isChecked(),
  };
  await save();
  if (!await modelAllowed.isChecked()) {
    await modelAllowed.check();
    const policyResponse = page.waitForResponse((response) =>
      match(response, `/projects/${project.project_id}/policy`), { timeout: 30_000 });
    await policy.getByRole('button', { name: '保存项目开关' }).click();
    const saved = await policyResponse;
    const savedBody = await saved.json();
    if (saved.status() !== 200 || !savedBody.model_allowed) {
      throw new Error(`项目模型授权失败：${saved.status()} ${JSON.stringify(savedBody)}`);
    }
  }
  const model = page.locator('.growth-page__model').filter({
    has: page.getByRole('heading', { name: '模型生成机会' }),
  });
  const providerName = live ? 'deepseek' : 'local';
  await model.locator(`#growth-provider-select option[value="${providerName}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await model.locator('#growth-provider-select').selectOption(providerName);
  const names = ['PriceCache.java', 'OrderService.java'];
  for (const name of names) {
    const source = model.locator('fieldset.growth-page__untracked label').filter({ hasText: name });
    if (await source.count() !== 1) throw new Error(`缺少唯一源码选择：${name}`);
    await source.locator('input[type="checkbox"]').check();
  }
  const previewResponse = page.waitForResponse((response) =>
    match(response, `/snapshots/${snapshot.snapshot_id}/model-preview`), { timeout: 30_000 });
  await model.getByRole('button', { name: '预览模型请求' }).click();
  const preview = await previewResponse;
  if (preview.status() !== 200) throw new Error(`S03 模型预览失败：${preview.status()}`);
  const payload = await preview.json();
  const shown = await model.locator('pre.growth-page__payload').innerText();
  if (shown !== payload.payload_text || payload.source_ref_ids.length !== 2 ||
      names.some((name) => !shown.includes(name))) {
    throw new Error('模型预览与两份批准源码不一致');
  }
  evidence.preview = {
    source_ref_ids: payload.source_ref_ids,
    payload_sha256: createHash('sha256').update(shown).digest('hex'),
    approval_hash: payload.approval_hash,
    provider_host: payload.provider_host,
    model_name: payload.model_name,
    ui_payload_exact_match: true,
  };
  if (live && (payload.provider_host !== 'https://api.deepseek.com'
      || payload.model_name !== 'deepseek-flash')) {
    throw new Error('live S03 必须使用真实 DeepSeek provider');
  }
  await save();
  await model.getByLabel('我已核对上述内容，同意发送给所选 provider。').check();
  const analysisResponse = page.waitForResponse((response) =>
    match(response, `/snapshots/${snapshot.snapshot_id}/analysis`), { timeout: 30_000 });
  await model.getByRole('button', { name: '发送并生成学习机会' }).click();
  const submitted = await analysisResponse;
  if (![200, 202].includes(submitted.status())) {
    throw new Error(`S03 机会提交失败：${submitted.status()}`);
  }
  evidence.submission = await submitted.json();
  let final;
  let analysis;
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    final = await localState();
    analysis = final.analysis_runs.find((item) =>
      item.analysis_id === evidence.submission.analysis_id);
    if (analysis?.status === 'succeeded' || analysis?.status === 'failed') break;
    await page.waitForTimeout(1000);
  }
  if (analysis?.status !== 'succeeded') {
    throw new Error(`S03 机会生成未成功：${analysis?.status ?? 'timeout'}`);
  }
  const opportunities = final.opportunities.filter((item) =>
    item.analysis_id === analysis.analysis_id);
  const matching = opportunities.filter((item) =>
    /TTL|缓存|过期/i.test(`${item.title} ${item.learning_goal}`)
    && new Set(JSON.parse(item.source_refs_json)).size === 2
    && payload.source_ref_ids.every((id) => JSON.parse(item.source_refs_json).includes(id)));
  if (matching.length < 1) {
    throw new Error('S03 缺少同时引用 PriceCache 和 Service 的 TTL 学习机会');
  }
  const opportunity = matching[0];
  const proposal = final.topic_proposals.find((item) =>
    item.opportunity_id === opportunity.opportunity_id && item.status === 'pending');
  if (!proposal) throw new Error('S03 TTL 主题建议缺失');
  evidence.opportunity_id = opportunity.opportunity_id;
  evidence.proposal_id = proposal.proposal_id;
  evidence.analysis = analysis;
  evidence.opportunity = opportunity;
  evidence.all_opportunities = opportunities;
  const row = page.locator('.growth-page__opportunities li').filter({
    hasText: opportunity.title,
  });
  await row.waitFor({ timeout: 30_000 });
  const confirmedResponse = page.waitForResponse((response) =>
    match(response, `/topic-proposals/${proposal.proposal_id}/decision`), { timeout: 30_000 });
  await row.getByRole('button', { name: '确认主题' }).click();
  const confirmed = await confirmedResponse;
  if (confirmed.status() !== 200) throw new Error('S03 卡片主题确认失败');
  evidence.topic_id = (await confirmed.json()).topic_id;
  const moduleSelect = row.locator(`#growth-card-module-${opportunity.opportunity_id}`);
  await moduleSelect.locator(`option[value="${prepared.module_id}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await moduleSelect.selectOption(prepared.module_id);
  const taskResponse = page.waitForResponse((response) =>
    match(response, `/opportunities/${opportunity.opportunity_id}/tasks`), { timeout: 30_000 });
  await row.getByRole('button', { name: '创建短卡片' }).click();
  const created = await taskResponse;
  if (created.status() !== 201) throw new Error(`S03 模块建卡失败：${created.status()}`);
  evidence.task_id = (await created.json()).task_id;
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  await card.getByText(opportunity.learning_goal, { exact: false }).waitFor({ timeout: 30_000 });
  await card.getByText('订单计价', { exact: false }).waitFor({ timeout: 30_000 });
  const cardText = await card.innerText();
  evidence.card_text = cardText;
  await save();
  if (!cardText.includes('订单计价') || !cardText.includes(opportunity.learning_goal)) {
    throw new Error('安装版卡片未显示模块与 TTL 问题');
  }
  const chainResponse = await page.request.get(
    `${origin}/api/growth/local/tasks/${evidence.task_id}`, { headers });
  if (chainResponse.status() !== 200) throw new Error('模块卡片本地原文不可读取');
  const chain = await chainResponse.json();
  if (chain.task.module_id !== prepared.module_id ||
      chain.task.module_index_revision !== prepared.module_index_revision ||
      chain.task.question !== opportunity.learning_goal) {
    throw new Error('本地模块卡片缺少模块索引版本');
  }
  evidence.local_task = chain.task;
  await save();

  const syncResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const synced = await syncResponse;
  if (synced.status() !== 200) throw new Error(`模块卡片上传失败：${synced.status()}`);
  evidence.final_sync = await synced.json();
  if (evidence.final_sync.outbox.pending !== 0 ||
      evidence.final_sync.outbox.conflict !== 0) {
    throw new Error('模块卡片上传后 outbox 仍有待处理或冲突');
  }
  evidence.status = live ? 'observed_pending_semantic_review' : 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_MODULE_CARD_OBSERVED ${evidence.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
