// @vitest-environment jsdom
/** Synthetic read-only DOM coverage. Not browser screenshots or real service E2E. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { WikiApp } from "../../src/WikiApp";
import { WikiApi } from "../../src/api";

afterEach(cleanup);
function emptyApi() {
  return new WikiApi(async (input) => {
    const path = String(input);
    if (path === "/health")
      return new Response(
        JSON.stringify({
          status: "ready",
          service: "synthetic",
          worker_enabled: false,
          channel_worker_enabled: false,
          channel_status: "disabled",
          automatic_commit_enabled: false,
          publication_enabled: false,
        }),
      );
    const data = path.includes("/intakes/capabilities")
      ? { capabilities: [] }
      : path.includes("/tasks?") || path.includes("/intakes?")
        ? { items: [], has_more: false }
        : [];
    return new Response(
      JSON.stringify({ data, trace_id: "fixture-empty", task_id: null }),
    );
  });
}
describe("approved private pages honest empty and planned states", () => {
  it.each([
    ["home", "从第一份知识开始"],
    ["inbox", "当前没有符合条件的收件"],
    ["catalog", "尚无正式知识条目"],
    ["reviews", "暂无需要本人确认的提案"],
    ["tasks", "没有符合条件的任务"],
  ])("%s does not fabricate populated data", async (route, heading) => {
    window.history.replaceState({}, "", "/wiki/#/" + route);
    render(<WikiApp api={emptyApi()} />);
    await screen.findByRole("heading", { name: heading });
    expect(
      document.querySelectorAll(
        ".category-portal, .intake-row, .task-row, .review-row, .entry-row",
      ),
    ).toHaveLength(0);
  });
  it("P14 ready health does not claim account, channel or publication success", async () => {
    window.history.replaceState({}, "", "/wiki/#/settings");
    render(<WikiApp api={emptyApi()} />);
    await screen.findByText("ready");
    expect(screen.getByText("服务没有返回解析能力记录。")).toBeTruthy();
    expect(screen.getByText("本页没有实时提供者诊断接口")).toBeTruthy();
    expect(screen.getByText("服务未启用自动发布")).toBeTruthy();
    expect(screen.queryByText("已部署", { exact: true })).toBeNull();
  });
  it("P13 plans are readable without installer or invented verification", async () => {
    window.history.replaceState({}, "", "/wiki/#/tools");
    render(<WikiApp api={emptyApi()} />);
    expect(screen.getByText("后续规划 · 尚未实现")).toBeTruthy();
    expect(document.querySelectorAll(".planning-tool")).toHaveLength(4);
    expect(
      screen.queryByRole("button", { name: /安装|启用|授权|升级/ }),
    ).toBeNull();
    expect(screen.getAllByText("规划 · 未注册")).toHaveLength(4);
  });
});
