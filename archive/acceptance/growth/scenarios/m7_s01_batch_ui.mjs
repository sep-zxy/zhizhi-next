// S01: confirm the model recommendation in the installed batch workflow.
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, modelPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !modelPath || !outputPath) {
  throw new Error('用法：node m7_s01_batch_ui.mjs <exe> <workspace> <model.json> <output.json>');
}
const model = JSON.parse(await readFile(modelPath, 'utf8'));
const opportunity = model.opportunities[0];
const evidence = { status: 'running', mode: 'installed_s01_batch',
  started_at: new Date().toISOString(), opportunity_id: opportunity.opportunity_id };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-batch')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=learning&project=${model.project_id}`);
  const panel = page.locator('[aria-labelledby="growth-recommendations-title"]');
  await panel.waitFor({ timeout: 30_000 });
  const recommendation = panel.locator('.growth-library__card').filter({ hasText: opportunity.title });
  if (await recommendation.count() !== 1) throw new Error('S01 待确认推荐未唯一显示');
  await recommendation.locator('input[type="checkbox"]').check();
  const response = page.waitForResponse((item) =>
    new URL(item.url()).pathname === '/api/growth/local/recommendations/decisions',
  { timeout: 30_000 });
  await panel.getByRole('button', { name: /确认所选 1 项并生成卡片/ }).click();
  const decided = await response;
  if (decided.status() !== 200) throw new Error(`S01 批量确认失败：${decided.status()}`);
  const result = await decided.json();
  if (result.created_card_count !== 1) throw new Error('S01 批量确认没有生成唯一正式卡片');
  const token = await page.evaluate(async () =>
    (await (await fetch('/api/auth/token')).json()).token);
  const stateResponse = await page.request.get(`${origin}/api/growth/local`,
    { headers: { 'X-AhaDiff-Token': token } });
  const state = await stateResponse.json();
  const card = state.growth_tasks.find((item) => item.opportunity_id === opportunity.opportunity_id);
  const proposal = state.topic_proposals.find((item) =>
    item.opportunity_id === opportunity.opportunity_id && item.status === 'confirmed');
  if (!card || !proposal || card.topic_id !== proposal.topic_id) {
    throw new Error('S01 卡片与已确认主题关系不完整');
  }
  evidence.card_id = card.card_id ?? card.task_id;
  evidence.task_id = card.task_id;
  evidence.topic_id = proposal.topic_id;
  evidence.created_card_count = result.created_card_count;
  evidence.status = 'PASS'; evidence.finished_at = new Date().toISOString();
  await save(); process.stdout.write(`S01_BATCH_PASS ${evidence.card_id}\n`);
} catch (error) {
  evidence.status = 'FAIL'; evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString(); await save(); throw error;
} finally { if (electron) await electron.close(); }
