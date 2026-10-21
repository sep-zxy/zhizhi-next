import type { ArticleTypeId } from "./types";

const SECTION_TO_ARTICLE_TYPE: Record<string, ArticleTypeId> = {
  blog: "blog",
  "app-note": "app-note",
  software: "software"
};

export function inferArticleTypeFromMarkdown(markdown: string): ArticleTypeId {
  const frontmatter = markdown.match(/^---\s*\r?\n([\s\S]*?)\r?\n---/u)?.[1] || "";
  const section = frontmatter.match(/^section\s*:\s*["']?([^\r\n"']+)/mu)?.[1]?.trim().toLowerCase();
  return section && SECTION_TO_ARTICLE_TYPE[section] ? SECTION_TO_ARTICLE_TYPE[section] : "blog";
}

export function sanitizeFileName(value: string) {
  const sanitized = value
    .trim()
    .replace(/[<>:"/\\|?*\u0000-\u001F]/gu, "-")
    .replace(/[. ]+$/gu, "")
    .replace(/-{2,}/gu, "-");
  return sanitized || "未命名";
}

export function normalizeVaultPath(value: string) {
  return value.replace(/\\/gu, "/").replace(/^\/+|\/+$/gu, "");
}
