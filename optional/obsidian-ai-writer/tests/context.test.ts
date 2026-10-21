import { describe, expect, it } from "vitest";
import { assembleContext, createContextItem, upsertContextItem } from "../src/core/context";

describe("context assembler", () => {
  it("includes only explicit items with visible source boundaries", () => {
    const note = createContextItem("note", "素材 A", "正文内容", "notes/a.md");
    const snapshot = assembleContext([note]);

    expect(snapshot.text).toContain("素材开始：素材 A（notes/a.md）");
    expect(snapshot.text).toContain("正文内容");
    expect(snapshot.items).toHaveLength(1);
    expect(snapshot.truncated).toBe(false);
  });

  it("updates the same source instead of duplicating it", () => {
    const first = createContextItem("note", "A", "旧内容", "a.md");
    const next = createContextItem("note", "A", "新内容", "a.md");
    const items = upsertContextItem([first], next);

    expect(items).toHaveLength(1);
    expect(items[0]?.content).toBe("新内容");
  });

  it("marks context truncation", () => {
    const item = createContextItem("note", "长素材", "内容".repeat(100), "long.md");
    const snapshot = assembleContext([item], 90);

    expect(snapshot.truncated).toBe(true);
    expect(snapshot.text).toContain("[内容因预算截断]");
  });
});

