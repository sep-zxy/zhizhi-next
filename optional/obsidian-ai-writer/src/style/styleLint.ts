export type LintSeverity = "error" | "warning";

export interface StyleLintFinding {
  severity: LintSeverity;
  rule: string;
  excerpt: string;
}

const FORBIDDEN_PHRASES = [
  "首先",
  "其次",
  "综上所述",
  "值得注意的是",
  "不难发现",
  "让我们来看看",
  "接下来让我们",
  "说白了",
  "本质上",
  "换句话说",
  "不可否认",
  "这意味着"
];

const EMPTY_OPENINGS = ["在当今", "随着技术的不断", "随着科技的不断"];

export function lintKhazixArticle(markdown: string): StyleLintFinding[] {
  const article = extractArticleBody(markdown);
  const findings: StyleLintFinding[] = [];

  for (const phrase of FORBIDDEN_PHRASES) {
    if (article.includes(phrase)) {
      findings.push({ severity: "error", rule: `禁用套话：${phrase}`, excerpt: phrase });
    }
  }

  const opening = article.trimStart().slice(0, 120);
  for (const phrase of EMPTY_OPENINGS) {
    if (opening.includes(phrase)) {
      findings.push({ severity: "error", rule: "空泛时代背景开头", excerpt: opening });
      break;
    }
  }

  if (/比如有一次/u.test(article)) {
    findings.push({ severity: "warning", rule: "疑似虚构经历，需要核对素材", excerpt: "比如有一次" });
  }

  const headingCount = article.split("\n").filter((line) => /^#{1,6}\s/u.test(line)).length;
  if (headingCount > 8) {
    findings.push({ severity: "warning", rule: "小标题过多，正文可能过度结构化", excerpt: `${headingCount} 个标题` });
  }

  const shortParagraphs = article
    .split(/\n\s*\n/u)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0 && paragraph.length <= 8).length;
  if (shortParagraphs > 10) {
    findings.push({ severity: "warning", rule: "短句独立成段过多，可能机械模仿节奏", excerpt: `${shortParagraphs} 个短段落` });
  }

  return findings;
}

export function extractArticleBody(markdown: string): string {
  return markdown
    .replace(/^---\s*\n[\s\S]*?\n---\s*\n?/u, "")
    .replace(/```[\s\S]*?```/gu, "")
    .replace(/<!--([\s\S]*?)-->/gu, "")
    .replace(/^>.*$/gmu, "")
    .replace(/https?:\/\/\S+/gu, "");
}

