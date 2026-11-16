// Continue S04 in the installed app: export its confirmed knowledge card to Anki.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [executable, workspace, initialPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !initialPath || !outputPath) {
  throw new Error('用法：node m7_s04_anki_ui.mjs <exe> <workspace> <initial.json> <output.json>');
}
const initial = JSON.parse(await readFile(initialPath, 'utf8'));
const evidence = { status: 'running', mode: 'installed_windows_s04', project_id: initial.project_id,
  started_at: new Date().toISOString() };
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await mkdir(path.dirname(outputPath), { recursive: true });
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-anki')}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=cards&project=${initial.project_id}`);
  const library = page.locator('[aria-labelledby="growth-library-title"]');
  await library.waitFor({ timeout: 30_000 });
  evidence.card_count = await library.locator('.growth-library__card').count();
  if (evidence.card_count < 1) throw new Error('S04 已确认卡片未出现在安装版知识卡片库');
  const target = path.join(path.dirname(outputPath), 'growth-knowledge-cards.apkg');
  await electron.evaluate(({ session }, savePath) => {
    globalThis.__growthAnkiDownload = { state: 'waiting' };
    session.defaultSession.on('will-download', (_event, item) => {
      item.setSavePath(savePath);
      globalThis.__growthAnkiDownload = { filename: item.getFilename(), state: 'started' };
      item.on('done', (_doneEvent, state) => {
        globalThis.__growthAnkiDownload.state = state;
      });
    });
  }, target);
  const response = page.waitForResponse((item) =>
    new URL(item.url()).pathname === '/api/growth/local/cards.apkg', { timeout: 60_000 });
  await library.getByRole('button', { name: '导出项目知识卡为 Anki' }).click();
  const result = await response;
  if (result.status() !== 200) {
    evidence.response_status = result.status();
    evidence.response_text = await result.text();
    throw new Error(`S04 Anki 导出响应：${result.status()} ${evidence.response_text}`);
  }
  let downloaded;
  for (let attempt = 0; attempt < 300; attempt += 1) {
    downloaded = await electron.evaluate(() => globalThis.__growthAnkiDownload);
    if (downloaded?.state === 'completed') break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (downloaded?.state !== 'completed') {
    throw new Error(`S04 Anki 下载未完成：${JSON.stringify(downloaded)}`);
  }
  const sha = createHash('sha256');
  for await (const chunk of createReadStream(target)) sha.update(chunk);
  evidence.filename = downloaded.filename;
  evidence.bytes = (await stat(target)).size;
  evidence.sha256 = sha.digest('hex');
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S04_ANKI_EXPORT_PASS ${evidence.sha256}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally { if (electron) await electron.close(); }
