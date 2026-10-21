// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WikiApi } from "../../src/api";
import { WikiApp } from "../../src/WikiApp";
const flags = { fixture: false, authentication_mode: "owner_loopback" };
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
function fixture(
  scopes = ["knowledge:read", "knowledge:write", "knowledge:review"],
) {
  let approved = false,
    authenticated = false;
  const paths: string[] = [];
  const api = new WikiApi(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/health")
      return json({
        status: "ready",
        service: "synthetic",
        worker_enabled: false,
        channel_worker_enabled: false,
        channel_status: "disabled",
        automatic_commit_enabled: false,
        publication_enabled: false,
        browser_authentication_mode: "owner_loopback",
      });
    if (path.endsWith("/request"))
      return json({
        ...flags,
        status: "waiting_approval",
        pairing_code: "A".repeat(20),
        pairing_proof: "P".repeat(43),
        expires_in_seconds: 120,
      });
    if (path.endsWith("/session")) {
      authenticated = approved;
      return json(
        approved
          ? {
              ...flags,
              status: "authenticated",
              csrf_token: "C".repeat(43),
              expires_in_seconds: 900,
              scopes,
            }
          : { ...flags, status: "waiting_approval" },
      );
    }
    if (path.endsWith("/logout")) {
      authenticated = false;
      return json({ ...flags, status: "waiting_auth" });
    }
    if (!authenticated)
      return json({ error: { code: "UNAUTHENTICATED" } }, 401);
    const entry = {
      entry_id: "entry_opaque.1",
      revision: 2,
      title: "Synthetic exact revision",
      body: "Synthetic private entry body",
      deleted: false,
      evidence_refs: [{ source_id: "src_fixture", content_hash: "hash" }],
    };
    return json({
      data: path.includes("/entries/")
        ? entry
        : path.includes("/intakes?") || path.includes("/tasks?")
          ? { items: [], has_more: false }
          : [],
      trace_id: "synthetic",
      task_id: null,
    });
  });
  return {
    api,
    paths,
    approve: () => {
      approved = true;
    },
  };
}
afterEach(() => {
  cleanup();
  delete window.__WIKI_PRIVATE_CONFIG__;
});
describe("owner UI and exact-version handoff", () => {
  it("labels owner independently, checks approval explicitly, offers scoped writes, and clears private content on logout", async () => {
    const f = fixture(),
      user = userEvent.setup();
    window.location.hash = "#/entry/entry_opaque.1?revision=2";
    window.__WIKI_PRIVATE_CONFIG__ = {
      engineeringUrl: "/learning/?discard=old#/growth?card=old",
    };
    render(<WikiApp api={f.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请 Owner 配对码" }),
    );
    expect(f.paths.some((p) => p.includes("/auth/local-test"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "检查本机批准" }));
    await screen.findByText(/尚未收到包内配对工具的批准/);
    expect(screen.queryByText("Synthetic private entry body")).toBeNull();
    f.approve();
    await user.click(screen.getByRole("button", { name: "检查本机批准" }));
    await screen.findByText("已连接 Owner 私有会话");
    const link = await screen.findByRole("link", {
      name: "用当前修订继续工程学习 ↗",
    });
    expect(link.getAttribute("href")).toBe(
      "/learning/#/growth?view=entry-context&entry_id=entry_opaque.1&revision=2",
    );
    expect(document.body.textContent).not.toContain("本机隔离测试");
    expect(document.body.textContent).toContain("指定历史版本");
    expect(
      (screen.getByRole("button", { name: "提出修改" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await user.click(
      screen.getByRole("button", { name: "注销本次 Owner 会话" }),
    );
    await screen.findByText(/服务已确认注销本次 Owner 会话/);
    expect(screen.queryByText("Synthetic private entry body")).toBeNull();
  });
  it("read-only owner sees genuine empty data but cannot offer intake or edit actions", async () => {
    const f = fixture(["knowledge:read"]),
      user = userEvent.setup();
    window.location.hash = "#/inbox";
    render(<WikiApp api={f.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请 Owner 配对码" }),
    );
    f.approve();
    await user.click(screen.getByRole("button", { name: "检查本机批准" }));
    await screen.findByText("当前 Owner 会话没有投递权限。");
    expect(
      (screen.getByRole("button", { name: "投递材料" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      f.paths.filter((p) => p === "/api/knowledge/v1/intakes"),
    ).toHaveLength(0);
  });
});
it.each(["0", "1e2", "1.5", "2\n"])(
  "invalid historical revision %j never falls back to latest",
  async (revision) => {
    const calls: string[] = [];
    const api = new WikiApi(async (input) => {
      calls.push(String(input));
      throw new Error("must not fetch latest");
    });
    window.location.hash =
      "#/entry/entry_opaque.1?" + new URLSearchParams({ revision });
    render(<WikiApp api={api} />);
    await screen.findByRole("alert");
    expect(calls).toHaveLength(0);
    expect(
      screen.queryByRole("link", { name: "用当前修订继续工程学习 ↗" }),
    ).toBeNull();
  },
);
