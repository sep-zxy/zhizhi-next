import { diffLines, type Change } from "diff";

export interface DiffLine {
  kind: "added" | "removed" | "unchanged";
  value: string;
}

export interface TextReplacement {
  from: number;
  to: number;
  text: string;
}

export const AI_WRITER_CONTENT_START = "<!-- ai-writer:content:start -->";
export const AI_WRITER_CONTENT_END = "<!-- ai-writer:content:end -->";

const FRONTMATTER_PATTERN = /^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/u;
const MANAGED_REGION_PATTERN = /(<!--\s*ai-writer:content:start\s*-->)[ \t]*\r?\n?([\s\S]*?)[ \t]*\r?\n?(<!--\s*ai-writer:content:end\s*-->)/iu;

export function createArticleDiff(before: string, after: string): DiffLine[] {
  return diffLines(before, after).map((part: Change) => ({
    kind: part.added ? "added" : part.removed ? "removed" : "unchanged",
    value: part.value
  }));
}

export function replaceMarkdownBody(original: string, generated: string) {
  return replaceManagedArticle(original, generated);
}

export function replaceManagedArticle(original: string, generated: string) {
  const generatedBody = extractMarkdownBody(generated);
  if (MANAGED_REGION_PATTERN.test(original)) {
    return original.replace(MANAGED_REGION_PATTERN, (_match, start: string, _body: string, end: string) =>
      `${start}\n${generatedBody}\n${end}`
    );
  }

  const frontmatter = original.match(FRONTMATTER_PATTERN)?.[0] || "";
  const prefix = frontmatter ? `${frontmatter}${frontmatter.endsWith("\n\n") ? "" : "\n"}` : "";
  return `${prefix}${AI_WRITER_CONTENT_START}\n${generatedBody}\n${AI_WRITER_CONTENT_END}\n`;
}

export function createTextReplacement(before: string, after: string): TextReplacement | null {
  if (before === after) return null;

  let from = 0;
  const maxPrefix = Math.min(before.length, after.length);
  while (from < maxPrefix && before[from] === after[from]) from += 1;

  let beforeEnd = before.length;
  let afterEnd = after.length;
  while (beforeEnd > from && afterEnd > from && before[beforeEnd - 1] === after[afterEnd - 1]) {
    beforeEnd -= 1;
    afterEnd -= 1;
  }

  return { from, to: beforeEnd, text: after.slice(from, afterEnd) };
}

export function extractManagedArticle(markdown: string) {
  return markdown.match(MANAGED_REGION_PATTERN)?.[2]?.trim() || null;
}

export function extractMarkdownBody(markdown: string) {
  const managed = extractManagedArticle(markdown);
  if (managed !== null) return managed;
  return markdown.replace(FRONTMATTER_PATTERN, "").trim();
}

export function isLikelySameArticle(current: string, known: string) {
  const currentBody = extractMarkdownBody(current);
  const knownBody = extractMarkdownBody(known);
  if (!currentBody || !knownBody) return false;

  const currentTitle = currentBody.match(/^#\s+(.+)$/mu)?.[1]?.trim();
  const knownTitle = knownBody.match(/^#\s+(.+)$/mu)?.[1]?.trim();
  if (currentTitle && knownTitle && currentTitle === knownTitle) return true;

  const currentLines = meaningfulLines(currentBody);
  const knownLines = meaningfulLines(knownBody);
  if (currentLines.size === 0 || knownLines.size === 0) return false;
  let shared = 0;
  for (const line of currentLines) {
    if (knownLines.has(line)) shared += 1;
  }
  return shared >= 2 && shared / Math.min(currentLines.size, knownLines.size) >= 0.25;
}

function meaningfulLines(markdown: string) {
  return new Set(markdown
    .split(/\r?\n/u)
    .map((line) => line.replace(/^\s*[#>*+-]+\s*/u, "").trim())
    .filter((line) => line.length >= 12));
}

export function countReadableCharacters(markdown: string) {
  const text = markdown
    .replace(/^---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)/u, "")
    .replace(/```[\s\S]*?```/gu, "")
    .replace(/<!--([\s\S]*?)-->/gu, "")
    .replace(/!?(?:\[([^\]]*)\]\([^)]*\)|\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\])/gu, "$1$2")
    .replace(/^[#>*+-]+\s*/gmu, "")
    .replace(/[*_~`|\[\](){}:]/gu, "")
    .replace(/\s+/gu, "");

  return Array.from(text).length;
}

export function isWithinTargetLength(actual: number, target: number, tolerance = 0.1) {
  const minimum = Math.round(target * (1 - tolerance));
  const maximum = Math.round(target * (1 + tolerance));
  return actual >= minimum && actual <= maximum;
}
