// S01: type N1 in the installed app, then read the same note on another Windows identity.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const { _electron } = createRequire(path.join(root, 'viewer', 'package.json'))('@playwright/test');
const [phase, executable, workspace, credentialsPath, preparationPath, outputPath] =
  process.argv.slice(2);
if (!['create', 'read'].includes(phase) || ![
  executable, workspace, credentialsPath, preparationPath, outputPath,
].every(Boolean)) {
  throw new Error('用法：node m7_s01_same_card_note_ui.mjs <create|read> <exe> <workspace> <credentials.json> <preparation.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const preparation = JSON.parse(await readFile(preparationPath, 'utf8'));
const { card_id: cardId, topic_id: topicId, note_text: noteText } = preparation;
if (!cardId || !topicId || !noteText || (phase === 'read' && !preparation.note_id)) {
  throw new Error('S01 原卡、主题或 N1 输入不完整');
}
const evidence = { status: 'running', phase, account_id: credentials.account_id,
  card_id: cardId, topic_id: topicId, note_id: preparation.note_id ?? null,
  note_text: noteText, started_at: new Date().toISOString(), checks: {} };
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  electron = await _electron.launch({ executablePath: executable,
    args: [`--user-data-dir=${path.join(path.dirname(outputPath), `electron-s01-note-${phase}`)}`],
    env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000 });
  const page = await electron.firstWindow({ timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth?view=all`);
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const details = sync.locator('details').first();
  if (!await details.evaluate(element => element.open)) {
    await details.locator('summary').click();
  }
  await page.locator('#growth-cloud-url').fill(credentials.cloud_url);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  const synchronize = async () => {
    const response = page.waitForResponse(item =>
      new URL(item.url()).pathname === '/api/growth/local/sync', { timeout: 120_000 });
    await sync.locator('form:has(#growth-cloud-url) button').click();
    const completed = await response;
    if (completed.status() !== 200) {
      throw new Error(`S01 安装版云同步失败：${completed.status()}`);
    }
    return completed.json();
  };
  evidence.sync = await synchronize();
  await page.locator(`#growth-topic-select option[value="${topicId}"]`)
    .waitFor({ state: 'attached', timeout: 30_000 });
  await page.locator('#growth-topic-select').selectOption(topicId);
  const panel = page.locator('#growth-topic-create').locator('..');
  if (phase === 'create') {
    await page.locator(`#growth-topic-note-task option[value="${cardId}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-note-task').selectOption(cardId);
    const editor = page.locator('#growth-topic-note');
    evidence.checks.blank_before_input = (await editor.inputValue()).trim() === '';
    if (!evidence.checks.blank_before_input) throw new Error('S01 N1 编辑器初始内容非空');
    await editor.fill(noteText);
    evidence.checks.typed_exact_text = await editor.inputValue() === noteText;
    const response = page.waitForResponse(item =>
      new URL(item.url()).pathname ===
        `/api/growth/local/cloud/topics/${topicId}/notes`, { timeout: 60_000 });
    await panel.getByRole('button', { name: '保存笔记' }).click();
    const created = await response;
    if (created.status() !== 201) {
      throw new Error(`S01 N1 安装版保存失败：${created.status()} ${await created.text()}`);
    }
    const body = await created.json();
    evidence.note_id = body.note_id;
    evidence.checks.saved_note_receipt = !!body.note_id && body.revision === 1;
    if (!evidence.checks.saved_note_receipt) {
      throw new Error('S01 UI 提交 N1 后未获得唯一云端回执');
    }
    evidence.after_write_sync = await synchronize();
  }
  const matching = panel.locator('.growth-page__originals pre').filter({ hasText: noteText });
  await matching.first().waitFor({ timeout: 30_000 });
  evidence.checks.visible_exact_text = await matching.first().innerText() === noteText;
  if (!evidence.checks.visible_exact_text) throw new Error('第二端 UI 所见 N1 原文不一致');
  evidence.status = Object.values(evidence.checks).every(Boolean) ? 'PASS' : 'FAIL';
  evidence.finished_at = new Date().toISOString();
  await save();
  if (evidence.status !== 'PASS') throw new Error('S01 安装版 N1 断言失败');
  process.stdout.write(`S01_SAME_CARD_NOTE_${phase.toUpperCase()}_PASS ${evidence.note_id}\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.stack ?? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
