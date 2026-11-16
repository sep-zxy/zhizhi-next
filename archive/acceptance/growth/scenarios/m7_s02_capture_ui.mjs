// Capture the real S02 Git change through the installed Growth UI.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspaceArg, baseSha, outputPath, ...flags] = process.argv.slice(2);
if (!executable || !workspaceArg || !baseSha || !outputPath) {
  throw new Error('用法：node m7_s02_capture_ui.mjs <exe> <workspace> <base_sha> <output.json>');
}
if (flags.some((flag) => !flag.startsWith('--untracked='))) {
  throw new Error('只接受 --untracked=path 选项');
}
const selectedUntracked = flags.map((flag) => flag.slice('--untracked='.length));
const workspace = path.resolve(workspaceArg);
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode: 'live_observation', status: 'running', workspace,
  executable_sha256: hash.digest('hex'), started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-profile-s02-capture')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  await page.locator('#growth-project-name').waitFor({ timeout: 30_000 });
  const token = await page.evaluate(async () => (await (await fetch('/api/auth/token')).json()).token);
  const headers = { 'X-AhaDiff-Token': token };
  const state = async () => {
    const response = await page.request.get(`${origin}/api/growth/local`, { headers });
    if (response.status() !== 200) throw new Error(`本机状态 HTTP ${response.status()}`);
    return response.json();
  };
  let local = await state();
  let project = local.projects.find((item) => item.name === 'search-demo');
  if (!project) {
    await page.locator('#growth-project-name').fill('search-demo');
    await page.locator('form:has(#growth-project-name) button').click();
    await page.waitForFunction(() => !!document.querySelector('#growth-project-select')?.value);
    local = await state();
    project = local.projects.find((item) => item.name === 'search-demo');
  }
  if (!project || local.projects.length !== 1) throw new Error('S02 项目状态不是唯一');
  evidence.project_id = project.project_id;
  await page.locator('#growth-project-select').selectOption(project.project_id);
  let binding = local.bindings.find((item) => item.project_id === project.project_id);
  if (!binding) {
    await page.locator('#growth-repository-path').fill(workspace);
    await page.locator('form:has(#growth-repository-path) button').click();
    await page.waitForFunction(() => !!document.querySelector('#growth-binding-select')?.value);
    local = await state();
    binding = local.bindings.find((item) => item.project_id === project.project_id);
  }
  if (!binding || path.resolve(binding.canonical_local_path).toLowerCase() !== workspace.toLowerCase()) {
    throw new Error('S02 Git 绑定不是本次绝对路径');
  }
  evidence.binding_id = binding.binding_id;
  await page.locator('#growth-binding-select').selectOption(binding.binding_id);
  let feature = local.features.find((item) => item.project_id === project.project_id);
  if (!feature) {
    await page.locator('#growth-feature-label').fill('搜索响应竞态');
    await page.locator('#growth-base-ref').fill(baseSha);
    await page.locator('form:has(#growth-feature-label) button').click();
    await page.waitForFunction(() => !!document.querySelector('#growth-feature-select')?.value);
    local = await state();
    feature = local.features.find((item) => item.project_id === project.project_id);
  }
  if (!feature || feature.base_ref !== baseSha) throw new Error('S02 Feature 基线不一致');
  evidence.feature_id = feature.feature_id;
  await page.locator('#growth-feature-select').selectOption(feature.feature_id);
  if (local.snapshots.length !== 0) throw new Error('S02 快照已存在，拒绝重复捕获');
  for (const name of selectedUntracked) {
    const source = page.locator('section[aria-labelledby="growth-capture-title"]')
      .locator('fieldset.growth-page__untracked label').filter({ hasText: name });
    if (await source.count() !== 1) throw new Error(`S02 未跟踪 Probe 不唯一：${name}`);
    await source.locator('input[type="checkbox"]').check();
  }
  evidence.selected_untracked = selectedUntracked;
  const capturedResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith(`/features/${feature.feature_id}/snapshots`),
  { timeout: 60_000 });
  await page.locator('section[aria-labelledby="growth-capture-title"] button').click();
  const captured = await capturedResponse;
  if (![200, 201].includes(captured.status())) throw new Error(`捕获失败 ${captured.status()}`);
  await page.locator('section[aria-labelledby="growth-capture-title"] .growth-page__capture')
    .waitFor({ timeout: 30_000 });
  local = await state();
  const snapshot = local.snapshots.find((item) => item.feature_id === feature.feature_id);
  if (!snapshot || local.snapshots.length !== 1) throw new Error('S02 快照非唯一');
  evidence.snapshot_id = snapshot.snapshot_id;
  evidence.snapshot = snapshot;
  evidence.capture_text = await page.locator(
    'section[aria-labelledby="growth-capture-title"] .growth-page__capture').innerText();
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_CAPTURE_OBSERVED ${snapshot.snapshot_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
