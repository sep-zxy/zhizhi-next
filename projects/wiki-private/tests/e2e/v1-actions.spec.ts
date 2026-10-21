import { test, expect } from "@playwright/test";
import {
  entry,
  intake,
  task,
  openOwner,
  reply,
  reject,
  setupV1,
} from "./v1-fixtures";

test("fixture P03: source/note separation, exact retry and no automatic keep", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write, index) => {
      expect(write.path).toBe("/intakes/int_synthetic/supplement");
      expect(write.body).toEqual({
        expected_preview_revision: 2,
        text: "Synthetic supplemented source",
        private_note: "Synthetic private annotation",
      });
      if (index === 1) return route.abort("connectionfailed");
      return reply(route, { ...intake, preview_revision: 3 });
    },
  });
  await openOwner(page, f, "#/intake/int_synthetic");
  await page.getByRole("button", { name: "粘贴补充文本" }).click();
  await page.getByLabel("补充来源正文").fill("Synthetic supplemented source");
  await page
    .getByLabel("私人附言（不作为来源正文）")
    .fill("Synthetic private annotation");
  await expect(
    page.getByRole("button", { name: "提交本次补料" }),
  ).toBeDisabled();
  expect(f.writes).toHaveLength(0);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "提交本次补料" }).click();
  await expect(page.getByLabel("补充来源正文")).toBeDisabled();
  await page.getByRole("button", { name: "重试同一请求" }).click();
  await expect(
    page.getByText("补料已写入临时预览，尚未保留", { exact: true }),
  ).toBeVisible();
  expect(f.writes).toHaveLength(2);
  expect(f.writes[0].body).toEqual(f.writes[1].body);
  expect(f.writes[0].key).toBe(f.writes[1].key);
});
test("fixture P03: cancel clears actual selected bytes, replacement upload preserves BOM/CRLF", async ({
  page,
}) => {
  const bytes = Buffer.from(
    "\uFEFF# Synthetic attachment\r\n<svg onload=bad>text</svg>\r\n",
  );
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/intakes/int_synthetic/text-attachment");
      expect(write.body).toEqual({
        expected_preview_revision: 2,
        filename: "actual.md",
        content_type: "text/markdown",
        content_base64: bytes.toString("base64"),
        private_note: "Separate note",
      });
      return reply(route, { ...intake, preview_revision: 3 });
    },
  });
  await openOwner(page, f, "#/intake/int_synthetic");
  await page.getByLabel("选择文本文件").setInputFiles({
    name: "cancel.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Cancelled synthetic bytes"),
  });
  await page
    .getByLabel("文件私人附言（不属于来源正文）")
    .fill("Cancelled note");
  await page.getByRole("button", { name: "取消并清除所选文件" }).click();
  await expect(page.locator("body")).not.toContainText(
    "Cancelled synthetic bytes",
  );
  expect(f.writes).toHaveLength(0);
  await page.getByLabel("选择文本文件").setInputFiles({
    name: "actual.md",
    mimeType: "text/markdown",
    buffer: bytes,
  });
  await page.getByLabel("文件私人附言（不属于来源正文）").fill("Separate note");
  await expect(page.locator("svg[onload]")).toHaveCount(0);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "上传核对后的文本文件" }).click();
  await expect(
    page.getByText("文本文件已进入临时预览，尚未保留", { exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("文件私人附言（不属于来源正文）")).toHaveCount(
    0,
  );
  await expect(page.locator("body")).not.toContainText("Synthetic attachment");
  expect(f.writes).toHaveLength(1);
});
test("fixture P03: expiry at click clears selected bytes without uploading", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-10-21T10:00:00Z") });
  const f = await setupV1(page, { expiresAt: "2026-10-21T10:01:00Z" });
  await openOwner(page, f, "#/intake/int_synthetic");
  await page.getByLabel("选择文本文件").setInputFiles({
    name: "expired.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Expired selected bytes"),
  });
  await page.getByRole("checkbox").check();
  // Hold the click-time clock change and event in one browser turn so the
  // background interval cannot preempt the handler's own expiry assertion.
  await page
    .getByRole("button", { name: "上传核对后的文本文件" })
    .evaluate((button) => {
      const previous = Date.now;
      Date.now = () => Date.parse("2026-10-21T10:01:01Z");
      try {
        (button as HTMLButtonElement).click();
      } finally {
        Date.now = previous;
      }
    });
  await expect(page.getByRole("alert")).toContainText("临时预览已到期");
  await expect(page.locator("body")).not.toContainText(
    "Expired selected bytes",
  );
  expect(f.writes).toHaveLength(0);
});
test("fixture P03: stale attachment revision is a visible refusal, never a retained receipt", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/intakes/int_synthetic/text-attachment");
      expect(write.body.expected_preview_revision).toBe(2);
      return reject(route, "PREVIEW_STALE");
    },
  });
  await openOwner(page, f, "#/intake/int_synthetic");
  await page.getByLabel("选择文本文件").setInputFiles({
    name: "stale.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Synthetic stale source"),
  });
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "上传核对后的文本文件" }).click();
  await expect(page.getByRole("alert")).toContainText("预览已变化");
  await expect(
    page.getByText("文本文件已进入临时预览，尚未保留", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重试同一请求" })).toHaveCount(
    0,
  );
  expect(f.writes).toHaveLength(1);
});
test("fixture P05: bound evidence is read on demand, closed safely, and exact revision handoff carries no content", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/evidence/read");
      expect(write.body).toEqual(entry.evidence_refs[0]);
      return reply(route, {
        kind: "source",
        source_id: "src_synthetic",
        revision: 1,
        content_hash: "synthetic-hash",
        text: "Synthetic authorized evidence",
        text_basis: "retained_text",
        body_complete: false,
      });
    },
  });
  await openOwner(page, f, "#/entry/entry_synthetic.1?revision=2");
  const link = page.getByRole("link", { name: "用当前修订继续工程学习 ↗" });
  await expect(link).toHaveAttribute(
    "href",
    "http://127.0.0.1:49152/#/growth?view=entry-context&entry_id=entry_synthetic.1&revision=2",
  );
  expect(f.writes).toHaveLength(0);
  await page.getByRole("tab", { name: /来源与证据/ }).click();
  await page.getByRole("button", { name: "读取已授权来源文本" }).click();
  await expect(
    page.getByText("Synthetic authorized evidence", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭来源文本" }).click();
  await expect(
    page.getByText("Synthetic authorized evidence", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "提出修改" })).toBeDisabled();
  expect(f.writes).toHaveLength(1);
});
test("fixture P05: mismatched evidence binding never displays foreign source text", async ({
  page,
}) => {
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/evidence/read");
      return reply(route, {
        kind: "source",
        source_id: "wrong-source",
        revision: 1,
        content_hash: "synthetic-hash",
        text: "Foreign bytes must remain hidden",
        text_basis: "retained_text",
      });
    },
  });
  await openOwner(page, f, "#/entry/entry_synthetic.1");
  await page.getByRole("tab", { name: /来源与证据/ }).click();
  await page.getByRole("button", { name: "读取已授权来源文本" }).click();
  await expect(page.getByRole("alert")).toContainText("无法验证的数据");
  await expect(page.locator("body")).not.toContainText(
    "Foreign bytes must remain hidden",
  );
});
for (const operation of ["update", "delete"] as const)
  test(`fixture P05: ${operation} stays a version-bound proposal through exact retry, never Writer apply`, async ({
    page,
  }) => {
    const f = await setupV1(page, {
      onWrite: async (route, write, index) => {
        expect(write.path).toBe("/changes");
        expect(write.body).toEqual({
          entry_id: entry.entry_id,
          base_revision: 3,
          operation,
          title: entry.title,
          body:
            operation === "update"
              ? "Synthetic candidate correction"
              : entry.body,
          evidence_refs: entry.evidence_refs,
          publication_intent: "public",
        });
        if (index === 1) return route.abort("connectionfailed");
        return reply(route, {
          ...entry,
          ...write.body,
          change_id: "chg_synthetic",
          state: "proposed",
          patch_hash: "synthetic-patch",
          risk_flags: [],
        });
      },
    });
    await openOwner(page, f, "#/entry/entry_synthetic.1");
    await page
      .getByRole("button", {
        name: operation === "update" ? "提出修改" : "提出删除",
      })
      .click();
    if (operation === "update")
      await page.getByLabel("候选正文").fill("Synthetic candidate correction");
    await expect(
      page.getByRole("button", { name: "提交版本化提案" }),
    ).toBeDisabled();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "提交版本化提案" }).click();
    await page.getByRole("button", { name: "重试同一请求" }).click();
    await expect(
      page.getByText("提案已创建，正式知识未改写", { exact: true }),
    ).toBeVisible();
    expect(f.writes).toHaveLength(2);
    expect(f.writes[0].key).toBe(f.writes[1].key);
    expect(f.writes[0].body).toEqual(f.writes[1].body);
  });
