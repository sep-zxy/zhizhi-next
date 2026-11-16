// Drive installed feedback preview, approval, and persistence for two saved answers.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, taskId, bindingId, firstId, correctionId, outputPath, modeFlag] =
  process.argv.slice(2);
if (!executable || !workspace || !taskId || !bindingId || !firstId || !correctionId
  || !outputPath || (modeFlag && modeFlag !== '--replay')) {
  throw new Error('用法：node m7_s01_feedback_ui.mjs <exe> <workspace> <task-id> <binding-id> <first-id> <correction-id> <output.json> [--replay]');
}
const mode = modeFlag === '--replay' ? 'replay_not_live' : 'live_observation';
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode,
  status: 'running',
  executable_sha256: hash.digest('hex'),
  workspace,
  task_id: taskId,
  binding_id: bindingId,
  started_at: new Date().toISOString(),
  turns: [],
};
await mkdir(path.dirname(outputPath), { recursive: true });
const profile = path.join(path.dirname(outputPath), 'electron-profile');
await mkdir(profile, { recursive: true });
const save = async () => writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
const match = (response, suffix) => new URL(response.url()).pathname.endsWith(suffix);
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
  await page.waitForURL(/#\/growth\?view=today$/, { timeout: 30_000 });
  const origin = new URL(page.url()).origin;
  await page.goto(`${origin}/#/growth`);
  const token = await page.evaluate(async () => {
    const response = await fetch('/api/auth/token');
    if (!response.ok) throw new Error(`本地凭据引导失败：${response.status}`);
    return (await response.json()).token;
  });
  const headers = { 'X-AhaDiff-Token': token };
  const readChain = async () => {
    const response = await page.request.get(`${origin}/api/growth/local/tasks/${taskId}`, { headers });
    if (response.status() !== 200) throw new Error(`卡片链读取失败：${response.status()}`);
    return response.json();
  };
  const original = await readChain();
  if (original.task.task_id !== taskId || original.attempts.length !== 2
    || original.attempts[0].attempt_id !== firstId
    || original.attempts[1].attempt_id !== correctionId
    || original.attempts[1].parent_attempt_id !== firstId) {
    throw new Error('反馈前置必须是保留原文的两次关联回答');
  }
  const row = page.locator('.growth-page__opportunities li')
    .filter({ hasText: original.opportunity.title });
  await row.getByRole('button', { name: /打开卡片/ }).click();
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  for (const [index, attemptId] of [firstId, correctionId].entries()) {
    const source = original.attempts[index];
    const turn = { index: index + 1, attempt_id: attemptId, original_answer: source.answer_text };
    evidence.turns.push(turn);
    await save();
    const attemptRow = card.locator('ul').first().locator('li')
      .filter({ hasText: source.answer_text });
    const previewPromise = page.waitForResponse((response) =>
      match(response, `/attempts/${attemptId}/feedback-preview`), { timeout: 30_000 });
    await attemptRow.getByRole('button', { name: /预览模型反馈请求|重新预览并重试反馈/ }).click();
    const previewResponse = await previewPromise;
    if (previewResponse.status() !== 200) {
      throw new Error(`第 ${index + 1} 次反馈预览失败：${previewResponse.status()}`);
    }
    const preview = await previewResponse.json();
    const shown = await card.locator('pre.growth-page__payload').last().innerText();
    if (shown !== preview.payload_text || !shown.includes(source.answer_text)) {
      throw new Error(`第 ${index + 1} 次反馈 UI 预览正文不匹配`);
    }
    if (mode === 'live_observation' && /(?:acceptance|fake|replay)/i.test(preview.model_name)) {
      throw new Error('live 观察拒绝固定响应模型');
    }
    turn.preview = {
      payload_sha256: createHash('sha256').update(preview.payload_text).digest('hex'),
      approval_hash: preview.approval_hash,
      provider_host: preview.provider_host,
      model_name: preview.model_name,
      ui_payload_exact_match: true,
    };
    await save();
    await card.getByLabel('我已核对上述内容，同意发送给所选 provider。').check();
    const submitPromise = page.waitForResponse((response) =>
      match(response, `/attempts/${attemptId}/feedback`), { timeout: 30_000 });
    await card.getByRole('button', { name: '发送并生成反馈' }).click();
    const submitted = await submitPromise;
    if (![200, 202].includes(submitted.status())) {
      throw new Error(`第 ${index + 1} 次反馈提交失败：${submitted.status()}`);
    }
    turn.submission = await submitted.json();
    await save();
    const deadline = Date.now() + 120_000;
    let chain;
    let updated;
    while (Date.now() < deadline) {
      chain = await readChain();
      updated = chain.attempts.find((item) => item.attempt_id === attemptId);
      if (updated?.status === 'feedback_ready' || updated?.status === 'feedback_failed') break;
      await page.waitForTimeout(1000);
    }
    if (updated?.status !== 'feedback_ready' || !updated.feedback_json
      || updated.answer_text !== source.answer_text) {
      throw new Error(`第 ${index + 1} 次反馈未成功或覆盖了原回答：${updated?.status ?? 'timeout'}`);
    }
    const feedback = JSON.parse(updated.feedback_json);
    if (!Array.isArray(feedback.user_claims) || !Array.isArray(feedback.code_facts)
      || !Array.isArray(feedback.general_principles)
      || !Array.isArray(feedback.inferences) || !Array.isArray(feedback.corrections)) {
      throw new Error('反馈没有区分用户说法、代码事实、一般原理与推断');
    }
    turn.feedback = feedback;
    turn.status = updated.status;
    turn.finished_at = new Date().toISOString();
    await save();
  }
  const final = await readChain();
  if (final.attempts[0].answer_text !== original.attempts[0].answer_text
    || final.attempts[1].answer_text !== original.attempts[1].answer_text
    || final.attempts[1].parent_attempt_id !== firstId
    || final.notes.length !== original.notes.length) {
    throw new Error('反馈后原回答、修正链或笔记发生变化');
  }
  evidence.final_chain = final;
  evidence.status = 'observed_pending_semantic_review';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_FEEDBACK_OBSERVED ${mode} ${taskId}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
