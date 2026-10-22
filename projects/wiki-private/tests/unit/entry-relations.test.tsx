// @vitest-environment jsdom
/** Synthetic projection and DOM checks, not a real-channel E2E. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseRelationView } from "../../src/relation-contracts";
import { EntryRelations } from "../../src/entry-relations";
import { WikiApi } from "../../src/api";
import { parseEntry } from "../../src/contracts";
afterEach(cleanup);
const root = { entry_id: "a", revision: 2, title: "根条目" };
const target = { entry_id: "b", revision: 3, title: "邻接条目" };
function projection(kind = "candidate") {
  return {
    root_entry_id: "a",
    nodes: [root, target],
    edges: [
      {
        edge_id: "rel_one",
        source_entry_id: "a",
        target_entry_id: "b",
        source_revision: 2,
        target_revision: 3,
        kind,
        is_fact: kind === "confirmed",
        relation_type: "supports",
        statement: "合成待核验关系",
        evidence_refs: [],
      },
    ],
    inference: "none",
    projection_revision: "fixture-projection",
    truncated: false,
  };
}
const entry = parseEntry({
  ...root,
  body: "合成正文",
  deleted: false,
  visibility: "private",
  entry_type: "concept",
  metadata: {},
  evidence_refs: [],
});
describe("direct relation projection boundary", () => {
  it.each(["navigation", "candidate"])(
    "preserves %s without promoting to fact",
    (kind) => {
      expect(parseRelationView(projection(kind), "a").edges[0].kind).toBe(kind);
    },
  );
  it("rejects confirmed without reviewed evidence", () => {
    expect(() => parseRelationView(projection("confirmed"), "a")).toThrow();
  });
  it("accepts backend-confirmed exact revision and evidence", () => {
    const value = projection("confirmed");
    Object.assign(value.edges[0], {
      verification: "reviewed_evidence",
      declared_target_revision: 3,
      evidence_refs: [
        {
          source_id: "source",
          content_hash: "hash",
          locator: { quote: "exact quote" },
        },
      ],
      review_provenance: { review_id: "review" },
    });
    expect(parseRelationView(value, "a").edges[0].kind).toBe("confirmed");
  });
  it("rejects an otherwise valid B graph returned to API request A", async () => {
    const value = { ...projection(), root_entry_id: "b" };
    const api = new WikiApi(
      async () =>
        new Response(JSON.stringify({ data: value, trace_id: "fixture" })),
    );
    await expect(api.relations("a")).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });
  it.each([
    { evidence_refs: [{}], review_provenance: {} },
    {
      evidence_refs: [
        {
          source_id: "source",
          learning_outcome_id: "outcome",
          content_hash: "hash",
        },
      ],
      review_provenance: {},
    },
  ])("rejects malformed confirmed evidence/provenance %j", (malformed) => {
    const value = projection("confirmed");
    Object.assign(value.edges[0], {
      verification: "reviewed_evidence",
      declared_target_revision: 3,
      ...malformed,
    });
    expect(() => parseRelationView(value, "a")).toThrow();
  });
  it("rejects hidden endpoint or stale edge revision", () => {
    const value = projection();
    value.edges[0].target_revision = 4;
    expect(() => parseRelationView(value, "a")).toThrow();
    expect(() =>
      parseRelationView({ ...projection(), nodes: [root] }, "a"),
    ).toThrow();
  });
  it("rejects wrong root, inferred results and unbounded responses", () => {
    expect(() => parseRelationView(projection(), "other")).toThrow();
    expect(() =>
      parseRelationView({ ...projection(), inference: "transitive" }, "a"),
    ).toThrow();
    expect(() =>
      parseRelationView(
        { ...projection(), edges: Array(21).fill(projection().edges[0]) },
        "a",
      ),
    ).toThrow();
  });
  it("loads only on explicit read and links exact neighbor revision", async () => {
    const transport = vi.fn(
      async (_input: RequestInfo | URL) =>
        new Response(
          JSON.stringify({ data: projection(), trace_id: "fixture" }),
        ),
    );
    render(<EntryRelations entry={entry} api={new WikiApi(transport)} />);
    expect(transport).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "查看局部关系" }));
    await screen.findByText("候选 · 待核验");
    expect(
      screen.getByRole("link", { name: /邻接条目/ }).getAttribute("href"),
    ).toBe("#/entry/b?revision=3");
    expect(transport.mock.calls[0][0]).toBe(
      "/api/knowledge/v1/relations/a?limit=20",
    );
  });
  it("does not misrepresent current graph as a historical version", async () => {
    const transport = async () =>
      new Response(JSON.stringify({ data: projection(), trace_id: "fixture" }));
    render(
      <EntryRelations
        entry={{ ...entry, revision: 1 }}
        api={new WikiApi(transport)}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "查看局部关系" }));
    await screen.findByText("关系版本与正在阅读的版本不同");
    expect(screen.queryByRole("link", { name: /邻接条目/ })).toBeNull();
  });
});
