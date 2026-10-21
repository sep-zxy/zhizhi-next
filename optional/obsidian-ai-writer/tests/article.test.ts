import {
  countReadableCharacters,
  createArticleDiff,
  createTextReplacement,
  extractManagedArticle,
  extractMarkdownBody,
  isWithinTargetLength,
  isLikelySameArticle,
  replaceManagedArticle,
  replaceMarkdownBody
} from "../src/core/article";
import { describe, expect, it } from "vitest";

describe("article output", () => {
  it("preserves original frontmatter byte-for-byte and creates a managed region", () => {
    const original = "---\ntags: [draft]\naliases: [旧标题]\nupdated: 2025-01-02\n---\n\n旧正文\n";
    const result = replaceMarkdownBody(original, "---\nai_writer: true\n---\n\n# 新标题\n\n新正文");

    expect(result.startsWith("---\ntags: [draft]\naliases: [旧标题]\nupdated: 2025-01-02\n---")).toBe(true);
    expect(result).toContain("# 新标题");
    expect(result).not.toContain("ai_writer: true");
    expect(result).not.toContain("旧正文");
    expect(result).toContain("<!-- ai-writer:content:start -->");
    expect(result).toContain("<!-- ai-writer:content:end -->");
    expect(result).toContain("updated: 2025-01-02");
  });

  it("updates only the managed region and preserves surrounding content", () => {
    const original = "---\ntitle: A\n---\n\n前置说明\n\n<!-- ai-writer:content:start -->\n旧稿\n<!-- ai-writer:content:end -->\n\n后置链接\n";
    const result = replaceManagedArticle(original, "# 新稿\n\n正文");

    expect(result).toContain("前置说明");
    expect(result).toContain("后置链接");
    expect(result).not.toContain("旧稿");
    expect(extractManagedArticle(result)).toBe("# 新稿\n\n正文");
  });

  it("creates the smallest editor replacement without touching frontmatter", () => {
    const before = "---\ntitle: A\n---\n\n<!-- ai-writer:content:start -->\n旧稿\n<!-- ai-writer:content:end -->\n";
    const after = replaceManagedArticle(before, "新稿");
    const replacement = createTextReplacement(before, after);

    expect(replacement).not.toBeNull();
    expect(before.slice(0, replacement?.from)).toContain("title: A");
    expect(replacement?.text).toBe("新");
    expect(before.slice(replacement?.to)).toContain("稿\n<!-- ai-writer:content:end -->");
  });

  it("extracts the editable body and recognizes revisions of the same article", () => {
    const original = "---\ntitle: A\n---\n\n# 同一标题\n\n原正文内容足够长，用于判断文章。";
    const edited = "# 同一标题\n\n用户在工作区修改后的正文。";
    expect(extractMarkdownBody(original)).toContain("# 同一标题");
    expect(isLikelySameArticle(edited, original)).toBe(true);
  });

  it("returns line-level additions and removals", () => {
    const diff = createArticleDiff("旧句\n", "新句\n");
    expect(diff.some((part) => part.kind === "removed")).toBe(true);
    expect(diff.some((part) => part.kind === "added")).toBe(true);
  });

  it("counts readable content and validates the target range", () => {
    const count = countReadableCharacters("# 标题\n\n正文 123\n\n[[笔记|显示名]]");
    expect(count).toBeGreaterThan(8);
    expect(isWithinTargetLength(4000, 4000)).toBe(true);
    expect(isWithinTargetLength(3500, 4000)).toBe(false);
  });
});
