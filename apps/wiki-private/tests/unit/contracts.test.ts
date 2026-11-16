import { describe, it, expect } from "vitest";
import {
  parseEntry,
  parseTask,
  parsePage,
  parseIntake,
} from "../../src/contracts";
import {
  categoryCounts,
  parseRoute,
  publication,
  safeNavigation,
  taskLabel,
  taskResult,
  routeHref,
} from "../../src/state";
const entry = {
  entry_id: "ent_1",
  revision: 1,
  title: "真实标题",
  body: "正式正文",
  entry_type: "concept",
  visibility: "public",
  publication_eligible: true,
  evidence_refs: [],
  metadata: { categories: ["生活", "生活"], tags: ["读书"] },
  deleted: false,
  created_at: "2026-10-20T10:00:00Z",
  updated_at: "2026-10-20T10:00:00Z",
};
describe("domain projection contracts", () => {
  it("requires the canonical body instead of inventing knowledge", () =>
    expect(() => parseEntry({ ...entry, body: undefined })).toThrow());
  it("drops owner, paths, private internal fields outside the displayed contract", () => {
    const result = parseEntry({
      ...entry,
      owner_id: "owner",
      path: "/secret",
      raw_path: "hidden",
    });
    expect(result).not.toHaveProperty("path");
    expect(result).not.toHaveProperty("owner_id");
  });
  it("strips task leases, raw input and model errors", () => {
    const result = parseTask({
      task_id: "tsk_1",
      kind: "knowledge.generate",
      state: "waiting_auth",
      revision: 2,
      lease_token: "secret",
      payload: { password: "secret" },
      error: { code: "AUTH_REQUIRED", detail: "raw secret" },
    });
    expect(result).not.toHaveProperty("lease_token");
    expect(result).not.toHaveProperty("payload");
    expect(result.error).toEqual({ code: "AUTH_REQUIRED" });
  });
  it("only retains controlled task result diagnostics, never model output or free-text summaries", () => {
    const result = parseTask({
      task_id: "tsk_1",
      kind: "knowledge.generate",
      state: "failed",
      revision: 2,
      result: {
        entry_id: "ent_1",
        revision: 1,
        output: "secret",
        model: {
          status: "failed",
          error_code: "AUTH_REQUIRED",
          output: "secret",
          failure: {
            stage: "authenticate",
            kind: "authentication",
            summary: "untrusted raw secret",
            event_type: "diagnostic",
            exit_code: 1,
          },
        },
      },
    });
    expect(JSON.stringify(result)).not.toContain("secret");
    expect(result.result).toMatchObject({
      entry_id: "ent_1",
      model: {
        status: "failed",
        failure: {
          stage: "authenticate",
          kind: "authentication",
          exit_code: 1,
        },
      },
    });
  });
  it("requires explicit pagination state", () => {
    expect(() => parsePage({ items: [] }, parseIntake)).toThrow();
    expect(parsePage({ items: [], has_more: false }, parseIntake)).toEqual({
      items: [],
      has_more: false,
    });
  });
  it("does not call a completed task published", () => {
    const task = parseTask({
      task_id: "tsk_1",
      kind: "knowledge.review",
      state: "succeeded",
      revision: 2,
      result: { status: "ok" },
    });
    expect(taskResult(task).label).toContain("不等于");
    expect(
      taskResult({
        ...task,
        state: "running",
        result: { entry_id: "ent_1", revision: 1 },
      }).label,
    ).toContain("尚未确认");
  });
  it("separates visibility intent, eligibility and actual publishing", () =>
    expect(publication(parseEntry(entry))).toEqual({
      intent: "public",
      eligible: "资格通过",
      published: "发布状态未提供",
    }));
  it("uses actual category data and deduplicates one entry within a category", () =>
    expect(
      categoryCounts([
        parseEntry(entry),
        parseEntry({ ...entry, entry_id: "ent_2", deleted: true }),
      ]),
    ).toEqual([{ name: "生活", count: 1 }]));
  it("does not guess an unknown task state", () =>
    expect(taskLabel("future_state")).toContain("未知状态"));
});
describe("routing and privacy navigation", () => {
  it("keeps normal hash routes and query strings", () => {
    expect(parseRoute("#/entry/ent%20one?revision=2").id).toBe("ent one");
    expect(
      parseRoute("#/entry/ent%20one?revision=2").query.get("revision"),
    ).toBe("2");
    expect(routeHref("entry", "x/y")).toBe("#/entry/x%2Fy");
  });
  it("fails closed for broken encoded identifiers", () =>
    expect(parseRoute("#/entry/%E0%A4%A").page).toBe("not-found"));
  it("rejects external and protocol-relative navigation configuration", () => {
    for (const value of [
      "https://bad.test",
      "//bad.test",
      "/\\bad.test",
      "/path\nheader",
    ])
      expect(safeNavigation(value, "/public/")).toBe("/public/");
    expect(safeNavigation("/#/growth?view=today", "/")).toBe(
      "/#/growth?view=today",
    );
  });
});
