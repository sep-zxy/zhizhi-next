import { test, expect } from "@playwright/test";
import { CODE, CSRF, PROOF, openOwner, setupV1 } from "./v1-fixtures";

test("fixture owner: read-only scopes expose real reading but disable material writes", async ({
  page,
}) => {
  const f = await setupV1(page, { scopes: ["knowledge:read"] });
  await openOwner(page, f, "#/intake/int_synthetic");
  await expect(
    page.getByRole("button", { name: "粘贴补充文本" }),
  ).toBeDisabled();
  await expect(page.getByLabel("选择文本文件")).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "私有保留并整理" }),
  ).toBeDisabled();
  expect(f.writes).toHaveLength(0);
  await expect(page.locator("body")).toContainText("Original synthetic source");
  await expect(page.locator("body")).not.toContainText("本机隔离测试");
  expect(
    await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    })),
  ).toEqual({ local: [], session: [] });
  expect(page.url()).not.toContain(PROOF);
  expect(page.url()).not.toContain(CSRF);
  expect(page.url()).not.toContain(CODE);
  await page.getByRole("link", { name: "收件箱", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "投递材料", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("来源正文")).toHaveCount(0);
  expect(f.writes).toHaveLength(0);
});
test("fixture owner: cancelling pending pairing clears the code without claiming server revocation", async ({
  page,
}) => {
  const f = await setupV1(page);
  await page.goto("#/home");
  await page.getByRole("button", { name: "申请 Owner 配对码" }).click();
  await expect(page.getByLabel("一次性配对码")).toHaveText(CODE);
  await page.getByRole("button", { name: "取消本页配对" }).click();
  await expect(page.getByLabel("一次性配对码")).toHaveCount(0);
  await expect(page.getByText(/已清除此页配对信息/)).toBeVisible();
  expect(f.authPaths.some((path) => path.endsWith("/logout"))).toBe(false);
  await expect(
    page.getByText("已连接 Owner 私有会话", { exact: true }),
  ).toHaveCount(0);
});
test("fixture owner: pending pairing expires without an implicit new request or authentication", async ({
  page,
}) => {
  await page.clock.install();
  const f = await setupV1(page);
  await page.goto("#/home");
  await page.getByRole("button", { name: "申请 Owner 配对码" }).click();
  await expect(page.getByLabel("一次性配对码")).toHaveText(CODE);
  await page.clock.fastForward(121000);
  await expect(page.getByLabel("一次性配对码")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "申请 Owner 配对码" }),
  ).toBeEnabled();
  expect(f.authPaths.filter((path) => path.endsWith("/request"))).toHaveLength(
    1,
  );
  expect(f.authPaths.some((path) => path.endsWith("/session"))).toBe(false);
});
test("fixture owner: authenticated expiry clears entry and search before any new read", async ({
  page,
}) => {
  await page.clock.install();
  const f = await setupV1(page, { sessionTtl: 60 });
  await openOwner(page, f, "#/entry/entry_synthetic.1");
  await expect(
    page.getByText("Synthetic formal source body", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("搜索私有知识").fill("Synthetic private search draft");
  const reads = f.reads.length;
  await page.clock.fastForward(61000);
  await expect(
    page.getByText("Synthetic formal source body", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("搜索私有知识")).toHaveCount(0);
  await expect(
    page.getByText("已连接 Owner 私有会话", { exact: true }),
  ).toHaveCount(0);
  expect(f.reads).toHaveLength(reads);
});
test("fixture owner: local-test-shaped response cannot authenticate the owner route", async ({
  page,
}) => {
  const f = await setupV1(page, { mismatch: true });
  await page.goto("#/home");
  await page.getByRole("button", { name: "申请 Owner 配对码" }).click();
  await expect(
    page
      .getByRole("region", { name: "Owner 私有会话", exact: true })
      .getByRole("alert"),
  ).toContainText("配对结果无法验证");
  await expect(page.getByLabel("一次性配对码")).toHaveCount(0);
  await expect(
    page.getByText("已连接 Owner 私有会话", { exact: true }),
  ).toHaveCount(0);
  expect(f.authPaths).toEqual(["/api/knowledge/v1/auth/owner/request"]);
  expect(f.writes).toHaveLength(0);
});
test("fixture owner: uncertain logout removes private content but never claims server revocation", async ({
  page,
}) => {
  const f = await setupV1(page, { logoutFailure: true });
  await openOwner(page, f, "#/entry/entry_synthetic.1");
  await expect(
    page.getByText("Synthetic formal source body", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "注销本次 Owner 会话" }).click();
  await expect(
    page
      .getByRole("region", { name: "Owner 私有会话", exact: true })
      .getByRole("alert"),
  ).toContainText("服务端注销未得到确认");
  await expect(
    page.getByText("Synthetic formal source body", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(/服务已确认注销本次 Owner 会话/)).toHaveCount(0);
  expect(f.authPaths.filter((path) => path.endsWith("/logout"))).toHaveLength(
    1,
  );
});
