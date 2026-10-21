import type { StyleLintFinding } from "../style/styleLint";
import { findUnresolvedMarkers } from "./outputValidation";

export function lintMarkdownStructure(markdown: string): StyleLintFinding[] {
  const findings: StyleLintFinding[] = [];
  const body = stripFrontmatter(markdown);
  const lines = body.split(/\r?\n/u);
  const h1Count = lines.filter((line) => /^#\s+\S/u.test(line)).length;
  if (h1Count !== 1) {
    findings.push({ severity: "error", rule: "一级标题数量应为 1", excerpt: `当前 ${h1Count} 个` });
  }

  const emptyHeadings = lines.filter((line) => /^\s*#{1,6}\s*$/u.test(line)).length;
  if (emptyHeadings > 0) {
    findings.push({ severity: "error", rule: "存在空标题", excerpt: `${emptyHeadings} 处只有 # 符号的标题` });
  }

  const emptyBlocks = lines.filter((line) => /^\s*>\s*$/u.test(line) || /^\s*(?:[-*+]|\d+\.)\s*$/u.test(line)).length;
  if (emptyBlocks > 0) {
    findings.push({ severity: "error", rule: "存在空引用或空列表项", excerpt: `${emptyBlocks} 处` });
  }

  const fenceCount = (body.match(/^```/gmu) || []).length;
  if (fenceCount % 2 !== 0) {
    findings.push({ severity: "error", rule: "代码围栏未闭合", excerpt: `${fenceCount} 个围栏标记` });
  }

  const markers = findUnresolvedMarkers(body);
  if (markers.length > 0) {
    findings.push({ severity: "error", rule: "正文仍有未完成标记", excerpt: markers.join("、") });
  }

  const substantive = lines.filter((line) => line.trim().length > 0);
  const listCount = substantive.filter((line) => /^\s*(?:[-*+] |\d+\. )/u.test(line)).length;
  const quoteCount = substantive.filter((line) => /^>\s?/u.test(line)).length;
  if (substantive.length >= 15 && listCount / substantive.length > 0.45) {
    findings.push({ severity: "warning", rule: "列表占比过高", excerpt: `${listCount}/${substantive.length} 行` });
  }
  if (quoteCount > 6 || (substantive.length >= 12 && quoteCount / substantive.length > 0.25)) {
    findings.push({ severity: "warning", rule: "引用使用过多", excerpt: `${quoteCount} 行引用` });
  }

  const longParagraph = body
    .split(/\r?\n\s*\r?\n/u)
    .map((paragraph) => paragraph.replace(/\s+/gu, "").length)
    .find((length) => length > 700);
  if (longParagraph) {
    findings.push({ severity: "warning", rule: "存在过长纯文本段落", excerpt: `约 ${longParagraph} 字` });
  }

  const suspiciousTable = lines.find((line, index) => {
    if (!line.includes("|") || index + 1 >= lines.length) return false;
    const next = lines[index + 1] || "";
    return next.includes("|") && /^\s*\|?\s*:?-{3,}/u.test(next) && countPipes(line) !== countPipes(next);
  });
  if (suspiciousTable) {
    findings.push({ severity: "warning", rule: "表格列数可能不一致", excerpt: suspiciousTable.trim().slice(0, 80) });
  }
  return findings;
}

function stripFrontmatter(markdown: string) {
  return markdown.replace(/^---\s*\r?\n[\s\S]*?\r?\n---\s*(?:\r?\n|$)/u, "");
}

function countPipes(value: string) {
  return (value.match(/\|/gu) || []).length;
}
