// Rehearse the S02 note conflict in two installed Windows desktop identities.
// It cannot stand in for the required macOS, external cloud, or model steps.
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [phase, executable, installer, workspace, credentialsPath, priorPath, outputPath,
  candidateSha256] = process.argv.slice(2);
const phases = ['create', 'bootstrap', 'offline-edit', 'online-edit', 'conflict-merge', 'verify', 'bind', 'complete'];
if (!phases.includes(phase) || !executable || !installer || !workspace
  || !credentialsPath || !priorPath || !outputPath
  || !/^[0-9a-f]{64}$/.test(candidateSha256 ?? '')) {
  throw new Error('用法：node m7_s02_conflict_ui.mjs <phase> <exe> <installer> <workspace> <credentials> <prior.json> <output.json> <sha256>');
}
if (process.platform !== 'win32' || process.arch !== 'x64') {
  throw new Error(`需要 Windows x64，当前为 ${process.platform}-${process.arch}`);
}
const hash = async (file) => {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(file)) digest.update(chunk);
  return digest.digest('hex');
};
const installerHash = await hash(installer);
if (installerHash !== candidateSha256) throw new Error('安装包哈希不匹配');
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const prior = JSON.parse(await readFile(priorPath, 'utf8'));
if (phase === 'create' ? prior.status !== 'observed_pending_audit'
  : prior.status !== 'PASS') throw new Error('前一阶段未通过');
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
const baseText = '我以前默认先发出的请求先返回。现在用一个递增编号识别最新请求，只有它能更新界面。旧请求仍可能完成，但结果不会覆盖最新输入。';
const offlineText = `${baseText}这个保护只约束 render，不会自动停止网络请求。`;
const onlineText = `${baseText}如果后续要节省请求开销，还要单独考虑取消请求和错误处理。`;
const mergedText = `${baseText}这个保护只约束 render，不会自动停止网络请求。如果后续要节省请求开销，还要单独考虑取消请求和错误处理。`;
const evidence = {
  status: 'running', mode: 'two_windows_devices_local_cloud_rehearsal', phase,
  candidate_sha256: installerHash, installed_exe_sha256: await hash(executable),
  account_id: credentials.account_id, topic_id: prior.topic_id ?? null,
  task_id: prior.task_id ?? null,
  note_id: prior.note_id ?? null, device_a_id: prior.device_a_id ?? null,
  device_b_id: prior.device_b_id ?? null, base_text: baseText,
  offline_text: offlineText, online_text: onlineText, merged_text: mergedText,
  operation_ids: prior.operation_ids ?? {}, checks: {}, actions: [],
  started_at: new Date().toISOString(),
};
await mkdir(path.dirname(outputPath), { recursive: true });
const save = () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
await save();
let electron;
try {
  if (phase === 'offline-edit') {
    let available = false;
    try {
      await fetch(`${cloudUrl}/v1/account`, { signal: AbortSignal.timeout(1500) });
      available = true;
    } catch { /* Connection refusal is the injected outage. */ }
    evidence.checks.cloud_stopped = !available;
    if (available) throw new Error('离线编辑前必须停止隔离云');
  }
  let page;
  for (let startup = 0; startup < 2; startup += 1) {
    try {
      electron = await _electron.launch({
        executablePath: executable,
        args: [`--user-data-dir=${path.join(path.dirname(outputPath), `electron-${phase}`)}`],
        env: { ...process.env, GROWTH_WORKSPACE: workspace }, timeout: 90_000,
      });
      page = await electron.firstWindow({ timeout: 90_000 });
      break;
    } catch (error) {
      if (electron) {
        await electron.close().catch(() => {});
        electron = null;
      }
      if (startup === 1) throw error;
    }
  }
  await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90_000 });
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 90_000 });
  await page.goto(`${new URL(page.url()).origin}/#/growth?view=all`);
  await page.waitForURL(/#\/growth\?view=all$/, { timeout: 30_000 });
  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const notePanel = page.locator('#growth-synced-note').locator('..');
  const topicPanel = page.locator('#growth-topic-create').locator('..');
  const request = async (button, pathname, status) => {
    const waiting = page.waitForResponse((response) =>
      new URL(response.url()).pathname === pathname, { timeout: 90_000 });
    await button.click();
    const response = await waiting;
    const body = await response.json();
    evidence.actions.push({ path: pathname, status: response.status(), body });
    await save();
    if (response.status() !== status) {
      throw new Error(`${pathname}: ${response.status()} ${JSON.stringify(body)}`);
    }
    return body;
  };
  const connect = async () => {
    const details = sync.locator('details').first();
    if (!await details.evaluate((element) => element.open)) {
      await details.locator('summary').click();
    }
    await page.locator('#growth-cloud-url').fill(cloudUrl);
    await page.locator('#growth-cloud-token').fill(credentials.access_token);
    const result = await request(sync.locator('form:has(#growth-cloud-url) button'),
      '/api/growth/local/sync', 200);
    evidence.sync = result;
    return result;
  };
  const selectNote = async (text) => {
    const value = `${credentials.account_id}:${evidence.note_id}`;
    await page.locator(`#growth-synced-note option[value="${value}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-synced-note').selectOption(value);
    await notePanel.locator('.growth-page__originals pre').filter({ hasText: text })
      .first().waitFor({ timeout: 30_000 });
  };
  if (phase === 'create') {
    const connected = await connect();
    evidence.device_b_id = connected.device_id;
    evidence.page_url = page.url();
    evidence.synced_task_control_count = await page.locator('#growth-synced-task').count();
    evidence.topic_control_count = await page.locator('#growth-topic-select').count();
    await save();
    const taskChoice = `${credentials.account_id}:${evidence.task_id}`;
    await page.locator(`#growth-synced-task option[value="${taskChoice}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-synced-task').selectOption(taskChoice);
    const taskPanel = page.locator('#growth-synced-task').locator('..');
    const taskText = await taskPanel.innerText();
    const answer = '最后显示 ab 的结果。a 返回时 requestId 已经不是最新值，因此不会 render。a 的请求本身仍然完成了，这里只是忽略旧结果，没有取消它。';
    const token = await page.evaluate(async () =>
      (await (await fetch('/api/auth/token')).json()).token);
    const readTask = async () => {
      const response = await page.request.get(
        `${new URL(page.url()).origin}/api/growth/local/sync/workspace`,
        { headers: { 'X-AhaDiff-Token': token } });
      if (response.status() !== 200) throw new Error('无法读取第二设备卡片缓存');
      return (await response.json()).tasks.find((task) => task.task_id === evidence.task_id);
    };
    let syncedTask = await readTask();
    const hasAnswer = syncedTask?.attempts.some((item) => item.answer_text === answer);
    evidence.checks.remote_task_paused = syncedTask?.task.progress === 'paused'
      || hasAnswer === true;
    evidence.checks.remote_draft = syncedTask?.remote_draft?.content_text
      === '最后应该显示 ab 的结果，因为返回时还要检查请求编号。'
      || hasAnswer === true;
    evidence.checks.approved_excerpt = taskText.includes('latestRequest')
      && taskText.includes('已批准同步');
    evidence.checks.no_local_binding = taskText.includes('尚未绑定')
      || taskText.includes('未绑定');
    if (!Object.values(evidence.checks).every(Boolean)) {
      throw new Error('第二设备未看到同一卡片、草稿、已批准片段或无绑定状态');
    }
    const previousAnswer = syncedTask?.attempts.find((item) => item.answer_text === answer);
    if (previousAnswer) {
      evidence.attempt_id = previousAnswer.attempt_id ?? previousAnswer.entity_id;
    } else {
      await page.locator('#growth-sync-task-answer').fill(answer);
      const submitted = await request(taskPanel.getByRole('button', { name: '提交离线正式回答' }),
        `/api/growth/local/sync/tasks/${evidence.task_id}/attempts`, 201);
      evidence.attempt_id = submitted.attempt_id;
      await connect();
      syncedTask = await readTask();
    }
    evidence.checks.answer_uploaded = syncedTask?.attempts.some((item) =>
      (item.attempt_id ?? item.entity_id) === evidence.attempt_id
      && item.answer_text === answer) === true;
    if (!evidence.checks.answer_uploaded || !evidence.attempt_id) {
      throw new Error('第二设备正式回答没有以原 attempt ID 上云');
    }
    if (!syncedTask?.attempts.some((item) =>
      (item.attempt_id ?? item.entity_id) === evidence.attempt_id
      && item.status === 'feedback_ready')) {
      await page.locator('#growth-sync-feedback-provider').fill('deepseek');
      const feedbackPreviewPath =
        `/api/growth/local/cloud/attempts/${evidence.attempt_id}/feedback/preview`;
      const feedbackPreview = page.waitForResponse((response) =>
        new URL(response.url()).pathname === feedbackPreviewPath,
      { timeout: 30_000 });
      await taskPanel.getByRole('button', { name: '预览模型反馈请求' }).click();
      const previewed = await feedbackPreview;
      if (previewed.status() !== 200) {
        evidence.feedback_preview_error = {
          status: previewed.status(), body: await previewed.text(),
        };
        await save();
        throw new Error(`第二设备反馈预览失败：${previewed.status()}`);
      }
      evidence.feedback_preview = await previewed.json();
      evidence.checks.feedback_uses_approved_source =
        evidence.feedback_preview.payload_text.includes('latestRequest')
        && evidence.feedback_preview.payload_text.includes(answer);
      await page.getByLabel('我已核对回答与源码片段，同意发送给所选模型').check();
      const feedbackPath =
        `/api/growth/local/cloud/attempts/${evidence.attempt_id}/feedback`;
      const feedbackResponse = page.waitForResponse((response) =>
        new URL(response.url()).pathname === feedbackPath,
      { timeout: 180_000 });
      await taskPanel.getByRole('button', { name: '发送并生成反馈' }).click();
      const generated = await feedbackResponse;
      if (generated.status() !== 200) {
        throw new Error(`第二设备真实反馈生成失败：${generated.status()} ${await generated.text()}`);
      }
      evidence.feedback = await generated.json();
      syncedTask = await readTask();
    }
    evidence.checks.feedback_ready = syncedTask?.attempts.some((item) =>
      (item.attempt_id ?? item.entity_id) === evidence.attempt_id
      && item.status === 'feedback_ready') === true;
    await topicPanel.getByRole('button', { name: '重新读取主题和版本' }).click();
    await page.locator(`#growth-topic-select option[value="${evidence.topic_id}"]`)
      .waitFor({ state: 'attached', timeout: 30_000 });
    await page.locator('#growth-topic-select').selectOption(evidence.topic_id);
    await page.locator('#growth-topic-note').fill(baseText);
    await page.locator('#growth-topic-note-task').selectOption(evidence.task_id);
    const notePath = `/api/growth/local/cloud/topics/${evidence.topic_id}/notes`;
    const saveNote = topicPanel.getByRole('button', { name: '保存笔记' });
    await page.route(`**${notePath}`, async (route) => {
      const first = route.request().postDataJSON();
      evidence.lost_response_request = {
        operation_id: first.operation_id, note_id: first.note_id,
        task_id: first.task_id, content_text: first.content_text,
      };
      const committed = await route.fetch();
      evidence.lost_response_commit = {
        status: committed.status(), body: await committed.json(),
      };
      await save();
      await route.abort('failed');
    }, { times: 1 });
    await saveNote.click();
    await page.waitForFunction(() => {
      const button = document.querySelector('#growth-topic-note-task + button');
      return button instanceof HTMLButtonElement && !button.disabled;
    }, null, { timeout: 30_000 });
    evidence.checks.note_input_survives_lost_response =
      await page.locator('#growth-topic-note').inputValue() === baseText;
    const retryRequest = page.waitForRequest((requestItem) =>
      new URL(requestItem.url()).pathname === notePath, { timeout: 30_000 });
    const retryResponse = page.waitForResponse((response) =>
      new URL(response.url()).pathname === notePath, { timeout: 30_000 });
    await saveNote.click();
    const retried = await retryRequest;
    const retryBody = retried.postDataJSON();
    const retriedResponse = await retryResponse;
    if (retriedResponse.status() !== 201) throw new Error('笔记创建重试失败');
    const created = await retriedResponse.json();
    evidence.checks.same_note_operation_retry =
      evidence.lost_response_commit.status === 201
      && evidence.lost_response_request.operation_id === retryBody.operation_id
      && evidence.lost_response_request.note_id === retryBody.note_id
      && evidence.lost_response_request.task_id === evidence.task_id
      && evidence.lost_response_request.content_text === retryBody.content_text
      && evidence.lost_response_commit.body.note_id === created.note_id;
    evidence.note_id = created.note_id;
    await connect();
    await selectNote(baseText);
    evidence.checks.note_created_once = created.revision === 1;
    evidence.checks.base_cached_on_b = await notePanel.locator('.growth-page__originals pre')
      .filter({ hasText: baseText }).count() > 0;
  } else if (phase === 'bootstrap') {
    const connected = await connect();
    evidence.device_a_id = connected.device_id;
    await selectNote(baseText);
    evidence.checks = { second_identity: connected.device_id !== evidence.device_b_id,
      base_cached_on_a: await notePanel.locator('.growth-page__originals pre')
        .filter({ hasText: baseText }).count() > 0 };
  } else if (phase === 'offline-edit') {
    await selectNote(baseText);
    await page.locator('#growth-sync-note-edit').fill(offlineText);
    const queued = await request(notePanel.getByRole('button', {
      name: '保存修订并等待同步',
    }), `/api/growth/local/sync/notes/${evidence.note_id}/revisions`, 201);
    evidence.operation_ids.offline = queued.operation_id;
    evidence.checks.offline_pending = queued.state === 'pending';
    evidence.checks.base_still_visible = await notePanel.locator('.growth-page__originals pre')
      .filter({ hasText: baseText }).count() > 0;
  } else if (phase === 'online-edit') {
    await connect();
    await selectNote(baseText);
    await page.locator('#growth-sync-note-edit').fill(onlineText);
    const queued = await request(notePanel.getByRole('button', {
      name: '保存修订并等待同步',
    }), `/api/growth/local/sync/notes/${evidence.note_id}/revisions`, 201);
    evidence.operation_ids.online = queued.operation_id;
    const synced = await connect();
    await selectNote(onlineText);
    evidence.checks.online_revision_two = synced.uploaded === 1
      && (await page.locator(`#growth-synced-note option[value="${credentials.account_id}:${evidence.note_id}"]`)
        .textContent())?.includes('r2') === true;
  } else if (phase === 'conflict-merge') {
    const conflicted = await connect();
    await selectNote(onlineText);
    const originals = await notePanel.locator('.growth-page__originals pre').allInnerTexts();
    evidence.conflict_originals = originals;
    evidence.checks.conflict_reported = conflicted.outbox.conflict === 1;
    evidence.checks.three_texts_visible = [baseText, offlineText, onlineText]
      .every((value) => originals.includes(value));
    if (!Object.values(evidence.checks).every(Boolean)) {
      throw new Error('冲突原文未全部显示');
    }
    await page.locator('#growth-sync-note-edit').fill(mergedText);
    const queued = await request(notePanel.getByRole('button', {
      name: '保存合并并等待同步',
    }), `/api/growth/local/sync/notes/${evidence.note_id}/resolve`, 201);
    evidence.operation_ids.merge = queued.operation_id;
    const synced = await connect();
    await selectNote(mergedText);
    evidence.checks.merge_revision_three = queued.state === 'pending'
      && synced.uploaded === 1
      && (await page.locator(`#growth-synced-note option[value="${credentials.account_id}:${evidence.note_id}"]`)
        .textContent())?.includes('r3') === true;
  } else if (phase === 'bind') {
    const connected = await connect();
    await selectNote(mergedText);
    evidence.checks.same_second_identity = connected.device_id === evidence.device_b_id;
    const sourceTask = await page.request.get(
      `${new URL(page.url()).origin}/api/growth/local/sync/workspace`,
      { headers: { 'X-AhaDiff-Token': await page.evaluate(async () =>
        (await (await fetch('/api/auth/token')).json()).token) } });
    if (sourceTask.status() !== 200) throw new Error('无法读取重启后的云端任务');
    const syncedTask = (await sourceTask.json()).tasks.find((task) =>
      task.task_id === evidence.task_id);
    evidence.project_id = syncedTask?.project_id;
    await page.locator('#growth-synced-task').selectOption(
      `${credentials.account_id}:${evidence.task_id}`);
    evidence.checks.merged_note_after_restart = await notePanel
      .locator('.growth-page__originals pre').filter({ hasText: mergedText }).count() > 0;
    const repoPath = path.resolve(workspace, 'repository');
    let bound;
    const token = await page.evaluate(async () =>
      (await (await fetch('/api/auth/token')).json()).token);
    const existingResponse = await page.request.get(`${new URL(page.url()).origin}/api/growth/local`,
      { headers: { 'X-AhaDiff-Token': token } });
    const existingBinding = (await existingResponse.json()).bindings.find((item) =>
      item.project_id === syncedTask?.project_id && item.canonical_local_path === repoPath);
    if (existingBinding) {
      bound = { project_id: existingBinding.project_id, binding_id: existingBinding.binding_id,
        path: existingBinding.canonical_local_path };
    } else if (await page.locator('#growth-synced-repository-path').count()) {
      await page.locator('#growth-synced-repository-path').fill(repoPath);
      bound = await request(page.getByRole('button', { name: '绑定到此已同步项目' }),
        `/api/growth/local/sync/tasks/${evidence.task_id}/binding`, 201);
    } else {
      throw new Error('重试时找不到原第二设备绑定');
    }
    evidence.binding_id = bound.binding_id;
    evidence.checks.same_project_id = bound.project_id === syncedTask?.project_id;
    evidence.checks.second_path = bound.path === repoPath;
    const verified = await request(page.getByRole('button', { name: '核对本机源码版本' }),
      `/api/growth/local/sync/tasks/${evidence.task_id}/source-locations`, 200);
    evidence.source_locations = verified.locations;
    evidence.checks.source_version_matched = verified.locations.some((item) =>
      item.relative_path === 'src/search.ts' && item.status === 'matched'
      && item.local_path === path.join(repoPath, 'src', 'search.ts'));
  } else if (phase === 'complete') {
    await connect();
    await selectNote(mergedText);
    const taskChoice = `${credentials.account_id}:${evidence.task_id}`;
    await page.locator('#growth-synced-task').selectOption(taskChoice);
    const progressControl = page.locator('#growth-sync-task-progress');
    const currentProgress = await progressControl.inputValue();
    for (const target of currentProgress === 'paused'
      ? ['in_progress', 'completed'] : ['completed']) {
      await progressControl.selectOption(target);
      await request(page.getByRole('button', { name: '保存进度并等待同步' }),
        `/api/growth/local/sync/tasks/${evidence.task_id}/progress`, 201);
      await connect();
      await page.locator('#growth-synced-task').selectOption(taskChoice);
    }
    const token = await page.evaluate(async () =>
      (await (await fetch('/api/auth/token')).json()).token);
    const response = await page.request.get(
      `${new URL(page.url()).origin}/api/growth/local/sync/workspace`,
      { headers: { 'X-AhaDiff-Token': token } });
    const task = (await response.json()).tasks.find((item) => item.task_id === evidence.task_id);
    evidence.checks.completed = task?.task.progress === 'completed';
    evidence.checks.one_answer = task?.attempts.length === 1;
    evidence.checks.feedback_preserved = task?.attempts[0]?.status === 'feedback_ready';
    evidence.checks.merged_note_preserved = await notePanel
      .locator('.growth-page__originals pre').filter({ hasText: mergedText }).count() > 0;
    const journeyPath = `/api/growth/local/cloud/tasks/${evidence.task_id}/journey`;
    for (let attempt = 0; attempt < 8; attempt += 1) {
      evidence.journey = await request(page.getByRole('button', {
        name: '查看成长时间线与下次提问',
      }), journeyPath, 200);
      if (evidence.journey.memory_refs.length >= 2) break;
      await page.waitForTimeout(2000);
    }
    evidence.checks.timeline_one_answer_one_note =
      evidence.journey.timeline.filter((item) => item.source_type === 'attempt').length === 1
      && evidence.journey.timeline.filter((item) => item.source_type === 'note').length === 1;
    evidence.checks.pause_and_completion_traced =
      evidence.journey.progress_events.some((item) => item.payload.to === 'paused')
      && evidence.journey.progress_events.some((item) => item.payload.to === 'completed');
    evidence.checks.next_question_scheduled =
      evidence.journey.next_question?.question === task?.task.question;
    evidence.checks.memory_sources_projected =
      evidence.journey.memory_refs.some((item) => item.source_type === 'attempt')
      && evidence.journey.memory_refs.some((item) => item.source_type === 'note');
  } else {
    await connect();
    await selectNote(mergedText);
    evidence.checks.same_account_and_note = evidence.sync.account_id === credentials.account_id;
    evidence.checks.final_text_on_a = await notePanel.locator('.growth-page__originals pre')
      .filter({ hasText: mergedText }).count() > 0;
  }
  if (!Object.values(evidence.checks).every(Boolean)) throw new Error('阶段检查失败');
  evidence.status = 'PASS';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S02_CONFLICT_${phase.toUpperCase()}_PASS\n`);
} catch (error) {
  evidence.status = 'FAIL';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
