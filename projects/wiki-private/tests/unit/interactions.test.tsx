// @vitest-environment jsdom
/** Synthetic React DOM interaction checks, not a browser or real-account E2E. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WikiApp } from "../../src/WikiApp";
import { WikiApi, type Transport } from "../../src/api";

const entry = {
  entry_id: "ent_component",
  revision: 2,
  title: "隔离组件知识",
  body: "# 隔离组件知识\n\n正式正文与来源分开。",
  entry_type: "concept",
  visibility: "private",
  publication_intent: "public",
  publication_eligible: false,
  evidence_refs: [
    { source_id: "src_component", source_revision: 3, claim_ids: ["claim_1"] },
  ],
  metadata: { categories: ["通用知识"], tags: ["组件测试"] },
  deleted: false,
  created_at: "2026-10-20T12:00:00Z",
  updated_at: "2026-10-20T12:00:00Z",
};
const change = {
  ...entry,
  change_id: "chg_component",
  state: "needs_user",
  operation: "update",
  base_revision: 2,
  patch_hash: "exact_component_patch",
  body: "# 新版正文\n\n需要本人核对的差异。",
  risk_flags: ["core_conclusion"],
};
const intake = {
  intake_id: "int_component",
  state: "preview_ready",
  channel: "http",
  preview_revision: 4,
  expires_at: "2099-01-01T00:00:00Z",
  preview: {
    document: {
      title: "隔离临时来源",
      body_text: "只保留明确核对的版本。",
      body_complete: true,
      metadata: { text_basis: "body" },
    },
  },
};
const task = {
  task_id: "tsk_component",
  kind: "knowledge.component-private",
  state: "waiting_auth",
  revision: 2,
  result: {
    model: {
      status: "waiting_auth",
      failure: { stage: "authentication", kind: "credential_required" },
    },
  },
};
function response(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({ data, task_id: null, trace_id: "component_trace" }),
    { status, headers: { "Content-Type": "application/json" } },
  );
}
function unauthorized() {
  return new Response(
    JSON.stringify({
      error: { code: "UNAUTHENTICATED", detail: "raw must not appear" },
    }),
    { status: 401 },
  );
}
type Write = {
  path: string;
  body: Record<string, unknown>;
  key: string | null;
};
function fixture(
  options: {
    expired?: boolean;
    events?: Promise<Response>;
    uncertainFirstWrite?: boolean;
  } = {},
) {
  const writes: Write[] = [];
  let retained = false;
  const transport: Transport = async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace("/api/knowledge/v1", "");
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      writes.push({
        path,
        body,
        key: new Headers(init.headers).get("Idempotency-Key"),
      });
      if (options.uncertainFirstWrite && writes.length === 1)
        throw new Error("synthetic network interruption");
      if (path.endsWith("/decisions"))
        return response({
          decision: body.decision,
          change_id: change.change_id,
          task_id: task.task_id,
        });
      retained = body.decision === "keep";
      return response({
        state: retained ? "retained" : "discarded",
        intake_id: intake.intake_id,
      });
    }
    if (path === "/entries") return response([entry]);
    if (path.startsWith("/entries/"))
      return response({
        ...entry,
        revision: Number(url.searchParams.get("revision")) || 2,
      });
    if (path === "/reviews") return response([change]);
    if (path === "/changes/chg_component") return response(change);
    if (path.endsWith("/decision-challenge"))
      return response({
        change_id: change.change_id,
        patch_hash: change.patch_hash,
        base_revision: 2,
        policy_version: "1",
        expires_at: options.expired
          ? "2000-01-01T00:00:00Z"
          : "2099-01-01T00:00:00Z",
        decision_nonce: "inert-component-nonce",
      });
    if (path === "/tasks") return response({ items: [task], has_more: false });
    if (path === "/tasks/tsk_component") return response(task);
    if (path.endsWith("/events")) return options.events || response([]);
    if (path === "/intakes")
      return response({ items: [intake], has_more: false });
    if (path === "/intakes/int_component")
      return response(
        retained
          ? { ...intake, state: "retained", source_id: "src_component" }
          : intake,
      );
    return response(null, 404);
  };
  return { api: new WikiApi(transport), writes, transport };
}

beforeEach(() => {
  window.history.replaceState({}, "", "/wiki/#/home");
  window.localStorage.clear();
  delete window.__WIKI_PRIVATE_CONFIG__;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("isolated React DOM interaction", () => {
  it("does not navigate back to an intake when its receipt arrives after leaving the form", async () => {
    let finish!: (value: Response) => void;
    const submitted = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const base = fixture(),
      user = userEvent.setup();
    let writes = 0;
    const api = new WikiApi((input, init) => {
      if (
        String(input) === "/api/knowledge/v1/intakes" &&
        init?.method === "POST"
      ) {
        writes += 1;
        return submitted;
      }
      return base.transport(input, init);
    });
    window.history.replaceState({}, "", "/wiki/#/inbox");
    render(<WikiApp api={api} />);
    await user.click(await screen.findByRole("button", { name: "投递链接" }));
    await user.type(
      screen.getByLabelText("来源链接"),
      "https://example.com/synthetic",
    );
    await user.click(screen.getByRole("button", { name: "提交临时预览" }));
    expect(writes).toBe(1);
    await user.click(screen.getByRole("link", { name: "知识目录" }));
    await screen.findByRole("heading", { name: "知识目录与全库检索" });
    await act(async () => {
      finish(response(intake));
    });
    expect(window.location.hash).toBe("#/catalog");
    expect(
      screen.queryByRole("heading", { name: "素材预览与来源详情" }),
    ).toBeNull();
  });
  it("does not invent cross-app entrypoints that the host has not supplied", async () => {
    render(<WikiApp api={fixture().api} />);
    await screen.findByText("工程学习入口待连接");
    expect(document.querySelector('a[href="/public/"]')).toBeNull();
    expect(document.querySelector('a[href="/#/growth?view=today"]')).toBeNull();
    expect(screen.queryByRole("link", { name: /去知枝/ })).toBeNull();
  });

  it("uses an explicit same-origin host path without forwarding the private search", async () => {
    window.__WIKI_PRIVATE_CONFIG__ = {
      publicWikiUrl: "/published/",
      engineeringUrl: "/engineering/",
    };
    window.history.replaceState({}, "", "/wiki/#/home?q=private-query");
    render(<WikiApp api={fixture().api} />);
    await screen.findByRole("heading", { name: "通用知识" });
    expect(
      screen.getByRole("link", { name: /公开阅读站/ }).getAttribute("href"),
    ).toBe("/published/");
    expect(
      screen.getByRole("link", { name: /去知枝/ }).getAttribute("href"),
    ).toBe("/engineering/");
  });

  it("navigates private home → catalog → canonical entry and reads a real requested revision", async () => {
    const user = userEvent.setup();
    render(<WikiApp api={fixture().api} />);
    await screen.findByRole("heading", { name: "通用知识" });
    await user.click(screen.getByRole("link", { name: /进入完整目录/ }));
    await screen.findByRole("heading", { name: "知识目录与全库检索" });
    await user.click(screen.getByRole("link", { name: /隔离组件知识/ }));
    await screen.findByText("发布状态未提供");
    await user.type(screen.getByLabelText("读取指定修订"), "1");
    await user.click(screen.getByRole("button", { name: "读取" }));
    await screen.findByText("v1 · 指定历史版本");
    expect(window.location.hash).toContain("revision=1");
  });

  it("unmounts already loaded private data as soon as another domain request returns 401", async () => {
    const user = userEvent.setup();
    let finish!: (value: Response) => void;
    const events = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    window.history.replaceState({}, "", "/wiki/#/tasks/tsk_component");
    render(<WikiApp api={fixture({ events }).api} />);
    await screen.findByRole("heading", { name: task.kind });
    await user.type(
      screen.getByLabelText("搜索私有知识"),
      "不应留在隐藏 DOM 的查询",
    );
    await act(async () => {
      finish(unauthorized());
    });
    await screen.findByRole("heading", { name: "连接私有工作区" });
    expect(screen.queryByRole("heading", { name: task.kind })).toBeNull();
    expect(screen.queryByText("raw must not appear")).toBeNull();
    expect(document.querySelector(".global-search")).toBeNull();
    expect(Object.keys(localStorage)).toEqual([]);
  });

  it("clears the search draft rather than restoring it after an explicit reconnect", async () => {
    const user = userEvent.setup(),
      { api } = fixture();
    render(<WikiApp api={api} />);
    await screen.findByRole("heading", { name: "通用知识" });
    await user.type(
      screen.getByLabelText("搜索私有知识"),
      "仅本次会话的私有查询",
    );
    act(() => api.forgetLocalTestSession());
    expect(screen.queryByLabelText("搜索私有知识")).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "已完成受信任连接，重新读取" }),
    );
    await screen.findByRole("heading", { name: "通用知识" });
    expect(
      (screen.getByLabelText("搜索私有知识") as HTMLInputElement).value,
    ).toBe("");
  });

  it("rechecks review expiry at click time even if a background display timer has not run", async () => {
    const schedule = window.setInterval.bind(window);
    vi.spyOn(window, "setInterval").mockImplementation(
      (handler) =>
        // jsdom returns a browser ID; Vitest sees Node's last timer overload.
        schedule(handler, 60_000) as unknown as ReturnType<
          typeof window.setInterval
        >,
    );
    let now = Date.parse("2098-12-31T23:59:00Z");
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const user = userEvent.setup(),
      { api, writes } = fixture();
    window.history.replaceState({}, "", "/wiki/#/review/chg_component");
    render(<WikiApp api={api} />);
    await user.click(
      await screen.findByRole("button", { name: "准备批准本次差异" }),
    );
    await user.type(screen.getByLabelText("决定说明"), "核对后的说明");
    await user.click(screen.getByRole("checkbox"));
    now = Date.parse("2099-01-01T00:00:01Z");
    await user.click(screen.getByRole("button", { name: "提交这次决定" }));
    expect(writes).toHaveLength(0);
    await screen.findByText(/这次确认已过期/);
  });

  it("rechecks preview expiry at click time without sending an expired retention request", async () => {
    const schedule = window.setInterval.bind(window);
    vi.spyOn(window, "setInterval").mockImplementation(
      (handler) =>
        schedule(handler, 60_000) as unknown as ReturnType<
          typeof window.setInterval
        >,
    );
    let now = Date.parse("2098-12-31T23:59:00Z");
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const user = userEvent.setup(),
      { api, writes } = fixture();
    window.history.replaceState({}, "", "/wiki/#/intake/int_component");
    render(<WikiApp api={api} />);
    await user.click(
      await screen.findByRole("button", { name: "私有保留并整理" }),
    );
    now = Date.parse("2099-01-01T00:00:01Z");
    await user.click(screen.getByRole("button", { name: "确认本次决定" }));
    expect(writes).toHaveLength(0);
    await screen.findByText(/临时预览已到期/);
  });

  it("cancels approval without writes, then submits exactly one bound decision on double click", async () => {
    const user = userEvent.setup();
    const { api, writes } = fixture();
    window.history.replaceState({}, "", "/wiki/#/review/chg_component");
    render(<WikiApp api={api} />);
    await screen.findByText("需要本人核对的差异。");
    expect(document.body.textContent).toContain('"source_revision": 3');
    expect(document.body.textContent).toContain('"claim_1"');
    await user.click(screen.getByRole("button", { name: "准备批准本次差异" }));
    await user.click(screen.getByRole("button", { name: "取消，不作决定" }));
    expect(writes).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "准备批准本次差异" }));
    await user.type(screen.getByLabelText("决定说明"), "已核对隔离测试差异");
    await user.click(screen.getByRole("checkbox"));
    await user.dblClick(screen.getByRole("button", { name: "提交这次决定" }));
    await screen.findByText("服务已记录决定");
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toMatchObject({
      expected_patch_hash: change.patch_hash,
      base_revision: 2,
      decision_nonce: "inert-component-nonce",
    });
    expect(writes[0].key).toBeTruthy();
  });

  it("does not let an expired challenge enable confirmation", async () => {
    const user = userEvent.setup();
    const { api, writes } = fixture({ expired: true });
    window.history.replaceState({}, "", "/wiki/#/review/chg_component");
    render(<WikiApp api={api} />);
    await user.click(
      await screen.findByRole("button", { name: "准备批准本次差异" }),
    );
    await screen.findByText(/已失效，请取消并重新读取差异/);
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(
      true,
    );
    expect(
      (
        screen.getByRole("button", {
          name: "提交这次决定",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(writes).toHaveLength(0);
  });

  it("keeps cancellation inert and binds retention to the viewed preview revision", async () => {
    const user = userEvent.setup();
    const { api, writes } = fixture();
    window.history.replaceState({}, "", "/wiki/#/intake/int_component");
    render(<WikiApp api={api} />);
    await user.click(
      await screen.findByRole("button", { name: "私有保留并整理" }),
    );
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(writes).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "私有保留并整理" }));
    await user.click(screen.getByRole("button", { name: "确认本次决定" }));
    await screen.findAllByText("已私有保留");
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toEqual({ decision: "keep", preview_revision: 4 });
  });

  it("keeps an uncertain write pending and reuses the same key only on explicit user retry", async () => {
    const user = userEvent.setup();
    const { api, writes } = fixture({ uncertainFirstWrite: true });
    window.history.replaceState({}, "", "/wiki/#/intake/int_component");
    render(<WikiApp api={api} />);
    await user.click(
      await screen.findByRole("button", { name: "私有保留并整理" }),
    );
    await user.click(screen.getByRole("button", { name: "确认本次决定" }));
    await screen.findByText(/当前结果未得到确认/);
    expect(writes).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "确认本次决定" }));
    await waitFor(() => expect(writes).toHaveLength(2));
    expect(writes[0].key).toBe(writes[1].key);
    expect(writes[0].body).toEqual(writes[1].body);
  });

  it("shows controlled model diagnostics without claiming a verified root cause", async () => {
    window.history.replaceState({}, "", "/wiki/#/tasks/tsk_component");
    render(<WikiApp api={fixture().api} />);
    await screen.findByText("credential_required");
    expect(screen.getByText(/不代表已核实根因/)).toBeTruthy();
    expect(screen.getByText("尚未确认正式知识提交。")).toBeTruthy();
  });
});
