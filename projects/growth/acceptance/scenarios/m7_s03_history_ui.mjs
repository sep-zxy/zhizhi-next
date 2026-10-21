// S03: inspect the rebased repository's full local history and author filters in the installed app.
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, preparationPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !preparationPath || !outputPath) {
  throw new Error('用法：node m7_s03_history_ui.mjs <exe> <workspace> <preparation.json> <output.json>');
}
const prepared = JSON.parse(await readFile(preparationPath, 'utf8'));
const evidence = { status: 'running', mode: 'installed_s03_history',
  started_at: new Date().toISOString(), feature_after_rebase: prepared.feature_after_rebase };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-history')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=projects&project=${prepared.project_id}`);
  const panel = page.locator('[aria-labelledby="growth-commits-title"]');
  await panel.waitFor({ timeout: 30_000 });
  const scan = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/bindings\/[^/]+\/commits$/.test(url.pathname) && url.searchParams.get('scan') === 'true';
  }, { timeout: 60_000 });
  await panel.getByRole('button', { name: '扫描本机新提交' }).click();
  const result = await scan;
  if (result.status() !== 200) throw new Error(`历史扫描失败：${result.status()}`);
  const body = await result.json();
  const shas = body.commits.map((item) => item.sha);
  const names = body.authors.map((item) => item.author_name);
  if (!shas.includes(prepared.feature_after_rebase) ||
      !names.includes('缓存功能作者') || !names.includes('订单维护者')) {
    throw new Error('全部引用的历史提交或不同作者身份缺失');
  }
  evidence.commit_count = body.commits.length;
  evidence.authors = body.authors.map((item) => ({ name: item.author_name,
    email: item.author_email, count: item.commit_count }));
  evidence.head_visible = await panel.locator('.growth-page__commit-row')
    .filter({ hasText: prepared.feature_after_rebase.slice(0, 8) }).count() === 1;
  await panel.locator('details summary').click();
  const identity = panel.locator('.growth-workspace__author-list label')
    .filter({ hasText: '缓存功能作者' });
  const filtered = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return /\/bindings\/[^/]+\/commits$/.test(url.pathname) &&
      url.searchParams.has('author_email');
  }, { timeout: 60_000 });
  await identity.locator('input[type="checkbox"]').check();
  const filterResult = await filtered;
  if (filterResult.status() !== 200) throw new Error('作者筛选失败');
  const filteredBody = await filterResult.json();
  if (!filteredBody.commits.length || filteredBody.commits.some((item) =>
    item.author_name !== '缓存功能作者')) throw new Error('作者筛选返回无关提交');
  evidence.filtered_count = filteredBody.commits.length;
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S03_HISTORY_PASS ${evidence.commit_count}\n`);
} catch (error) {
  evidence.status = 'FAIL'; evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString(); await save(); throw error;
} finally { if (electron) await electron.close(); }
