// Rehearse offline draft and answer entry through the installed Electron UI.
// The cached card comes from a replay fixture, so this is not a formal S02 step.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createReadStream, existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, installer, workspace, chainPath, outputPath, candidateSha256] =
  process.argv.slice(2);
if (!executable || !installer || !workspace || !chainPath || !outputPath
  || !/^[0-9a-f]{64}$/.test(candidateSha256 ?? '')) {
  throw new Error('用法：node m7_s02_installed_offline_write_ui.mjs <exe> <installer> <workspace> <chain.json> <output.json> <candidate-sha256>');
}
if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error(`此预演要求 Windows x64，实际为 ${process.platform}-${process.arch}`);
}
for (const required of [executable, installer, chainPath,
  path.join(workspace, '.ahadiff', 'growth.sqlite')]) {
  if (!existsSync(required)) throw new Error(`预演输入不存在：${required}`);
}
const repositoryRoot = execFileSync('git', ['-C', workspace, 'rev-parse', '--show-toplevel'], {
  encoding: 'utf8',
}).trim();
if (path.resolve(repositoryRoot).toLowerCase() !== path.resolve(workspace).toLowerCase()) {
  throw new Error(`测试工作区 Git 根目录不匹配：${repositoryRoot}`);
}
async function sha256(file) {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(file)) digest.update(chunk);
  return digest.digest('hex');
}
const installerSha256 = await sha256(installer);
if (installerSha256 !== candidateSha256) throw new Error('安装包 SHA-256 不匹配');
const chain = JSON.parse(await readFile(chainPath, 'utf8'));
const draftText = '最后应该显示 ab 的结果，因为返回时还要检查请求编号。';
const answerText = '最后显示 ab 的结果。a 返回时 requestId 已经不是最新值，因此不会 render。a 的请求本身仍然完成了，这里只是忽略旧结果，没有取消它。';
const evidence = {
  mode: 'replay_not_live',
  status: 'running',
  platform: `${process.platform}-${process.arch}`,
  installer_sha256: installerSha256,
  installed_exe_sha256: await sha256(executable),
  account_id: chain.account_id,
  task_id: chain.task_id,
  parent_attempt_id: chain.attempt_ids.at(-1),
  draft_text: draftText,
  answer_text: answerText,
  checks: {},
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();

async function launch(visit) {
  let electron;
  try {
    electron = await _electron.launch({
      executablePath: executable,
      args: [`--user-data-dir=${path.join(path.dirname(outputPath), 'electron-profile')}`],
      env: { ...process.env, GROWTH_WORKSPACE: workspace },
      timeout: 90_000,
    });
    const page = await electron.firstWindow({ timeout: 90_000 });
    await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
    const origin = new URL(page.url()).origin;
    await page.goto(`${origin}/#/growth`);
    await page.locator('#growth-synced-task option').first().waitFor({
      state: 'attached', timeout: 30_000,
    });
    const taskValue = `${chain.account_id}:${chain.task_id}`;
    await page.locator('#growth-synced-task').selectOption(taskValue);
    const panel = page.locator('#growth-synced-task').locator('..');
    await visit(page, panel);
  } finally {
    if (electron) await electron.close();
  }
}

try {
  await launch(async (page, panel) => {
    const progress = await page.locator('#growth-sync-task-progress').inputValue();
    if (progress !== 'in_progress') {
      throw new Error('源缓存卡片不是进行中状态');
    }
    await page.locator('#growth-sync-task-answer').fill(draftText);
    const savedPromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/sync/tasks/${chain.task_id}/draft`),
    { timeout: 30_000 });
    await panel.getByRole('button', { name: '保存离线草稿' }).click();
    const saved = await savedPromise;
    if (saved.status() !== 201) throw new Error(`草稿入队失败：${saved.status()}`);
    evidence.draft_receipt = await saved.json();
    await panel.getByText(draftText, { exact: true }).waitFor({ timeout: 30_000 });
    evidence.checks.draft_written_by_installed_ui = true;
    await save();
  });
  await launch(async (page, panel) => {
    await panel.getByText(draftText, { exact: true }).waitFor({ timeout: 30_000 });
    await panel.getByRole('button', { name: '载入已保存草稿到编辑框' }).click();
    evidence.checks.draft_survives_restart =
      await page.locator('#growth-sync-task-answer').inputValue() === draftText;
    if (!evidence.checks.draft_survives_restart) throw new Error('重启后草稿载入不一致');
    await page.locator('#growth-sync-task-answer').fill(answerText);
    await page.locator('#growth-sync-task-parent').selectOption(chain.attempt_ids.at(-1));
    const savedPromise = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(`/sync/tasks/${chain.task_id}/attempts`),
    { timeout: 30_000 });
    await panel.getByRole('button', { name: '提交离线正式回答' }).click();
    const saved = await savedPromise;
    if (saved.status() !== 201) throw new Error(`回答入队失败：${saved.status()}`);
    evidence.answer_receipt = await saved.json();
    await panel.getByText(answerText, { exact: true }).first().waitFor({ timeout: 30_000 });
    evidence.checks.answer_written_by_installed_ui = true;
    await save();
  });
  await launch(async (_page, panel) => {
    const pendingAnswer = panel.locator('strong').filter({
      hasText: '本机回答原文 · pending',
    }).locator('..').locator('pre');
    await pendingAnswer.waitFor({ timeout: 30_000 });
    evidence.checks.answer_survives_restart = await pendingAnswer.innerText() === answerText;
    evidence.checks.progress_waits_for_sync = await panel.getByRole('button', {
      name: '保存进度并等待同步',
    }).isDisabled();
    if (!evidence.checks.answer_survives_restart
      || !evidence.checks.progress_waits_for_sync) {
      throw new Error('重启后待同步回答或进度门槛不一致');
    }
  });
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write('S02_INSTALLED_OFFLINE_WRITE_REPLAY_PASS\n');
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
}
