import { test, expect } from "@playwright/test";

// Only inert fixture values may be used in this trace-enabled browser suite.
// Real pairing must disable trace/HAR/logging and must not capture the displayed code.
test("fixture: explicit local pairing, secret-free UI, empty ledger and confirmed logout", async ({
  page,
}) => {
  const code = "1234567890ABCDEF1234",
    proof = "P".repeat(43),
    csrf = "C".repeat(43);
  let approved = false,
    authenticated = false,
    requested = 0;
  const protectedReads: Record<string, string>[] = [];
  let logoutHeaders: Record<string, string> = {};
  await page.route(
    (url) => url.pathname.startsWith("/api/knowledge/v1/"),
    async (route) => {
      const request = route.request(),
        path = new URL(request.url()).pathname;
      const reply = (body: unknown, status = 200) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify(body),
        });
      if (path.endsWith("/auth/local-test/request")) {
        requested++;
        return reply({
          status: "waiting_approval",
          pairing_code: code,
          pairing_proof: proof,
          expires_in_seconds: 120,
          fixture: true,
        });
      }
      if (path.endsWith("/auth/local-test/session")) {
        expect(request.headers()["x-wiki-pairing-proof"]).toBe(proof);
        if (!approved)
          return reply({ status: "waiting_approval", fixture: true });
        authenticated = true;
        return reply({
          status: "authenticated",
          csrf_token: csrf,
          expires_in_seconds: 900,
          fixture: true,
        });
      }
      if (path.endsWith("/auth/local-test/logout")) {
        logoutHeaders = request.headers();
        authenticated = false;
        return reply({ status: "waiting_auth", fixture: true });
      }
      if (!authenticated)
        return reply({ error: { code: "UNAUTHENTICATED" } }, 401);
      protectedReads.push(request.headers());
      return reply({
        data:
          path.endsWith("/intakes") || path.endsWith("/tasks")
            ? { items: [], has_more: false }
            : [],
        task_id: null,
        trace_id: "fixture",
      });
    },
  );
  await page.route(
    (url) => url.pathname === "/health",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          status: "ready",
          service: "synthetic",
          worker_enabled: false,
          channel_worker_enabled: false,
          channel_status: "disabled",
          automatic_commit_enabled: false,
          publication_enabled: false,
          browser_authentication_mode: "local_test",
          learning_viewer_url: null,
        }),
      }),
  );
  await page.goto("#/home");
  await expect(
    page.getByRole("button", { name: "申请本机测试配对码" }),
  ).toBeVisible();
  expect(requested).toBe(0);
  await page.getByRole("button", { name: "申请本机测试配对码" }).dblclick();
  await expect(page.getByLabel("一次性配对码")).toHaveText(code);
  expect(requested).toBe(1);
  await expect(page.locator("body")).not.toContainText(proof);
  await expect(page.locator("body")).not.toContainText(csrf);
  await page.getByRole("button", { name: "检查本机批准" }).click();
  await expect(page.getByText(/尚未收到包内配对工具的批准/)).toBeVisible();
  approved = true;
  await page.getByRole("button", { name: "检查本机批准" }).click();
  await expect(
    page.getByText("已连接本机测试身份", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "从第一份知识开始" }),
  ).toBeVisible();
  expect(protectedReads.length).toBeGreaterThan(0);
  expect(
    protectedReads.every(
      (headers) =>
        headers["x-wiki-pairing-proof"] === proof && !headers.authorization,
    ),
  ).toBe(true);
  expect(
    await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    })),
  ).toEqual({ local: [], session: [] });
  expect(page.url()).not.toContain(code);
  await page.getByRole("link", { name: "收件箱", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "投递材料", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("材料类型")).toHaveCount(0);
  await expect(page.getByLabel("来源正文")).toHaveCount(0);
  await page.getByRole("button", { name: "注销本次测试会话" }).click();
  await expect(page.getByText(/服务已确认注销本次测试会话/)).toBeVisible();
  expect(logoutHeaders["x-wiki-pairing-proof"]).toBe(proof);
  expect(logoutHeaders["x-wiki-csrf"]).toBe(csrf);
  await expect(
    page.getByRole("heading", { name: "从第一份知识开始" }),
  ).toHaveCount(0);
});

test("fixture: default-disabled pairing remains unavailable without a code or authenticated success", async ({
  page,
}) => {
  await page.route(
    (url) => url.pathname.startsWith("/api/knowledge/v1/"),
    (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "UNAUTHENTICATED" } }),
      }),
  );
  await page.route(
    (url) => url.pathname === "/health",
    (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          status: "ready",
          service: "synthetic",
          worker_enabled: false,
          channel_worker_enabled: false,
          channel_status: "disabled",
          automatic_commit_enabled: false,
          publication_enabled: false,
          browser_authentication_mode: "disabled",
          learning_viewer_url: null,
        }),
      }),
  );
  await page.goto("#/home");
  await expect(
    page.getByRole("button", { name: "申请本机测试配对码" }),
  ).toBeDisabled();
  await expect(
    page.getByText("浏览器认证未启用", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("一次性配对码")).toHaveCount(0);
  await expect(
    page.getByText("已连接本机测试身份", { exact: true }),
  ).toHaveCount(0);
});
