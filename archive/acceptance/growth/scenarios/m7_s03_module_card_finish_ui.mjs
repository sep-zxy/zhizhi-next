// Continue a live S03 installed-card run after card creation, without asking the model again.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, preparationPath, interruptedPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !preparationPath || !interruptedPath || !outputPath) {
  throw new Error('用法：node m7_s03_module_card_finish_ui.mjs <exe> <workspace> <credentials> <preparation> <interrupted> <output>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const earlier = JSON.parse(await readFile(interruptedPath, 'utf8'));
if (earlier.status !== 'failed' || !earlier.task_id || !earlier.opportunity
    || earlier.module_id !== prepared.module_id
    || earlier.preview?.provider_host !== 'https://api.deepseek.com') {
  throw new Error('仅接受已由真实模型生成且已通过安装版创建的卡片');
}
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
if (cloudUrl !== prepared.cloud_origin || credentials.account_id !== prepared.account_id) {
  throw new Error('云端账号与 S03 模块前置不一致');
}
const evidence = {
  mode: 'live_observation', status: 'running', continued_from: path.basename(interruptedPath),
  task_id: earlier.task_id, opportunity_id: earlier.opportunity_id,
  analysis_id: earlier.analysis.analysis_id, module_id: earlier.module_id,
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  const profile = path.join(path.dirname(outputPath), 'electron-profile-s03-card-finish');
  await mkdir(profile, { recursive: true });
  electron = await _electron.launch({
    executablePath: executable, args: [`--user-data-dir=${profile}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
  });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  await page.waitForURL(/#\/growth\?view=all$/, { timeout: 30_000 });
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地授权失败 ${response.status}`);
    return (await response.json()).token;
  });
  const headers = { 'X-AhaDiff-Token': token };
  const taskResponse = await page.request.get(`${origin}/api/growth/local/tasks/${earlier.task_id}`, { headers });
  if (taskResponse.status() !== 200) throw new Error('已创建的本地卡片不可读取');
  const chain = await taskResponse.json();
  const task = chain.task;
  if (task.module_id !== prepared.module_id
      || task.module_index_revision !== prepared.module_index_revision
      || !task.question.includes(earlier.opportunity.learning_goal)
      || !task.question.includes('PriceCache.get')
      || new Set(JSON.parse(task.source_refs_json)).size !== 2
      || !earlier.preview.source_ref_ids.every((id) => JSON.parse(task.source_refs_json).includes(id))) {
    throw new Error('本地任务缺少正确模块、模型问题或双源码引用');
  }
  evidence.local_task = task;
  await save();

  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = sync.locator('details').first();
  if (!await details.evaluate((element) => element.open)) await details.locator('summary').first().click();
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const promise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/growth/local/sync', { timeout: 180_000 });
  await sync.locator('form:has(#growth-cloud-url) button').click();
  const response = await promise;
  if (response.status() !== 200) throw new Error(`卡片上传 HTTP ${response.status()}`);
  evidence.final_sync = await response.json();
  if (evidence.final_sync.outbox.pending !== 0 || evidence.final_sync.outbox.conflict !== 0) {
    throw new Error('卡片上传后仍有待处理或冲突');
  }
  evidence.card_text = earlier.card_text;
  evidence.card_ui_observed_in_prior_session = true;
  if (!evidence.card_text.includes('订单计价')
      || !evidence.card_text.includes(earlier.opportunity.learning_goal)) {
    throw new Error('建卡阶段的安装版界面证据缺少模块或问题');
  }
  evidence.status = 'observed_pending_audit';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_CARD_FINISHED ${earlier.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
