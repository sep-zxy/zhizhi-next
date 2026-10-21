import { describe, expect, it } from "vitest";
import { extractArticleBody, lintKhazixArticle } from "../src/style/styleLint";

describe("Khazix style lint", () => {
  it("finds forbidden prose patterns", () => {
    const findings = lintKhazixArticle("在当今 AI 时代，首先我们来看看问题。本质上，这意味着变化。\n");
    expect(findings.some((finding) => finding.rule.includes("空泛"))).toBe(true);
    expect(findings.some((finding) => finding.rule.includes("首先"))).toBe(true);
    expect(findings.some((finding) => finding.rule.includes("本质上"))).toBe(true);
  });

  it("excludes frontmatter, code, comments, quotes and URLs from the article body", () => {
    const markdown = `---\ntitle: 首先\n---\n\n正文很自然。\n\n\`\`\`txt\n本质上\n\`\`\`\n\n> 这意味着\n\n<!-- 说白了 -->\n\nhttps://example.com/首先`;
    const body = extractArticleBody(markdown);
    const findings = lintKhazixArticle(markdown);

    expect(body).toContain("正文很自然");
    expect(findings).toHaveLength(0);
  });
});

