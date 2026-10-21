// Drive the installed S01 model request through the UI. Review evidence separately.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, providerName, outputPath, ...flags] = process.argv.slice(2);
if (!executable || !workspace || !providerName || !outputPath
  || flags.some((flag) => !['--replay', '--reanalysis'].includes(flag)
    && !flag.startsWith('--source='))) {
  throw new Error('用法：node m7_s01_model_ui.mjs <exe> <workspace> <provider> <output.json> [--replay] [--reanalysis] [--source=path]');
}
const mode = flags.includes('--replay') ? 'replay_not_live' : 'live_observation';
const reanalysis = flags.includes('--reanalysis');
const sourceNames = (flags.find((flag) => flag.startsWith('--source='))?.slice(9)
  || 'RoutePlanner.java').split(',').filter(Boolean);
if (mode === 'live_observation' && /(?:acceptance|fake|replay)/i.test(providerName)) {
  throw new Error('live 观察拒绝固定响应 provider');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode,
  status: 'running',
  platform: 'win32-x64',
  executable,
  executable_sha256: hash.digest('hex'),
  workspace,
  provider_name: providerName,
  reanalysis,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const apiPath = (response, suffix) => new URL(response.url()).pathname.endsWith(suffix);
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
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth`);
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地凭据引导失败：${response.status}`);
    return (await response.json()).token;
  });
  const stateHeaders = { 'X-AhaDiff-Token': token };
  const stateResponse = await page.request.get(`${origin}/api/growth/local`, {
    headers: stateHeaders,
  });
  if (stateResponse.status() !== 200) throw new Error(`本地状态失败：${stateResponse.status()}`);
  const initial = await stateResponse.json();
  if (initial.projects.length !== 1 || initial.bindings.length !== 1
    || initial.snapshots.length !== 1 || initial.opportunities.length !== 0) {
    throw new Error('S01 隔离工作区应只有一个项目、绑定和快照');
  }
  const project = initial.projects[0];
  const binding = initial.bindings[0];
  const snapshot = initial.snapshots[0];
  evidence.project_id = project.project_id;
  evidence.binding_id = binding.binding_id;
  evidence.snapshot_id = snapshot.snapshot_id;
  await page.locator('#growth-project-select').selectOption(project.project_id);
  await page.locator('#growth-binding-select').selectOption(binding.binding_id);
  await page.locator('#growth-snapshot-select').selectOption(snapshot.snapshot_id);

  const policy = page.locator('section[aria-labelledby="growth-projects-title"]')
    .locator('form:has(h3)');
  const modelAllowed = policy.getByLabel('允许将批准的内容发送给所选模型');
  if (!await modelAllowed.isChecked()) {
    await modelAllowed.check();
    const policyPromise = page.waitForResponse((response) =>
      apiPath(response, `/projects/${project.project_id}/policy`), { timeout: 30_000 });
    await policy.getByRole('button', { name: '保存项目开关' }).click();
    const saved = await policyPromise;
    if (saved.status() !== 200 || !(await saved.json()).model_allowed) {
      throw new Error(`项目模型授权失败：${saved.status()}`);
    }
  }
  evidence.project_model_allowed = true;
  await save();

  const model = page.locator('.growth-page__model').filter({
    has: page.getByRole('heading', { name: '模型生成机会' }),
  });
  await model.locator(`#growth-provider-select option[value="${providerName}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await model.locator('#growth-provider-select').selectOption(providerName);
  const sourceLabels = model.locator('fieldset.growth-page__untracked label');
  if (await sourceLabels.count() !== sourceNames.length) {
    throw new Error('模型来源数量与指定源码不一致');
  }
  for (const name of sourceNames) {
    const label = sourceLabels.filter({ hasText: name });
    if (await label.count() !== 1) throw new Error(`模型来源不唯一：${name}`);
    await label.locator('input[type="checkbox"]').check();
  }
  const previewPromise = page.waitForResponse((response) =>
    apiPath(response, `/snapshots/${snapshot.snapshot_id}/model-preview`), { timeout: 30_000 });
  await model.getByRole('button', { name: '预览模型请求' }).click();
  const previewResponse = await previewPromise;
  if (previewResponse.status() !== 200) {
    throw new Error(`预览失败：${previewResponse.status()} ${await previewResponse.text()}`);
  }
  const preview = await previewResponse.json();
  const shown = await model.locator('pre.growth-page__payload').innerText();
  if (shown !== preview.payload_text || preview.source_ref_ids.length !== sourceNames.length
    || sourceNames.some((name) => !shown.includes(name))
    || !shown.includes(preview.source_ref_ids[0])) {
    throw new Error('UI 展示的请求正文与后端预览或源码选择不符');
  }
  if (mode === 'live_observation' && /(?:acceptance|fake|replay)/i.test(preview.model_name)) {
    throw new Error('live 观察拒绝固定响应模型');
  }
  evidence.preview = {
    source_ref_ids: preview.source_ref_ids,
    payload_sha256: createHash('sha256').update(preview.payload_text).digest('hex'),
    approval_hash: preview.approval_hash,
    provider_host: preview.provider_host,
    model_name: preview.model_name,
    ui_payload_exact_match: true,
  };
  await save();
  await model.getByLabel('我已核对上述内容，同意发送给所选 provider。').check();
  const analysisPromise = page.waitForResponse((response) =>
    apiPath(response, `/snapshots/${snapshot.snapshot_id}/analysis`), { timeout: 30_000 });
  await model.getByRole('button', { name: reanalysis
    ? '主动重新分析并保留版本关系' : '发送并生成学习机会' }).click();
  const submitted = await analysisPromise;
  if (![200, 202].includes(submitted.status())) {
    throw new Error(`分析提交失败：${submitted.status()} ${await submitted.text()}`);
  }
  const submission = await submitted.json();
  if (!submission.analysis_id || !submission.task_id || submission.reused) {
    throw new Error('分析提交缺少新任务或分析 ID');
  }
  evidence.submission = submission;
  await save();
  let final;
  let analysis;
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    const response = await page.request.get(`${origin}/api/growth/local`, {
      headers: stateHeaders,
    });
    if (response.status() !== 200) throw new Error('无法读取分析后的本地状态');
    final = await response.json();
    analysis = final.analysis_runs.find((item) => item.analysis_id === submission.analysis_id);
    if (analysis?.status === 'succeeded' || analysis?.status === 'failed') break;
    await page.waitForTimeout(1000);
  }
  if (!final || !analysis || analysis.status !== 'succeeded') {
    throw new Error(`模型分析未成功：${analysis?.status ?? 'timeout'}`);
  }
  const opportunities = final.opportunities.filter((item) =>
    item.analysis_id === submission.analysis_id);
  if (analysis?.mode !== 'live' || analysis?.status !== 'succeeded'
    || opportunities.length > 3 || opportunities.some((item) =>
      item.estimated_minutes < 3 || item.estimated_minutes > 5
      || JSON.parse(item.source_refs_json).some((id) => !preview.source_ref_ids.includes(id)))) {
    throw new Error('分析成功状态、机会数量或来源引用不符');
  }
  for (const opportunity of opportunities) {
    await page.locator('.growth-page__opportunities li')
      .filter({ hasText: opportunity.title }).first().waitFor({ timeout: 30_000 });
  }
  evidence.analysis = analysis;
  evidence.opportunities = opportunities;
  evidence.topic_proposals = final.topic_proposals.filter((item) =>
    opportunities.some((opportunity) => opportunity.opportunity_id === item.opportunity_id));
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_MODEL_OBSERVED ${mode} ${submission.analysis_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
