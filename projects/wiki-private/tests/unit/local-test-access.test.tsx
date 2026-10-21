// @vitest-environment jsdom
/** Isolated component fixtures. No Windows identity, browser session, or live E2E. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WikiApp } from "../../src/WikiApp";
import { WikiApi, type Transport } from "../../src/api";

const CODE = "ABCDEF0123456789ABCD";
const PROOF = "P".repeat(43),
  CSRF = "C".repeat(43);
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
function fixture(
  options: {
    disabled?: boolean;
    logoutFailure?: boolean;
    logout?: Promise<Response>;
  } = {},
) {
  let approved = false,
    authenticated = false;
  const calls: { path: string; headers: Headers }[] = [];
  const transport: Transport = async (input, init = {}) => {
    const path = String(input),
      headers = new Headers(init.headers);
    calls.push({ path, headers });
    if (path.endsWith("/request")) {
      if (options.disabled)
        return json({ error: { code: "UNAUTHENTICATED" } }, 401);
      return json({
        status: "waiting_approval",
        pairing_code: CODE,
        pairing_proof: PROOF,
        expires_in_seconds: 120,
        fixture: true,
      });
    }
    if (path.endsWith("/session")) {
      if (!approved) return json({ status: "waiting_approval", fixture: true });
      authenticated = true;
      return json({
        status: "authenticated",
        csrf_token: CSRF,
        expires_in_seconds: 900,
        fixture: true,
      });
    }
    if (path.endsWith("/logout")) {
      if (options.logoutFailure)
        throw new Error("fixture network interruption");
      if (options.logout) {
        const response = await options.logout;
        authenticated = false;
        return response;
      }
      authenticated = false;
      return json({ status: "waiting_auth", fixture: true });
    }
    if (!authenticated)
      return json({ error: { code: "UNAUTHENTICATED" } }, 401);
    const data =
      path.includes("/tasks?") || path.includes("/intakes?")
        ? { items: [], has_more: false }
        : [];
    return json({ data, task_id: null, trace_id: "fixture" });
  };
  return {
    api: new WikiApi(transport),
    calls,
    approve: () => {
      approved = true;
    },
  };
}
beforeEach(() => {
  window.history.replaceState({}, "", "/wiki/#/home");
  localStorage.clear();
  sessionStorage.clear();
  delete window.__WIKI_PRIVATE_CONFIG__;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("local-test pairing UI fixtures", () => {
  it("cannot reopen private content with the generic retry button during pending logout", async () => {
    let finish!: (response: Response) => void;
    const logout = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const user = userEvent.setup(),
      server = fixture({ logout });
    render(<WikiApp api={server.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请本机测试配对码" }),
    );
    server.approve();
    await user.click(
      await screen.findByRole("button", { name: "检查本机批准" }),
    );
    await screen.findByRole("heading", { name: "从第一份知识开始" });
    await user.click(screen.getByRole("button", { name: "注销本次测试会话" }));
    const retry = screen.getByRole("button", {
      name: "已完成受信任连接，重新读取",
    }) as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    const requests = server.calls.length;
    await user.click(retry);
    act(() => server.api.retryAccess());
    expect(server.calls).toHaveLength(requests);
    expect(
      screen.queryByRole("heading", { name: "从第一份知识开始" }),
    ).toBeNull();
    await act(async () => {
      finish(json({ status: "waiting_auth", fixture: true }));
    });
    await screen.findByText(/服务已确认注销本次测试会话/);
    expect(retry.disabled).toBe(false);
  });
  it("requires explicit request and approval checks, hides proof/CSRF, then shows real empty state and logout", async () => {
    const user = userEvent.setup(),
      server = fixture();
    render(<WikiApp api={server.api} />);
    await screen.findByRole("button", { name: "申请本机测试配对码" });
    expect(server.calls.some((call) => call.path.includes("/auth/"))).toBe(
      false,
    );
    await user.dblClick(
      screen.getByRole("button", { name: "申请本机测试配对码" }),
    );
    expect(
      server.calls.filter((call) => call.path.endsWith("/request")),
    ).toHaveLength(1);
    expect((await screen.findByLabelText("一次性配对码")).textContent).toBe(
      CODE,
    );
    expect(document.body.textContent).not.toContain(PROOF);
    expect(document.body.textContent).not.toContain(CSRF);
    expect(
      (
        screen.getByRole("button", {
          name: "已完成受信任连接，重新读取",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "检查本机批准" }));
    await screen.findByText(/尚未收到包内配对工具的批准/);
    expect(screen.queryByText("已连接本机测试身份")).toBeNull();
    server.approve();
    await user.click(screen.getByRole("button", { name: "检查本机批准" }));
    await screen.findByText("已连接本机测试身份");
    await screen.findByRole("heading", { name: "从第一份知识开始" });
    expect(screen.queryByLabelText("一次性配对码")).toBeNull();
    expect(Object.keys(localStorage)).toEqual([]);
    expect(Object.keys(sessionStorage)).toEqual([]);
    expect(window.location.href).not.toContain(CODE);
    expect(window.location.href).not.toContain(PROOF);
    await user.click(screen.getByRole("button", { name: "注销本次测试会话" }));
    await screen.findByText(/服务已确认注销本次测试会话/);
    expect(
      screen.queryByRole("heading", { name: "从第一份知识开始" }),
    ).toBeNull();
    const logout = server.calls.find((call) => call.path.endsWith("/logout"))!;
    expect(logout.headers.get("X-Wiki-Pairing-Proof")).toBe(PROOF);
    expect(logout.headers.get("X-Wiki-CSRF")).toBe(CSRF);
  });
  it("keeps default-disabled service waiting for authentication instead of manufacturing a pairing code", async () => {
    const user = userEvent.setup(),
      server = fixture({ disabled: true });
    render(<WikiApp api={server.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请本机测试配对码" }),
    );
    await screen.findByText(/当前服务未提供可用的本机测试配对/);
    expect(screen.queryByLabelText("一次性配对码")).toBeNull();
    expect(screen.queryByText("已连接本机测试身份")).toBeNull();
  });
  it("cancels only local pending state without claiming server logout", async () => {
    const user = userEvent.setup(),
      server = fixture();
    render(<WikiApp api={server.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请本机测试配对码" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "取消本页配对" }),
    );
    expect(screen.queryByLabelText("一次性配对码")).toBeNull();
    expect(
      server.calls.filter((call) => call.path.endsWith("/logout")),
    ).toHaveLength(0);
    await screen.findByText(/已清除此页配对信息/);
  });
  it("disables real URL intake for the explicitly synthetic test session", async () => {
    const user = userEvent.setup(),
      server = fixture();
    render(<WikiApp api={server.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请本机测试配对码" }),
    );
    server.approve();
    await user.click(
      await screen.findByRole("button", { name: "检查本机批准" }),
    );
    await screen.findByText("已连接本机测试身份");
    await user.click(screen.getByRole("link", { name: "收件箱" }));
    await screen.findByText("合成测试会话不投递真实链接");
    expect(
      (screen.getByRole("button", { name: "投递链接" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      server.calls.filter((call) => call.path === "/api/knowledge/v1/intakes"),
    ).toHaveLength(0);
  });
  it("reports uncertain logout without a false successful revocation message", async () => {
    const user = userEvent.setup(),
      server = fixture({ logoutFailure: true });
    render(<WikiApp api={server.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请本机测试配对码" }),
    );
    server.approve();
    await user.click(
      await screen.findByRole("button", { name: "检查本机批准" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "注销本次测试会话" }),
    );
    await screen.findByText(/服务端注销未得到确认/);
    expect(screen.queryByText(/服务已确认注销本次测试会话/)).toBeNull();
    expect(server.api.getLocalTestSession().status).toBe("waiting_auth");
  });
  it("hides an expired pairing code on foreground expiry check without sending another request", async () => {
    let now = Date.now();
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const user = userEvent.setup(),
      server = fixture();
    render(<WikiApp api={server.api} />);
    await user.click(
      await screen.findByRole("button", { name: "申请本机测试配对码" }),
    );
    await screen.findByLabelText("一次性配对码");
    const before = server.calls.length;
    now += 120_001;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(screen.queryByLabelText("一次性配对码")).toBeNull();
    expect(server.calls).toHaveLength(before);
    expect(server.api.getLocalTestSession().status).toBe("waiting_auth");
  });
});
