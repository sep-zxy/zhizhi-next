import { describe, expect, it } from "vitest";
import { lintMarkdownStructure } from "../src/core/markdownQuality";

describe("Markdown structure lint", () => {
  it("finds duplicate H1, unfinished markers and unclosed code fences", () => {
    const findings = lintMarkdownStructure("# A\n\n# B\n\n[待补充]\n\n```ts\nconst a = 1;");
    expect(findings.some((item) => item.rule.includes("一级标题"))).toBe(true);
    expect(findings.some((item) => item.rule.includes("未完成"))).toBe(true);
    expect(findings.some((item) => item.rule.includes("代码围栏"))).toBe(true);
  });

  it("accepts a compact structured article", () => {
    const findings = lintMarkdownStructure("# 标题\n\n正文。\n\n> 核心判断。\n\n- 动作一\n- 动作二\n");
    expect(findings).toEqual([]);
  });

  it("reports empty headings and empty list items", () => {
    const findings = lintMarkdownStructure("# 标题\n\n## \n\n- \n\n正文");
    expect(findings.some((item) => item.rule === "存在空标题")).toBe(true);
    expect(findings.some((item) => item.rule === "存在空引用或空列表项")).toBe(true);
  });
});
