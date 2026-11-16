import { test, expect } from "@playwright/test";
import { intake, openOwner, reject, reply, setupV1 } from "./v1-fixtures";

// Intercepted synthetic API only. Never record real owner material in this suite.
const original = "  合成首收正文 😀\n第二行\n  ";
const payload = {
  text: original,
  requested_action: "preview",
  data_classification: "personal",
};
const received = {
  ...intake,
  state: "received",
  preview_revision: 0,
  preview: undefined,
};

test("fixture first intake: text is exact, preview-only, and uncertain retry uses the same key", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write, index) => {
      expect(write.path).toBe("/intakes");
      expect(write.body).toEqual(payload);
      return index === 1
        ? route.abort("connectionfailed")
        : reply(route, received, 202);
    },
  });
  await openOwner(page, f, "#/inbox");
  await page.getByRole("button", { name: "投递材料", exact: true }).click();
  await page.getByLabel("材料类型").selectOption("text");
  await page.getByLabel("来源正文").fill(original);
  expect(f.writes).toHaveLength(0);
  await page.getByRole("button", { name: "提交临时预览" }).click();
  await expect(
    page.getByRole("button", { name: "重试同一请求" }),
  ).toBeVisible();
  await expect(page.getByLabel("材料类型")).toBeDisabled();
  await expect(page.getByLabel("来源正文")).toBeDisabled();
  expect(f.writes).toHaveLength(1);
  await page.getByRole("button", { name: "重试同一请求" }).click();
  await expect(page).toHaveURL(/#\/intake\/int_synthetic$/);
  expect(f.writes).toHaveLength(2);
  expect(f.writes[1]).toEqual(f.writes[0]);
  await expect(page.getByLabel("来源正文")).toHaveCount(0);
  expect(
    await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    })),
  ).toEqual({ local: [], session: [] });
  expect(page.url()).not.toContain(encodeURIComponent(original));
});

test("fixture first intake: switching mode or cancelling clears text, while URL intake remains available", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/intakes");
      expect(write.body).toEqual({
        url: "https://example.com/synthetic",
        requested_action: "preview",
        data_classification: "personal",
      });
      return reply(route, received, 202);
    },
  });
  await openOwner(page, f, "#/inbox");
  await page.getByRole("button", { name: "投递材料", exact: true }).click();
  await page.getByLabel("材料类型").selectOption("text");
  await page.getByLabel("来源正文").fill(original);
  await page.getByLabel("材料类型").selectOption("url");
  await expect(page.getByLabel("来源链接")).toHaveValue("");
  await page.getByLabel("材料类型").selectOption("text");
  await expect(page.getByLabel("来源正文")).toHaveValue("");
  await page.getByLabel("来源正文").fill(original);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(f.writes).toHaveLength(0);
  await page.getByRole("button", { name: "投递材料", exact: true }).click();
  await expect(page.getByLabel("来源链接")).toHaveValue("");
  await page.getByLabel("来源链接").fill("https://example.com/synthetic");
  await page.getByRole("button", { name: "提交临时预览" }).click();
  await expect(page).toHaveURL(/#\/intake\/int_synthetic$/);
  expect(f.writes).toHaveLength(1);
});

test("fixture first intake: rejected authentication clears text without retry or success", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/intakes");
      expect(write.body).toEqual(payload);
      return reject(route, "UNAUTHENTICATED", 401);
    },
  });
  await openOwner(page, f, "#/inbox");
  await page.getByRole("button", { name: "投递材料", exact: true }).click();
  await page.getByLabel("材料类型").selectOption("text");
  await page.getByLabel("来源正文").fill(original);
  await page.getByRole("button", { name: "提交临时预览" }).click();
  await expect(
    page.getByRole("heading", { name: "连接私有工作区" }),
  ).toBeVisible();
  await expect(page.getByLabel("来源正文")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(original);
  await expect(
    page.getByText("已连接 Owner 私有会话", { exact: true }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(/#\/inbox$/);
  expect(f.writes).toHaveLength(1);
});

test("fixture first intake: late receipt cannot replace a cancelled form or newer draft", async ({
  page,
}) => {
  let release!: () => Promise<void>;
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/intakes");
      expect(write.body).toEqual(payload);
      await new Promise<void>((resolve) => {
        release = async () => {
          await reply(route, received, 202);
          resolve();
        };
      });
    },
  });
  await openOwner(page, f, "#/inbox");
  await page.getByRole("button", { name: "投递材料", exact: true }).click();
  await page.getByLabel("材料类型").selectOption("text");
  await page.getByLabel("来源正文").fill(original);
  await page.getByRole("button", { name: "提交临时预览" }).click();
  await expect.poll(() => f.writes.length).toBe(1);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "投递材料", exact: true }).click();
  await page.getByLabel("材料类型").selectOption("text");
  await page.getByLabel("来源正文").fill("New synthetic draft");
  const response = page.waitForResponse(
    (value) =>
      new URL(value.url()).pathname === "/api/knowledge/v1/intakes" &&
      value.request().method() === "POST",
  );
  await release();
  await (await response).finished();
  // Allow the settled fetch and React commit to run before the negative checks.
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(page.getByLabel("来源正文")).toHaveValue("New synthetic draft");
  await expect(page).toHaveURL(/#\/inbox$/);
  expect(f.writes).toHaveLength(1);
});
