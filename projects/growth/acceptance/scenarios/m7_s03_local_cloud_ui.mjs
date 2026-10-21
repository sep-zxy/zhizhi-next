// Exercise installed Electron module mapping against an isolated local PostgreSQL cloud.
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, credentialsPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !credentialsPath || !outputPath) {
  throw new Error('用法：node m7_s03_local_cloud_ui.mjs <exe> <workspace> <credentials.json> <output.json>');
}
if (!existsSync(executable)) throw new Error(`安装版可执行文件不存在：${executable}`);
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
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
  const responses = [];
  page.on('response', (response) => {
    const pathname = new URL(response.url()).pathname;
    if (pathname.startsWith('/api/growth/local')) {
      responses.push({ path: pathname, status: response.status() });
    }
  });
  await page.goto(`${origin}/#/growth?view=all`);
  await page.waitForFunction(() => {
    const project = document.querySelector('#growth-project-select');
    const snapshot = document.querySelector('#growth-snapshot-select');
    return project instanceof HTMLSelectElement && !!project.value
      && snapshot instanceof HTMLSelectElement && !!snapshot.value;
  }, null, { timeout: 30_000 });
  const projectId = await page.locator('#growth-project-select').inputValue();
  const snapshotId = await page.locator('#growth-snapshot-select').inputValue();
  if (!projectId || !snapshotId) throw new Error('安装版未恢复项目或源码快照');

  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  const previewResponsePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/growth/local/sync/preview',
    { timeout: 30_000 },
  );
  await sync.locator('details > button').first().click();
  const preview = await (await previewResponsePromise).json();
  if (!Array.isArray(preview.operations)) throw new Error('发布预览缺少操作列表');
  if (preview.operations.length > 0) {
    await sync.locator('input[type="checkbox"]').first().waitFor({ timeout: 30_000 });
    await sync.locator('input[type="checkbox"]').first().check();
  }
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  await sync.locator('form:has(#growth-cloud-url) button').click();
  await sync.locator('details > p[role="status"]').waitFor({ timeout: 120_000 });
  const syncText = await sync.locator('details > p[role="status"]').innerText();
  const syncError = await sync.locator('details > p[role="alert"]').allInnerTexts();
  if (syncError.length) throw new Error(`安装版云同步失败：${syncError.join(' | ')}`);

  const analysisDetails = page.locator('section[aria-labelledby="growth-analysis-title"] details.growth-page__advanced').first();
  if (!await analysisDetails.evaluate((element) => element.open)) {
    await analysisDetails.locator('summary').first().click();
  }
  await page.locator('#growth-code-symbols').fill(
    'OrderController.quote -> OrderService.quote -> PriceCache.get -> PricingGateway.priceCents',
  );
  const indexPanel = page.locator('.growth-page__code-index').first();
  const indexResponsePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname
      === `/api/growth/local/snapshots/${snapshotId}/code-index`,
    { timeout: 180_000 },
  );
  await indexPanel.locator('button').first().click();
  const indexResponse = await indexResponsePromise;
  if (!indexResponse.ok()) throw new Error(`四符号索引 HTTP ${indexResponse.status()}`);
  const codeIndex = await indexResponse.json();
  await indexPanel.locator('.growth-page__capture').waitFor({ timeout: 180_000 });
  const indexText = await indexPanel.locator('.growth-page__capture').innerText();
  if (!indexText.includes('OrderService.quote → PricingGateway.priceCents: 有源码依据')
      || !indexText.includes('PriceCache.get → PricingGateway.priceCents: 未解析，未知')) {
    throw new Error(`索引未区分 lambda 调用与动态装载关系：${indexText}`);
  }

  const modulePanel = page.locator('div[aria-labelledby="growth-module-title"]');
  await modulePanel.locator('#growth-module-name').fill('订单计价');
  const mapResponsePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/growth/local/cloud/modules/map',
    { timeout: 120_000 },
  );
  await modulePanel.locator('button').first().click();
  const mapResponse = await mapResponsePromise;
  const mapBody = await mapResponse.json();
  if (!mapResponse.ok()) {
    throw new Error(`模块保存 HTTP ${mapResponse.status()}：${JSON.stringify(mapBody)}`);
  }
  await page.waitForFunction(() => {
    const panel = document.querySelector('div[aria-labelledby="growth-module-title"]');
    return !!panel?.querySelector('#growth-module-select')
      || !!panel?.querySelector('p[role="alert"]');
  }, null, { timeout: 120_000 });
  const mapErrors = await modulePanel.locator('p[role="alert"]').allInnerTexts();
  if (mapErrors.length) {
    throw new Error(`模块保存失败：${mapErrors.join(' | ')}；响应：${JSON.stringify(responses)}`);
  }
  const moduleId = await modulePanel.locator('#growth-module-select').inputValue();
  if (!moduleId) throw new Error('安装版未选择已保存模块');
  await modulePanel.getByRole('button', { name: '锁定此模块' }).click();
  await page.waitForFunction(() => {
    const select = document.querySelector('#growth-module-select');
    return select instanceof HTMLSelectElement
      && select.selectedOptions[0]?.textContent?.includes('已锁定');
  }, null, { timeout: 120_000 });
  const moduleText = await modulePanel.innerText();
  const evidence = {
    started_at: new Date().toISOString(),
    platform: 'win32-x64',
    cloud_kind: 'isolated_local_postgresql',
    cloud_url: cloudUrl,
    account_id: credentials.account_id,
    project_id: projectId,
    snapshot_id: snapshotId,
    module_id: moduleId,
    sync_text: syncText,
    preview_operation_kinds: preview.operations.map((operation) => operation.kind),
    index_flow: codeIndex.flow,
    index_text: indexText,
    module_text: moduleText,
    responses,
  };
  await writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
  process.stdout.write(`M5_UI_LOCAL_CLOUD_PASS ${moduleId}\n`);
} finally {
  if (electron) await electron.close();
}
