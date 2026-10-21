// Inspect persisted offline S02 records in the installed Electron app.
// The source database is a replay fixture; this is not a formal live step.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createReadStream, existsSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, installer, workspace, queuePath, outputPath, candidateSha256] =
  process.argv.slice(2);
if (!executable || !installer || !workspace || !queuePath || !outputPath
  || !/^[0-9a-f]{64}$/.test(candidateSha256 ?? '')) {
  throw new Error('用法：node m7_s02_installed_offline_ui.mjs <exe> <installer> <workspace> <queue.json> <output.json> <candidate-sha256>');
}
if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error(`此安装版预演要求 Windows x64，实际为 ${process.platform}-${process.arch}`);
}
for (const required of [executable, installer, queuePath,
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
if (installerSha256 !== candidateSha256) {
  throw new Error(`安装包哈希不匹配：${installerSha256}`);
}
const queue = JSON.parse(await readFile(queuePath, 'utf8'));
const note = queue.note_drafts.find((item) => item.state === 'pending');
const answer = queue.pending_answers.find((item) => item.state === 'pending');
const taskDraft = queue.operations.find((item) => item.path.endsWith('/draft'));
if (!note || !answer || !taskDraft || taskDraft.state !== 'pending') {
  throw new Error('源数据缺少待同步笔记、草稿或回答');
}
const expected = {
  note_id: note.note_id,
  note_operation_id: note.operation_id,
  note_text: note.content_text,
  task_id: taskDraft.path.match(/\/tasks\/([^/]+)\/draft$/)?.[1],
  draft_operation_id: taskDraft.operation_id,
  draft_text: taskDraft.payload.content_text,
  attempt_id: answer.attempt_id,
  answer_operation_id: answer.operation_id,
  answer_text: answer.answer_text,
};
if (!expected.task_id) throw new Error('源任务草稿路径无效');
const evidence = {
  mode: 'replay_not_live',
  status: 'running',
  platform: `${process.platform}-${process.arch}`,
  installer_sha256: installerSha256,
  installed_exe_sha256: await sha256(executable),
  source_queue: queuePath,
  expected_ids: {
    note_id: expected.note_id,
    note_operation_id: expected.note_operation_id,
    task_id: expected.task_id,
    draft_operation_id: expected.draft_operation_id,
    attempt_id: expected.attempt_id,
    answer_operation_id: expected.answer_operation_id,
  },
  checks: {},
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();

async function inspect(launchNumber) {
  const profile = path.join(path.dirname(outputPath), 'electron-profile');
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
    const origin = new URL(page.url()).origin;
    const responses = [];
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('response', (response) => {
      if (new URL(response.url()).pathname === '/api/growth/local/sync/workspace') {
        responses.push(response.status());
      }
    });
    await page.goto(`${origin}/#/growth`);
    try {
      await page.locator('#growth-synced-note option').first().waitFor({
        state: 'attached', timeout: 30_000,
      });
      await page.locator('#growth-synced-task option').first().waitFor({
        state: 'attached', timeout: 30_000,
      });
    } catch (error) {
      evidence.diagnostics = {
        launch_number: launchNumber,
        url: page.url(),
        workspace_api_statuses: responses,
        page_errors: pageErrors,
        body_text: (await page.locator('body').innerText()).slice(0, 1800),
      };
      await save();
      throw error;
    }
    const noteOptions = await page.locator('#growth-synced-note option').evaluateAll(
      (options) => options.map((option) => option.value),
    );
    const taskOptions = await page.locator('#growth-synced-task option').evaluateAll(
      (options) => options.map((option) => option.value),
    );
    const noteValue = noteOptions.find((value) => value.endsWith(`:${expected.note_id}`));
    const taskValue = taskOptions.find((value) => value.endsWith(`:${expected.task_id}`));
    if (!noteValue || !taskValue) throw new Error('安装版未显示源账号中的笔记或卡片');
    await page.locator('#growth-synced-note').selectOption(noteValue);
    await page.locator('#growth-synced-task').selectOption(taskValue);
    const notePanel = page.locator('#growth-synced-note').locator('..');
    const taskPanel = page.locator('#growth-synced-task').locator('..');
    const currentDraft = taskPanel.locator('strong').filter({
      hasText: '本机草稿原文 · pending',
    }).locator('..').locator('pre');
    const pendingAnswer = taskPanel.locator('strong').filter({
      hasText: '本机回答原文 · pending',
    }).locator('..').locator('pre');
    try {
      await notePanel.getByText(expected.note_text, { exact: true }).waitFor({ timeout: 30_000 });
      await currentDraft.waitFor({ timeout: 30_000 });
      await pendingAnswer.waitFor({ timeout: 30_000 });
    } catch (error) {
      evidence.diagnostics = {
        launch_number: launchNumber,
        url: page.url(),
        workspace_api_statuses: responses,
        page_errors: pageErrors,
        note_panel_text: (await notePanel.innerText()).slice(0, 2000),
        task_panel_text: (await taskPanel.innerText()).slice(0, 2000),
      };
      await save();
      throw error;
    }
    const observed = {
      workspace_api_ok: responses.includes(200),
      note_original_visible: await notePanel.getByText(expected.note_text, { exact: true }).count() > 0,
      note_pending_visible: await notePanel.getByText('本地版本状态：pending').isVisible(),
      current_draft_pending: await currentDraft.innerText() === expected.answer_text,
      answer_pending: await pendingAnswer.innerText() === expected.answer_text,
      progress_blocked_until_sync: await taskPanel.getByRole('button', {
        name: '保存进度并等待同步',
      }).isDisabled(),
    };
    evidence.checks[`launch_${launchNumber}`] = observed;
    await save();
    if (Object.values(observed).some((value) => !value)) {
      throw new Error(`第 ${launchNumber} 次启动的离线状态断言失败：${JSON.stringify(observed)}`);
    }
  } finally {
    if (electron) await electron.close();
  }
}

try {
  await inspect(1);
  await inspect(2);
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write('S02_INSTALLED_OFFLINE_REPLAY_PASS\n');
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
}
