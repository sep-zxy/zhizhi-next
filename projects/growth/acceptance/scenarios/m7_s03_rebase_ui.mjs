// Verify the installed S03 card survives rebase and reindex its locked module.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, preparationPath, credentialsPath, outputPath, ...flags] = process.argv.slice(2);
if (!executable || !preparationPath || !credentialsPath || !outputPath
    || flags.some((flag) => !['--live', '--resume'].includes(flag))) {
  throw new Error('用法：node m7_s03_rebase_ui.mjs <exe> <preparation.json> <credentials.json> <output.json> [--live]');
}
const live = flags.includes('--live');
const resume = flags.includes('--resume');
const previous = resume ? JSON.parse(await readFile(outputPath, 'utf8')) : null;
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
if (credentials.account_id !== prepared.account_id ||
    `http://127.0.0.1:${credentials.port}` !== prepared.cloud_origin) {
  throw new Error('S03 rebase 账号不一致');
}
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  ...(previous ?? {}),
  mode: live ? 'live_observation' : 'replay_not_live', status: 'running',
  executable_sha256: hash.digest('hex'), workspace: prepared.workspace,
  account_id: prepared.account_id, task_id: prepared.task_id,
  module_id: prepared.module_id, started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({
    executablePath: executable, args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: prepared.workspace }, timeout: 90_000,
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
  const headers = { 'X-AhaDiff-Token': token };
  const chain = async () => {
    const response = await page.request.get(
      `${origin}/api/growth/local/tasks/${prepared.task_id}`, { headers });
    if (response.status() !== 200) throw new Error('旧模块卡片不可读');
    return response.json();
  };
  const oldChain = await chain();
  if (oldChain.task.module_id !== prepared.module_id ||
      oldChain.task.module_index_revision !== prepared.module_index_revision ||
      oldChain.attempts.length !== 2 ||
      oldChain.attempts.some((attempt) => attempt.status !== 'feedback_ready')) {
    throw new Error('rebase 后旧卡片、模块索引或回答反馈链不完整');
  }
  evidence.old_chain = {
    question: oldChain.task.question,
    source_refs: JSON.parse(oldChain.task.source_refs_json),
    attempt_ids: oldChain.attempts.map((attempt) => attempt.attempt_id),
    module_index_revision: oldChain.task.module_index_revision,
  };
  if (resume && previous.old_snapshot_id) {
    await page.locator('#growth-snapshot-select').selectOption(previous.old_snapshot_id);
  }
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: oldChain.opportunity.title });
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.getByText(oldChain.task.question, { exact: false }).first()
    .waitFor({ timeout: 30_000 });
  evidence.old_card_opened = true;
  await save();

  const syncPanel = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = syncPanel.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  await page.locator('#growth-cloud-url').fill(prepared.cloud_origin);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const firstSync = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await syncPanel.locator('form:has(#growth-cloud-url) button').click();
  const first = await firstSync;
  if (first.status() !== 200) throw new Error(`旧卡片账号同步失败：${first.status()}`);
  evidence.initial_sync = await first.json();
  if (evidence.initial_sync.account_id !== prepared.account_id) {
    throw new Error('旧卡片云账号不一致');
  }

  const projectId = await page.locator('#growth-project-select').inputValue();
  const featureId = await page.locator('#growth-feature-select').inputValue();
  const oldSnapshotId = await page.locator('#growth-snapshot-select').inputValue();
  if (!projectId || !featureId || !oldSnapshotId) {
    throw new Error('S03 rebase 工作区未恢复项目/Feature/旧快照');
  }
  evidence.project_id = projectId;
  evidence.old_snapshot_id = resume ? previous.old_snapshot_id : oldSnapshotId;
  if (!resume) {
  const capture = page.waitForResponse((response) =>
    new URL(response.url()).pathname ===
      `/api/growth/local/features/${featureId}/snapshots`, { timeout: 120_000 });
  await page.locator('section[aria-labelledby="growth-capture-title"]')
    .getByRole('button', { name: '捕获当前变化' }).click();
  const captured = await capture;
  if (captured.status() !== 201) throw new Error(`rebase 后捕获失败：${captured.status()}`);
  evidence.new_snapshot = await captured.json();
  if (evidence.new_snapshot.snapshot_id === oldSnapshotId ||
      evidence.new_snapshot.head_sha !== prepared.feature_after_rebase) {
    throw new Error('新快照没有绑定 rebase 后 HEAD');
  }
  await page.locator('#growth-snapshot-select').selectOption(evidence.new_snapshot.snapshot_id);
  await save();

  const previewResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync/preview',
    { timeout: 30_000 });
  await syncPanel.locator('details > button').first().click();
  const preview = await previewResponse;
  if (preview.status() !== 200) throw new Error('新快照发布预览失败');
  evidence.sync_preview = await preview.json();
  if (!evidence.sync_preview.operations?.some((operation) =>
    operation.kind === 'snapshot' && operation.source_id === evidence.new_snapshot.snapshot_id)) {
    throw new Error('发布预览缺少 rebase 后快照');
  }
  await syncPanel.locator('input[type="checkbox"]').first().check();
  const publishResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await syncPanel.locator('form:has(#growth-cloud-url) button').click();
  const published = await publishResponse;
  if (published.status() !== 200) throw new Error(`新快照同步失败：${published.status()}`);
  evidence.snapshot_sync = await published.json();
  if (evidence.snapshot_sync.outbox.pending !== 0 ||
      evidence.snapshot_sync.outbox.conflict !== 0) {
    throw new Error('新快照同步后仍有 pending/conflict');
  }
  } else {
    if (!evidence.new_snapshot?.snapshot_id || !evidence.snapshot_sync) {
      throw new Error('S03 rebase 续跑缺少已发布快照');
    }
    await page.locator('#growth-snapshot-select').selectOption(evidence.new_snapshot.snapshot_id);
  }

  const analysisAdvanced = page.locator('section[aria-labelledby="growth-analysis-title"] details.growth-page__advanced').first();
  if (!await analysisAdvanced.evaluate((element) => element.open)) {
    await analysisAdvanced.locator('summary').first().click();
  }

  await page.locator('#growth-code-symbols').fill(
    'OrderController.quote -> OrderService.quote -> PriceCache.get -> PricingGateway.priceCents');
  const indexResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname ===
      `/api/growth/local/snapshots/${evidence.new_snapshot.snapshot_id}/code-index`,
    { timeout: 180_000 });
  await page.locator('div[aria-labelledby="growth-code-index-title"]')
    .getByRole('button', { name: '建立源码索引' }).click();
  const indexed = await indexResponse;
  if (indexed.status() !== 200) throw new Error(`新快照索引失败：${indexed.status()}`);
  evidence.new_index = await indexed.json();
  if (evidence.new_index.snapshot_id !== evidence.new_snapshot.snapshot_id ||
      evidence.new_index.index_revision === prepared.module_index_revision) {
    throw new Error('rebase 后索引未产生新修订');
  }
  const modulePanel = page.locator('div[aria-labelledby="growth-module-title"]');
  await modulePanel.locator(`#growth-module-select option[value="${prepared.module_id}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await modulePanel.locator('#growth-module-select').selectOption(prepared.module_id);
  const reindexResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/cloud/modules/map',
    { timeout: 120_000 });
  await modulePanel.getByRole('button', { name: '按当前快照重新索引所选模块' }).click();
  const reindexed = await reindexResponse;
  evidence.reindex_http_status = reindexed.status();
  evidence.reindex = await reindexed.json();
  if (reindexed.status() !== 200 || evidence.reindex.module_id !== prepared.module_id ||
      evidence.reindex.pending_adjustment) {
    throw new Error(`已锁定模块重新索引失败：${JSON.stringify(evidence.reindex)}`);
  }
  await modulePanel.getByText('模块已更新到当前快照', { exact: false })
    .waitFor({ timeout: 30_000 });
  const after = await chain();
  if (after.task.module_index_revision !== prepared.module_index_revision ||
      after.attempts.length !== 2 || after.attempts[0].answer_text !==
        oldChain.attempts[0].answer_text ||
      after.attempts[1].answer_text !== oldChain.attempts[1].answer_text) {
    throw new Error('重新索引覆盖旧卡片来源或回答');
  }
  evidence.old_card_after_reindex = {
    module_index_revision: after.task.module_index_revision,
    source_refs: JSON.parse(after.task.source_refs_json),
    attempt_ids: after.attempts.map((attempt) => attempt.attempt_id),
  };
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_REBASE_REINDEX_OBSERVED ${prepared.module_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
