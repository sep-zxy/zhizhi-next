// Exercise the installed growth product navigation without relying on screenshots.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !outputPath) {
  throw new Error('用法：node m7_growth_product_shell_ui.mjs <exe> <workspace> <output.json>');
}
async function hashFile(filePath) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest('hex');
}
const resources = path.join(path.dirname(executable), 'resources');
const evidence = {
  mode: 'installed_ui',
  status: 'running',
  executable_sha256: await hashFile(executable),
  app_asar_sha256: await hashFile(path.join(resources, 'app.asar')),
  viewer_index_sha256: await hashFile(path.join(resources, 'viewer', 'index.html')),
  workspace,
  started_at: new Date().toISOString(),
  checks: {},
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
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
  await page.context().addCookies([{ name: 'ahadiff_lang', value: 'zh-CN',
    url: new URL(page.url()).origin }]);
  await page.reload();
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const nav = page.locator('nav[aria-label="成长工作台"]');
  const expected = ['今天', '学习', '主题', '项目', '设置'];
  await nav.locator('a').first().waitFor();
  const labels = await nav.locator('a').allInnerTexts();
  evidence.checks.default_today = (await page.locator('h1').first().innerText()) === '今天';
  evidence.checks.five_product_destinations = JSON.stringify(labels) === JSON.stringify(expected);
  evidence.checks.product_title = (await page.title()) === '工程成长伴侣';
  evidence.checks.no_legacy_navigation = !labels.some((label) =>
    /Dashboard|Diff|Quiz|Ratchet|Welcome|Guide|Onboarding/.test(label));
  evidence.navigation_labels = labels;
  for (const [view, title] of [
    ['learning', '学习'], ['topics', '主题'], ['projects', '项目'],
  ]) {
    await nav.locator(`a[href="#/growth?view=${view}"]`).click();
    await page.waitForURL(new RegExp(`#\\/growth\\?view=${view}$`));
    evidence.checks[`view_${view}`] = (await page.locator('h1').first().innerText()) === title;
    if (view === 'learning') {
      evidence.checks.learning_is_focused =
        await page.locator('#growth-learning-context-title').count() === 1
        && await page.locator('#growth-project-name').count() === 0
        && await page.locator('details[open] #growth-sync-title').count() === 0;
    }
    if (view === 'projects') {
      evidence.checks.project_management_is_focused =
        await page.locator('#growth-project-name').count() === 1
        && await page.locator('#growth-learning-context-title').count() === 0;
    }
  }
  await nav.locator('a[href="#/settings"]').click();
  await page.waitForURL(/#\/settings$/);
  await page.locator('#growth-settings-provider').waitFor();
  evidence.settings_observed = {
    heading: await page.locator('h1').first().innerText(),
    provider_count: await page.locator('#growth-settings-provider').count(),
    privacy_count: await page.locator('#growth-settings-privacy-mode').count(),
    legacy_layout_count: await page.locator('.settings-layout').count(),
    alerts: await page.locator('[role="alert"]').allInnerTexts(),
  };
  evidence.checks.settings_only_growth_controls =
    evidence.settings_observed.heading === '设置'
    && evidence.settings_observed.provider_count === 1
    && evidence.settings_observed.privacy_count === 1
    && evidence.settings_observed.legacy_layout_count === 0;
  await page.goto(`${new URL(page.url()).origin}/#/dashboard`);
  await page.waitForURL(/#\/growth\?view=today$/);
  evidence.checks.legacy_route_redirects = (await page.locator('h1').first().innerText()) === '今天';
  evidence.status = Object.values(evidence.checks).every(Boolean) ? 'PASS' : 'FAIL';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`GROWTH_PRODUCT_SHELL_${evidence.status}\n`);
  if (evidence.status !== 'PASS') process.exitCode = 1;
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
