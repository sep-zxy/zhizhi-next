import { test, expect, type Page } from "@playwright/test";
const now = "2026-10-20T12:00:00Z";
const entry = (id: string, title: string, category: string) => ({
  entry_id: id,
  revision: 2,
  title,
  body:
    "# " +
    title +
    "\n\n这是隔离浏览器测试数据。\n\n## 来源边界\n正式条目与原始材料分开。",
  entry_type: "concept",
  visibility: "private",
  publication_intent: "public",
  publication_eligible: false,
  evidence_refs: [{ source_id: "src_fixture", content_hash: "abc" }],
  metadata: { categories: [category], tags: ["fixture"] },
  deleted: false,
  created_at: now,
  updated_at: now,
});
const entries = [
  entry("ent_one", "隔离测试条目", "工程与技术"),
  entry("ent_two", "阅读记录", "信息与学习"),
  entry("ent_three", "界面笔记", "产品与设计"),
  entry("ent_four", "城市路线", "生活与兴趣"),
];
const task = {
  task_id: "tsk_fixture",
  kind: "knowledge.generate",
  state: "waiting_auth",
  revision: 3,
  attempt: 1,
  max_attempts: 5,
  created_at: now,
  updated_at: now,
  error: { code: "AUTH_REQUIRED" },
};
const change = {
  change_id: "chg_fixture",
  entry_id: "ent_one",
  title: "测试修订提案",
  body: "# 完整候选正文\n\n隔离测试中的完整新版。",
  state: "needs_user",
  entry_type: "concept",
  operation: "update",
  base_revision: 2,
  patch_hash: "hash_fixture",
  risk_flags: ["core_conclusion"],
  visibility: "private",
  metadata: { categories: ["工程与技术"] },
  evidence_refs: [{ source_id: "src_fixture" }],
  updated_at: now,
};
const intake = {
  intake_id: "int_fixture",
  state: "preview_ready",
  channel: "http",
  url: "https://example.com/fixture",
  title: "测试收件",
  preview_revision: 2,
  parse_status: "partial",
  task_id: "tsk_fixture",
  expires_at: "2099-01-01T00:00:00Z",
  created_at: now,
  updated_at: now,
  preview: {
    summary: null,
    summary_state: "not_requested",
    document: {
      title: "测试收件",
      platform: "web",
      body_text: "测试原件，不包含真实用户资料。",
      body_complete: false,
      warnings: ["缺少附件"],
      metadata: { text_basis: "body" },
    },
  },
};
async function fixtures(
  page: Page,
  options: { auth?: boolean; slowId?: string } = {},
) {
  const writes: {
    path: string;
    body: Record<string, unknown>;
    key: string | undefined;
  }[] = [];
  await page.route("**/api/knowledge/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/api/knowledge/v1", "");
    const respond = (data: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify({
          data,
          task_id: null,
          trace_id: "trace_fixture",
        }),
      });
    if (options.auth === false)
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          error: {
            code: "UNAUTHENTICATED",
            detail: "sensitive raw diagnostic",
          },
          trace_id: "trace_fixture",
        }),
      });
    if (request.method() === "POST") {
      writes.push({
        path,
        body: request.postDataJSON(),
        key: request.headers()["idempotency-key"],
      });
      if (path === "/intakes") return respond(intake, 202);
      if (path.endsWith("/decisions"))
        return respond({
          decision: request.postDataJSON().decision,
          change_id: change.change_id,
          task_id: "tsk_fixture",
        });
      if (path.endsWith("/decision"))
        return respond({
          state:
            request.postDataJSON().decision === "keep"
              ? "retained"
              : "discarded",
          intake_id: "int_fixture",
          source_id: "src_fixture",
        });
    }
    if (path === "/entries") return respond(entries);
    if (path.startsWith("/entries/")) {
      if (options.slowId && path.endsWith(options.slowId))
        await new Promise((r) => setTimeout(r, 600));
      const item = entries.find((e) => path.endsWith(e.entry_id));
      return item
        ? respond({
            ...item,
            revision: Number(url.searchParams.get("revision")) || item.revision,
          })
        : respond(null, 404);
    }
    if (path === "/search")
      return respond({
        results:
          url.searchParams.get("q") === "无匹配"
            ? []
            : [
                {
                  entry_id: "ent_one",
                  revision: 2,
                  title: "隔离测试条目",
                  snippet: "真实接口形状的检索片段",
                  stale_index: true,
                  scope: "wiki",
                  visibility: "private",
                  evidence_refs: [],
                },
              ],
      });
    if (path === "/reviews") return respond([change]);
    if (path.endsWith("/decision-challenge"))
      return respond({
        change_id: change.change_id,
        patch_hash: change.patch_hash,
        base_revision: 2,
        policy_version: "1",
        expires_at: "2099-01-01T00:00:00Z",
        decision_nonce: "fixture-only-nonce",
      });
    if (path === "/changes/chg_fixture") return respond(change);
    if (path === "/tasks") return respond({ items: [task], has_more: false });
    if (path === "/tasks/tsk_fixture") return respond(task);
    if (path === "/tasks/tsk_fixture/events")
      return respond([
        {
          event_id: "evt_fixture",
          type: "task.updated.v1",
          sequence: 1,
          occurred_at: now,
          aggregate_revision: 3,
          payload: { state: "waiting_auth" },
        },
      ]);
    if (path === "/intakes")
      return respond({ items: [intake], has_more: false });
    if (path === "/intakes/int_fixture") return respond(intake);
    if (path === "/intakes/capabilities")
      return respond({
        capabilities: [
          {
            adapter: "web",
            platform: "web",
            body_text: "partial",
            captions: "unsupported",
            video: "unsupported",
            verification: "not_live_verified",
          },
        ],
      });
    return respond(null, 404);
  });
  await page.route("**/health", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        service: "zhizhi-knowledge",
        worker_enabled: false,
        channel_worker_enabled: false,
        channel_status: "disabled",
        automatic_commit_enabled: false,
        publication_enabled: false,
      }),
    }),
  );
  return writes;
}
test("fixture: private home, catalog, article, history and task navigation", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("#/home");
  await expect(
    page.getByRole("heading", { name: "我的知识入口" }),
  ).toBeVisible();
  await expect(page.getByText("工程与技术", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "进入完整目录" }).click();
  await expect(
    page.getByRole("heading", { name: "知识目录与全库检索" }),
  ).toBeVisible();
  await page.locator(".entry-row-main").first().click();
  await expect(
    page.getByRole("heading", { name: "隔离测试条目", exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("发布状态未提供")).toBeVisible();
  await page.getByLabel("读取指定修订").fill("1");
  await page.getByRole("button", { name: "读取", exact: true }).click();
  await expect(page).toHaveURL(/revision=1/);
  await expect(page.getByText("v1 · 指定历史版本")).toBeVisible();
  await page.goBack();
  await expect(page).not.toHaveURL(/revision=1/);
  await page.goto("#/tasks/tsk_fixture");
  await expect(
    page.getByRole("heading", { name: "knowledge.generate" }),
  ).toBeVisible();
  await expect(page.getByText("AUTH_REQUIRED", { exact: true })).toBeVisible();
  await expect(page.getByText("尚未确认正式知识提交。")).toBeVisible();
  await page.screenshot({
    path: "test-results/wiki-task-desktop.png",
    fullPage: true,
  });
});
test("fixture: search, stale index and empty result are explicit", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("#/catalog");
  await page.getByLabel("检索词").fill("知识");
  await page
    .locator(".catalog-search")
    .getByRole("button", { name: "搜索" })
    .click();
  await expect(page.getByText("索引已落后", { exact: true })).toBeVisible();
  await page.getByLabel("检索词").fill("无匹配");
  await page
    .locator(".catalog-search")
    .getByRole("button", { name: "搜索" })
    .click();
  await expect(
    page.getByRole("heading", { name: "库内没有找到匹配结果" }),
  ).toBeVisible();
});
test("fixture: no private data or fake success before authentication", async ({
  page,
}) => {
  await fixtures(page, { auth: false });
  await page.goto("#/home");
  await expect(
    page.getByRole("heading", { name: "连接私有工作区" }).first(),
  ).toBeVisible();
  await expect(page.getByText("隔离测试条目", { exact: true })).toHaveCount(0);
  await expect(page.getByText("sensitive raw diagnostic")).toHaveCount(0);
  await expect(page.getByText("暂无需要本人确认的提案")).toHaveCount(0);
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
});
test("fixture: outdated route response never replaces newer navigation", async ({
  page,
}) => {
  await fixtures(page, { slowId: "ent_one" });
  await page.goto("#/entry/ent_one");
  await page.goto("#/entry/ent_two");
  await expect(
    page.getByRole("heading", { name: "阅读记录", exact: true }).first(),
  ).toBeVisible();
  await page.waitForTimeout(750);
  await expect(
    page.getByRole("heading", { name: "隔离测试条目", exact: true }),
  ).toHaveCount(0);
});
test("fixture: cancelled approval has no decision POST; exact nonce approval submits once", async ({
  page,
}) => {
  const writes = await fixtures(page);
  await page.goto("#/review/chg_fixture");
  await expect(page.getByText("隔离测试中的完整新版。")).toBeVisible();
  await page.getByRole("button", { name: "准备批准本次差异" }).click();
  await page.getByRole("button", { name: "取消，不作决定" }).click();
  expect(writes).toHaveLength(0);
  await page.getByRole("button", { name: "准备批准本次差异" }).click();
  await page.getByLabel("决定说明").fill("已核对隔离测试差异");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "提交这次决定" }).dblclick();
  await expect(page.getByText("服务已记录决定", { exact: true })).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toMatchObject({
    decision: "approve",
    expected_patch_hash: "hash_fixture",
    base_revision: 2,
    decision_nonce: "fixture-only-nonce",
  });
  expect(writes[0].key).toBeTruthy();
});
test("fixture: intake cancellation is inert; keep binds preview revision", async ({
  page,
}) => {
  const writes = await fixtures(page);
  await page.goto("#/intake/int_fixture");
  await expect(page.getByText("缺少附件", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "私有保留并整理", exact: true })
    .click();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(writes).toHaveLength(0);
  await page
    .getByRole("button", { name: "私有保留并整理", exact: true })
    .click();
  await page.getByRole("button", { name: "确认本次决定" }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toEqual({ decision: "keep", preview_revision: 2 });
  expect(writes[0].body).not.toHaveProperty("owner_id");
});
test("fixture: responsive shell has no horizontal overflow and no auto-loaded third-party media", async ({
  page,
}) => {
  await fixtures(page);
  await page.goto("#/home");
  await page.screenshot({
    path: "test-results/wiki-home-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "菜单", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "菜单", exact: true }).click();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "知识目录", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "知识目录与全库检索" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/wiki-catalog-mobile.png",
    fullPage: true,
  });
  expect(await page.locator("img,iframe,video").count()).toBe(0);
});
