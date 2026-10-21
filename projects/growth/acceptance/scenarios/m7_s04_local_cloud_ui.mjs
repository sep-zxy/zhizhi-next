// Exercise S04's existing topic and note through the installed Windows app.
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const requireViewer = createRequire(path.join(root, 'viewer', 'package.json'));
const { _electron } = requireViewer('@playwright/test');
const [executable, workspace, baseSha, credentialsPath, outputPath] = process.argv.slice(2);
if (!executable || !workspace || !baseSha || !credentialsPath || !outputPath) {
  throw new Error('用法：node m7_s04_local_cloud_ui.mjs <exe> <workspace> <base_sha> <credentials.json> <output.json>');
}
const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const cloudUrl = `http://127.0.0.1:${credentials.port}`;
const oldNote = '登录成功说明应用识别了用户身份。';
const startedAt = new Date().toISOString();
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
  await page.locator('#growth-project-name').waitFor({ timeout: 30_000 });
  await page.locator('#growth-project-name').fill('document-demo');
  const createdProjectPromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/growth/local/projects'
      && response.request().method() === 'POST',
    { timeout: 30_000 },
  );
  await page.locator('form:has(#growth-project-name) button').click();
  const createdProjectResponse = await createdProjectPromise;
  const createdProject = await createdProjectResponse.json();
  if (createdProjectResponse.status() !== 201 || !createdProject.project_id) {
    throw new Error(`本机项目创建失败：${JSON.stringify(createdProject)}`);
  }
  const projectAvailable = async () => page.waitForFunction((id) => {
    const selected = document.querySelector('#growth-project-select');
    return selected instanceof HTMLSelectElement
      && Array.from(selected.options).some((option) => option.value === id);
  }, createdProject.project_id, { timeout: 15_000 });
  try {
    await projectAvailable();
  } catch {
    // A slower first state load can overtake the create response. Reload from
    // the durable SQLite record without submitting the form a second time.
    await page.reload();
    await projectAvailable();
  }
  await page.locator('#growth-project-select').selectOption(createdProject.project_id);
  const projectId = createdProject.project_id;
  await page.locator('#growth-repository-path').fill(workspace);
  await page.locator('form:has(#growth-repository-path) button').click();
  await page.waitForFunction(() => {
    const selected = document.querySelector('#growth-binding-select');
    return selected instanceof HTMLSelectElement && !!selected.value;
  });
  const bindingId = await page.locator('#growth-binding-select').inputValue();
  await page.locator('#growth-feature-label').fill('文档所有者授权');
  await page.locator('#growth-base-ref').fill(baseSha);
  await page.locator('form:has(#growth-feature-label) button').click();
  await page.waitForFunction(() => {
    const selected = document.querySelector('#growth-feature-select');
    return selected instanceof HTMLSelectElement && !!selected.value;
  });
  const featureId = await page.locator('#growth-feature-select').inputValue();

  const sync = page.locator('section[aria-labelledby="growth-sync-title"]');
  const syncDetails = sync.locator('details').first();
  if (!await syncDetails.evaluate((element) => element.open)) {
    await syncDetails.locator('summary').first().click();
  }
  const previewPromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/growth/local/sync/preview',
    { timeout: 30_000 },
  );
  await sync.locator('details > button').first().click();
  const preview = await (await previewPromise).json();
  if (preview.project_id !== projectId
      || preview.operations.map((operation) => operation.kind).join(',') !== 'project,feature') {
    throw new Error(`项目发布预览不匹配：${JSON.stringify(preview.operations.map((item) => item.kind))}`);
  }
  await sync.locator('input[type="checkbox"]').first().check();
  await page.locator('#growth-cloud-url').fill(cloudUrl);
  await page.locator('#growth-cloud-token').fill(credentials.access_token);
  await sync.locator('form:has(#growth-cloud-url) button').click();
  await sync.locator('details > p[role="status"]').waitFor({ timeout: 120_000 });
  const syncText = await sync.locator('details > p[role="status"]').innerText();
  await page.locator('#growth-topic-create').waitFor({ timeout: 30_000 });
  const topicCountBefore = await page.locator('#growth-topic-select option').count();
  if (topicCountBefore !== 0) throw new Error('隔离账号中预先存在正式主题');

  await page.locator('#growth-topic-create').fill('登录机制');
  const createTopicPromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/growth/local/cloud/topics/create',
    { timeout: 30_000 },
  );
  await page.locator('#growth-topic-create + button').click();
  const createTopicResponse = await createTopicPromise;
  const createdTopic = await createTopicResponse.json();
  if (createTopicResponse.status() !== 201 || !createdTopic.topic_id) {
    throw new Error(`旧主题创建失败：${JSON.stringify(createdTopic)}`);
  }
  await page.waitForFunction((id) => {
    const selected = document.querySelector('#growth-topic-select');
    return selected instanceof HTMLSelectElement && selected.value === id;
  }, createdTopic.topic_id);
  const topicId = await page.locator('#growth-topic-select').inputValue();
  await page.locator('#growth-topic-note').fill(oldNote);
  const createNotePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname
      === `/api/growth/local/cloud/topics/${topicId}/notes`,
    { timeout: 30_000 },
  );
  await page.locator('#growth-topic-note + button').click();
  const createNoteResponse = await createNotePromise;
  const createdNote = await createNoteResponse.json();
  if (createNoteResponse.status() !== 201 || !createdNote.note_id) {
    throw new Error(`旧笔记创建失败：${JSON.stringify(createdNote)}`);
  }
  await page.locator('.growth-page__originals pre').filter({ hasText: oldNote })
    .waitFor({ timeout: 30_000 });

  const evidence = {
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    platform: 'win32-x64',
    cloud_kind: 'isolated_local_postgresql',
    cloud_url: cloudUrl,
    account_id: credentials.account_id,
    workspace,
    base_sha: baseSha,
    project_id: projectId,
    binding_id: bindingId,
    feature_id: featureId,
    topic_count_before: topicCountBefore,
    topic_id: topicId,
    topic_count_after: await page.locator('#growth-topic-select option').count(),
    note_id: createdNote.note_id,
    note_text: oldNote,
    sync_text: syncText,
    responses,
  };
  await writeFile(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, 'utf8');
  process.stdout.write(`S04_LOCAL_UI_PASS ${topicId} ${createdNote.note_id}\n`);
} finally {
  if (electron) await electron.close();
}
