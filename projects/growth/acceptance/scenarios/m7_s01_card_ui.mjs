// Exercise installed local card actions from an isolated replay opportunity.
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, opportunityId, proposalId, outputPath, modeFlag, providerName, resumeFlag] = process.argv.slice(2);
if (!executable || !workspace || !opportunityId || !proposalId || !outputPath
  || (modeFlag && modeFlag !== '--live') || (modeFlag === '--live' && !providerName)
  || (resumeFlag && resumeFlag !== '--resume')) {
  throw new Error('用法：node m7_s01_card_ui.mjs <exe> <workspace> <opportunity-id> <proposal-id> <output.json> [--live <provider> [--resume]]');
}
const mode = modeFlag === '--live' ? 'live_observation' : 'replay_not_live';
const resume = resumeFlag === '--resume';
const hash = createHash('sha256');
for await (const chunk of createReadStream(executable)) hash.update(chunk);
const evidence = {
  mode,
  status: 'running',
  executable_sha256: hash.digest('hex'),
  workspace,
  opportunity_id: opportunityId,
  proposal_id: proposalId,
  started_at: new Date().toISOString(),
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
  const readChain = async (taskId) => {
    const response = await page.request.get(`${origin}/api/growth/local/tasks/${taskId}`, { headers });
    if (response.status() !== 200) throw new Error(`卡片链读取失败：${response.status()}`);
    return response.json();
  };
  const stateResponse = await page.request.get(`${origin}/api/growth/local`, { headers });
  if (stateResponse.status() !== 200) throw new Error('无法读取本机机会');
  const initial = await stateResponse.json();
  const opportunity = initial.opportunities.find((item) => item.opportunity_id === opportunityId);
  const proposal = initial.topic_proposals.find((item) => item.proposal_id === proposalId);
  const existingTask = initial.growth_tasks.find((item) => item.opportunity_id === opportunityId);
  if (!opportunity || proposal?.opportunity_id !== opportunityId
    || (resume ? proposal.status !== 'confirmed' || !existingTask
      : proposal.status !== 'pending' || existingTask)) {
    throw new Error('隔离机会、主题或卡片前置不满足');
  }
  const row = page.locator('.growth-page__opportunities li').filter({ hasText: opportunity.title });
  await row.waitFor({ timeout: 30_000 });
  if (resume) {
    evidence.topic_id = proposal.topic_id;
    evidence.task_id = existingTask.task_id;
    await row.getByRole('button', { name: /打开卡片/ }).click();
  } else {
    const proposalRow = row.locator('span').filter({ hasText: proposal.title }).first().locator('..');
    const confirmButton = proposalRow.getByRole('button', { name: '确认主题' });
    if (await confirmButton.count() !== 1) throw new Error('无法唯一定位选定机会的主题建议');
    const confirmPromise = page.waitForResponse((response) =>
      match(response, `/topic-proposals/${proposalId}/decision`), { timeout: 30_000 });
    await confirmButton.click();
    const confirmed = await confirmPromise;
    if (confirmed.status() !== 200) throw new Error(`主题确认失败：${confirmed.status()}`);
    evidence.topic_id = (await confirmed.json()).topic_id;
    if (!evidence.topic_id) throw new Error('确认未返回主题 ID');
    await save();

    const createPromise = page.waitForResponse((response) =>
      match(response, `/opportunities/${opportunityId}/tasks`), { timeout: 30_000 });
    await row.getByRole('button', { name: '创建短卡片' }).click();
    const created = await createPromise;
    if (![200, 201].includes(created.status())) throw new Error(`建卡失败：${created.status()}`);
    evidence.task_id = (await created.json()).task_id;
  }
  if (!evidence.task_id) throw new Error('建卡未返回任务 ID');
  const card = page.locator('[aria-labelledby="growth-card-title"]');
  await card.waitFor({ timeout: 30_000 });
  evidence.question = await card.locator('.growth-page__card-question').innerText();
  if (!/query|supplyAsync|查询/i.test(evidence.question)
    || !/线程|执行器|executor|被谁执行/i.test(evidence.question)) {
    throw new Error('短卡片问题没有覆盖本次并发查询学习目标');
  }
  await save();

  if (providerName) await page.locator('#growth-provider-select').selectOption(providerName);
  const giveFeedback = async (attemptId, answer) => {
    const attemptRow = card.locator('ul > li').filter({ hasText: answer })
      .filter({ has: page.getByRole('button', { name: '预览模型反馈请求' }) });
    const previewPromise = page.waitForResponse((response) =>
      match(response, `/attempts/${attemptId}/feedback-preview`), { timeout: 30_000 });
    await attemptRow.getByRole('button', { name: '预览模型反馈请求' }).click();
    const previewResponse = await previewPromise;
    if (previewResponse.status() !== 200) {
      throw new Error(`反馈预览失败：${previewResponse.status()}`);
    }
    const preview = await previewResponse.json();
    const shown = await card.locator('pre.growth-page__payload').last().innerText();
    if (shown !== preview.payload_text || !shown.includes(answer)) {
      throw new Error('反馈预览与界面展示不一致');
    }
    if (mode === 'live_observation' && /(?:acceptance|fake|replay)/i.test(preview.model_name)) {
      throw new Error('live 观察拒绝固定响应模型');
    }
    await card.getByLabel('我已核对上述内容，同意发送给所选 provider。').check();
    const submitPromise = page.waitForResponse((response) =>
      match(response, `/attempts/${attemptId}/feedback`), { timeout: 30_000 });
    await card.getByRole('button', { name: '发送并生成反馈' }).click();
    const submitted = await submitPromise;
    if (![200, 202].includes(submitted.status())) {
      throw new Error(`反馈提交失败：${submitted.status()}`);
    }
    const deadline = Date.now() + 120_000;
    let updated;
    while (Date.now() < deadline) {
      const chain = await readChain(evidence.task_id);
      updated = chain.attempts.find((item) => item.attempt_id === attemptId);
      if (updated?.status === 'feedback_ready' || updated?.status === 'feedback_failed') break;
      await page.waitForTimeout(1000);
    }
    if (updated?.status !== 'feedback_ready' || updated.answer_text !== answer
      || !updated.feedback_json) {
      throw new Error(`反馈未成功或原回答被覆盖：${updated?.status ?? 'timeout'}`);
    }
    const feedback = JSON.parse(updated.feedback_json);
    if (!Array.isArray(feedback.user_claims) || !Array.isArray(feedback.code_facts)
      || !Array.isArray(feedback.general_principles)
      || !Array.isArray(feedback.inferences) || !Array.isArray(feedback.corrections)) {
      throw new Error('反馈未区分用户说法、代码事实、通用原理与推断');
    }
    return { attempt_id: attemptId, feedback, preview_sha256:
      createHash('sha256').update(preview.payload_text).digest('hex') };
  };

  const firstAnswer = 'CompletableFuture 每调用一次 supplyAsync 就新建一个线程，join 只是把两个结果放到一起，不会等待。';
  await card.getByRole('button', { name: '开始或继续' }).click();
  await card.getByRole('button', { name: '暂停' }).waitFor({ timeout: 30_000 });
  await card.locator('#growth-answer').fill(firstAnswer);
  const firstPromise = page.waitForResponse((response) =>
    match(response, `/tasks/${evidence.task_id}/answers`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存回答' }).click();
  const first = await firstPromise;
  if (![200, 201].includes(first.status())) throw new Error(`首答保存失败：${first.status()}`);
  evidence.first_attempt_id = (await first.json()).attempt_id;
  await card.locator('ul li').filter({ hasText: firstAnswer }).first().waitFor({ timeout: 30_000 });
  await save();
  evidence.first_feedback = await giveFeedback(evidence.first_attempt_id, firstAnswer);
  await save();

  const hintPromise = page.waitForResponse((response) =>
    match(response, `/tasks/${evidence.task_id}/hints`), { timeout: 30_000 });
  await card.getByRole('button', { name: '查看下一条提示' }).click();
  const hinted = await hintPromise;
  if (![200, 201].includes(hinted.status())) throw new Error(`提示失败：${hinted.status()}`);
  evidence.hint = await hinted.json();
  if (evidence.hint.level !== 1) throw new Error('第一条提示级别不为 1');
  await card.locator('ol li').filter({ hasText: evidence.hint.hint_text })
    .waitFor({ timeout: 30_000 });
  await save();

  const correction = '两个查询任务交给传入的 executor，线程是不是新建由执行器决定。thenCombine 组合结果，最后 join 会等待组合结果，调用 plan 的线程仍可能被阻塞。';
  await card.locator('#growth-answer').fill(correction);
  await card.locator('#growth-parent-answer').selectOption(evidence.first_attempt_id);
  const correctionPromise = page.waitForResponse((response) =>
    match(response, `/tasks/${evidence.task_id}/answers`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存回答' }).click();
  const corrected = await correctionPromise;
  if (![200, 201].includes(corrected.status())) {
    throw new Error(`修正回答失败：${corrected.status()}`);
  }
  evidence.correction_attempt_id = (await corrected.json()).attempt_id;
  await card.locator('ul li').filter({ hasText: correction }).first().waitFor({ timeout: 30_000 });
  await save();
  evidence.correction_feedback = await giveFeedback(evidence.correction_attempt_id, correction);
  await save();

  const progress = async (name, expected, next) => {
    const responsePromise = page.waitForResponse((response) =>
      match(response, `/tasks/${evidence.task_id}/progress`), { timeout: 30_000 });
    await card.getByRole('button', { name }).click();
    const response = await responsePromise;
    if (response.status() !== 200 || (await response.json()).status !== expected) {
      throw new Error(`卡片进度 ${expected} 保存失败`);
    }
    if (next) await card.getByRole('button', { name: next }).waitFor({ timeout: 30_000 });
  };
  await progress('暂停', 'paused', '开始或继续');
  await progress('开始或继续', 'in_progress', '完成卡片');
  await progress('完成卡片', 'completed', null);
  evidence.progress = 'completed';
  const note = '我以前把 CompletableFuture 当成“每个任务自动新建线程”。这次代码把两个查询交给同一个 executor，实际线程由执行器安排。thenCombine 组合结果，join 会等待，所以这段代码并不意味着调用方从头到尾都不阻塞。以后排查并发问题，我要先找执行器在哪里配置。';
  if (await card.locator('#growth-note').inputValue() !== '') {
    throw new Error('S01-08 笔记编辑器打开时正文必须为空');
  }
  await card.locator('#growth-note').fill(note);
  const notePromise = page.waitForResponse((response) =>
    match(response, `/tasks/${evidence.task_id}/notes`), { timeout: 30_000 });
  await card.getByRole('button', { name: '保存笔记' }).click();
  const saved = await notePromise;
  if (![200, 201].includes(saved.status())) throw new Error(`笔记保存失败：${saved.status()}`);
  evidence.note_id = (await saved.json()).note_id;
  await card.locator('ul li').filter({ hasText: note }).first().waitFor({ timeout: 30_000 });
  await page.reload();
  await page.locator('.growth-page__opportunities li')
    .filter({ hasText: opportunity.title }).getByRole('button', { name: /打开卡片/ })
    .click();
  await card.waitFor({ timeout: 30_000 });
  const chainResponse = await page.request.get(`${origin}/api/growth/local/tasks/${evidence.task_id}`,
    { headers });
  if (chainResponse.status() !== 200) throw new Error('重启页面后卡片链不可读取');
  const chain = await chainResponse.json();
  if (chain.task.progress !== 'completed' || chain.attempts.length !== 2
    || chain.attempts[0].answer_text !== firstAnswer
    || chain.attempts[0].status !== 'feedback_ready'
    || chain.attempts[1].answer_text !== correction
    || chain.attempts[1].status !== 'feedback_ready'
    || chain.attempts[1].parent_attempt_id !== evidence.first_attempt_id
    || chain.attempts[1].hint_level !== 1
    || chain.notes.length !== 1 || chain.notes[0].content_text !== note) {
    throw new Error('页面重载后进度、修正父链、提示级别或笔记不符');
  }
  evidence.chain = chain;
  evidence.status = 'observed';
  evidence.finished_at = new Date().toISOString();
  await save();
  process.stdout.write(`S01_CARD_OBSERVED ${mode} ${evidence.task_id}\n`);
} catch (error) {
  evidence.status = 'failed';
  evidence.error = error instanceof Error ? error.message : String(error);
  evidence.finished_at = new Date().toISOString();
  await save();
  throw error;
} finally {
  if (electron) await electron.close();
}
