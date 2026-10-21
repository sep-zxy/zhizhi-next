// Drive the packaged Electron renderer against its real local sidecar.
// Inputs are positional: setup|capture, executable, Git workspace, base SHA, output JSON.
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [mode, executable, workspace, baseSha, outputPath, optionA, optionB] = process.argv.slice(2);
if (!['setup', 'capture'].includes(mode) || !executable || !workspace || !baseSha || !outputPath) {
  throw new Error('用法：node m7_windows_electron.mjs setup|capture <exe> <workspace> <base_sha> <output.json>');
}

const startedAt = new Date().toISOString();
const profileBase = path.join(path.dirname(outputPath),
  `electron-${path.parse(outputPath).name}-profile`);
let electron;
try {
  let page;
  for (let startup = 0; startup < 2; startup += 1) {
    let startupOutput = '';
    try {
      const profile = `${profileBase}-${startup}`;
      await mkdir(profile, { recursive: true });
      electron = await _electron.launch({
        executablePath: executable,
        args: [`--user-data-dir=${profile}`],
        env: { ...process.env, GROWTH_WORKSPACE: workspace },
        timeout: 90_000,
      });
      for (const stream of [electron.process().stdout, electron.process().stderr]) {
        stream?.on('data', (chunk) => {
          startupOutput = (startupOutput + String(chunk)).slice(-5000);
        });
      }
      page = await electron.firstWindow({ timeout: 90_000 });
      break;
    } catch (error) {
      if (electron) {
        await electron.close().catch(() => {});
        electron = null;
      }
      if (startup === 1) {
        throw new Error(`安装版窗口启动失败：${error.message}\n${startupOutput}`);
      }
    }
  }
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  try {
    await page.locator('#growth-project-name').waitFor({ timeout: 30_000 });
  } catch (error) {
    const headings = await page.locator('h1').allInnerTexts();
    const alerts = await page.locator('[role="alert"]').allInnerTexts();
    throw new Error(`项目表单未出现：URL=${page.url()} headings=${JSON.stringify(headings)} alerts=${JSON.stringify(alerts)}; ${error.message}`);
  }

  let observed;
  if (mode === 'setup') {
    await page.locator('#growth-project-name').fill(optionA || 'route-demo');
    await page.locator('form:has(#growth-project-name) button').click();
    await page.waitForFunction(() => {
      const selected = document.querySelector('#growth-project-select');
      return selected instanceof HTMLSelectElement && selected.value;
    });
    const projectId = await page.locator('#growth-project-select').inputValue();
    await page.locator('#growth-repository-path').fill(workspace);
    await page.locator('form:has(#growth-repository-path) button').click();
    await page.waitForFunction(() => {
      const selected = document.querySelector('#growth-binding-select');
      return selected instanceof HTMLSelectElement && selected.value;
    });
    const bindingId = await page.locator('#growth-binding-select').inputValue();
    await page.locator('#growth-feature-label').fill(optionB || '并发路线查询');
    await page.locator('#growth-base-ref').fill(baseSha);
    await page.locator('form:has(#growth-feature-label) button').click();
    await page.waitForFunction(() => {
      const selected = document.querySelector('#growth-feature-select');
      return selected instanceof HTMLSelectElement && selected.value;
    });
    const featureId = await page.locator('#growth-feature-select').inputValue();
    observed = { project_id: projectId, binding_id: bindingId, feature_id: featureId };
  } else {
    try {
      await page.locator('#growth-feature-select').waitFor({ timeout: 30_000 });
    } catch (error) {
      const headings = await page.locator('h1').allInnerTexts();
      const alerts = await page.locator('[role="alert"]').allInnerTexts();
      const status = await page.locator('[role="status"]').allInnerTexts();
      throw new Error(`Feature 选择框未出现：URL=${page.url()} headings=${JSON.stringify(headings)} alerts=${JSON.stringify(alerts)} status=${JSON.stringify(status)}; ${error.message}`);
    }
    const projectId = await page.locator('#growth-project-select').inputValue();
    const bindingId = await page.locator('#growth-binding-select').inputValue();
    const featureId = await page.locator('#growth-feature-select').inputValue();
    if (optionA) {
      const untrackedDetails = page.locator('section[aria-labelledby="growth-capture-title"] details.growth-page__advanced');
      if (!await untrackedDetails.evaluate((element) => element.open)) {
        await untrackedDetails.locator('summary').click();
      }
      const untracked = page.locator('.growth-page__untracked label').filter({ hasText: optionA });
      await untracked.locator('input').check({ timeout: 30_000 });
    }
    await page.locator('section[aria-labelledby="growth-capture-title"] button').click();
    const capture = page.locator('section[aria-labelledby="growth-capture-title"]')
      .locator('.growth-page__capture');
    await capture.waitFor({ timeout: 30_000 });
    const snapshotId = await page.locator('#growth-snapshot-select').inputValue();
    let analysisText = null;
    const diagnostics = page.locator('section[aria-labelledby="growth-analysis-title"]')
      .locator('details.growth-page__advanced');
    await diagnostics.locator('summary').click();
    if (!optionB) {
      await diagnostics.locator(':scope > button').click();
      await page.waitForFunction(() => {
        const entry = document.querySelector('.growth-page__analyses li');
        return entry && /Completed|已完成|Failed|失败/.test(entry.textContent || '');
      }, null, { timeout: 180_000 });
      analysisText = await page.locator('.growth-page__analyses li').first().innerText();
      if (/Failed|失败/.test(analysisText)) throw new Error(`本机诊断失败：${analysisText}`);
    }
    let codeIndexText = null;
    if (optionB) {
      await page.locator('#growth-code-symbols').fill(optionB);
      await page.locator('.growth-page__code-index').first().locator('button').first().click();
      const codeIndex = page.locator('.growth-page__code-index').first()
        .locator('.growth-page__capture');
      await codeIndex.waitFor({ timeout: 180_000 });
      codeIndexText = await codeIndex.innerText();
    }
    observed = {
      project_id: projectId, binding_id: bindingId, feature_id: featureId,
      snapshot_id: snapshotId, capture_text: await capture.innerText(),
      analysis_text: analysisText,
      selected_untracked: optionA || null,
      code_index_text: codeIndexText,
    };
  }

  const evidence = {
    mode, started_at: startedAt, finished_at: new Date().toISOString(),
    ui_origin: origin, workspace, ...observed,
  };
  await writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
  process.stdout.write(`${JSON.stringify(evidence)}\n`);
} finally {
  if (electron) await electron.close();
}