test("fixture P05: a late proposal receipt cannot navigate or restore a dismissed private editor", async ({
  page,
}) => {
  let release!: () => void;
  let arrived!: () => void;
  const started = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const f = await setupV1(page, {
    onWrite: async (route, write) => {
      expect(write.path).toBe("/changes");
      arrived();
      await held;
      return reply(route, {
        ...entry,
        ...write.body,
        change_id: "chg_late",
        state: "proposed",
        patch_hash: "synthetic",
        risk_flags: [],
      });
    },
  });
  await openOwner(page, f, "#/entry/entry_synthetic.1");
  await page.getByRole("button", { name: "提出修改" }).click();
  await page.getByLabel("候选正文").fill("Dismissed synthetic draft");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "提交版本化提案" }).click();
  await started;
  await page.getByRole("link", { name: "首页", exact: true }).click();
  release();
  await expect(
    page.getByRole("heading", { name: "我的知识入口" }),
  ).toBeVisible();
  await expect(page.getByLabel("候选正文")).toHaveCount(0);
  await expect(
    page.getByText("提案已创建，正式知识未改写", { exact: true }),
  ).toHaveCount(0);
  expect(page.url()).toContain("#/home");
});
for (const action of ["resume", "cancel"] as const)
  test(`fixture P11: ${action} confirms exact revision and retries the same operation key`, async ({
    page,
  }) => {
    const f = await setupV1(page, {
      onWrite: async (route, write, index) => {
        expect(write.path).toBe("/tasks/tsk_synthetic/" + action);
        expect(write.body).toEqual({ expected_revision: 4 });
        if (index === 1) return route.abort("connectionfailed");
        return reply(route, {
          ...task,
          revision: 5,
          state: action === "resume" ? "queued" : "cancelled",
        });
      },
    });
    await openOwner(page, f, "#/tasks/tsk_synthetic");
    await page
      .getByRole("button", {
        name: action === "resume" ? "恢复任务" : "取消任务",
      })
      .click();
    expect(f.writes).toHaveLength(0);
    await page.getByRole("button", { name: "确认任务操作" }).click();
    await page.getByRole("button", { name: "重试同一请求" }).click();
    await expect(
      page.getByText("已取得服务回执", { exact: true }),
    ).toBeVisible();
    expect(f.writes).toHaveLength(2);
    expect(f.writes[0].key).toBe(f.writes[1].key);
    expect(f.writes[0].body).toEqual(f.writes[1].body);
    await expect(page.getByText("任务已完成", { exact: true })).toHaveCount(0);
  });
for (const state of ["running", "succeeded"])
  test(`fixture P11: ${state} tasks do not expose unsafe resume/cancel`, async ({
    page,
  }) => {
    const f = await setupV1(page, { taskState: state });
    await openOwner(page, f, "#/tasks/tsk_synthetic");
    await expect(
      page.getByText(/运行中、终态或待对账任务不允许/),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "恢复任务" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "取消任务" })).toHaveCount(0);
    expect(f.writes).toHaveLength(0);
  });
