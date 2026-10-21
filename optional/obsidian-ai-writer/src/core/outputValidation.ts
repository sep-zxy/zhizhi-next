import type { WorkflowStage } from "./types";

const IMAGE_DIRECTIVE_PATTERN = /<!--\s*aiw-image:start\s*\r?\n([\s\S]*?)\r?\naiw-image:end\s*-->/giu;

const UNRESOLVED_PATTERNS: Array<{ label: string; pattern: RegExp }> = [
  { label: "待补充", pattern: /(?:\[|【)?\s*待补充(?:\s*[:：][^\]】\n]*)?(?:\]|】)?/iu },
  { label: "TODO", pattern: /\bTODO\b/iu },
  { label: "TBD", pattern: /\bTBD\b/iu },
  { label: "XXX", pattern: /\bXXX\b/iu },
  { label: "placeholder", pattern: /\bplaceholder\b/iu },
  { label: "空标题", pattern: /^\s*#{1,6}\s*$/mu },
  { label: "空引用", pattern: /^\s*>\s*$/mu },
  { label: "空列表项", pattern: /^\s*(?:[-*+]|\d+\.)\s*$/mu },
  { label: "孤立强调标记", pattern: /^\s*(?:\*{2,3}|_{1,3})\s*$/mu }
];

export function findUnresolvedMarkers(markdown: string) {
  const prose = stripCodeFences(stripGeneratedImageLines(markdown));
  return UNRESOLVED_PATTERNS
    .filter((item) => item.pattern.test(prose))
    .map((item) => item.label);
}

export function findStageOutputIssues(stage: WorkflowStage, markdown: string) {
  const issues = findUnresolvedMarkers(markdown);
  if (stage !== "images") return issues;

  const directives = parseImageDirectiveFields(markdown);
  if (directives.length === 0) return [...issues, "缺少插图标记"];
  if (directives.some((item) => !containsChinese(item.description))) issues.push("非中文插图说明");
  if (directives.some((item) => !containsChinese(item.prompt))) issues.push("非中文生图提示词");
  if (directives.some((item) => !containsChinese(item.avoid))) issues.push("非中文避免内容");
  return [...new Set(issues)];
}

export function removeMeaninglessMarkdownMarkers(markdown: string) {
  const lines = markdown.split(/\r?\n/u);
  let inFence = false;
  const cleaned = lines.filter((line) => {
    if (/^\s*(?:```|~~~)/u.test(line)) {
      inFence = !inFence;
      return true;
    }
    if (inFence) return true;
    return !isMeaninglessMarker(line);
  });
  return cleaned.join("\n").replace(/\n{3,}/gu, "\n\n").trim();
}

export function hasUnresolvedMarkers(markdown: string) {
  return findUnresolvedMarkers(markdown).length > 0;
}

export function compileRepairPrompt(stage: WorkflowStage, output: string, markers: string[]) {
  return `
下面的${stageName(stage)}仍然包含未完成标记：${markers.join("、")}。

请重写为可以直接交付的完整结果，并严格遵守：
- 可以补充稳定的公共知识、背景解释和通用方法。
- 不得编造用户的个人经历、私有测试结果、数据、人物原话或来源。
- 缺少个人事实时，删除相关叙述、改成不依赖个人经历的表达，或收束论点；不要留下任何占位符。
- 不得输出「待补充」、TODO、TBD、XXX、placeholder 或类似标记。
- 每一个标题、引用和列表项都必须有实际文本；删除只有 #、##、>、-、* 等符号的空行，不要为了保留标记而虚构标题。
- 如果是插图阶段，必须保留文章和插图标记，并将每个 description、prompt、avoid 字段改写成自然、具体的简体中文单行文本；英文专有名词可以保留，但不得使用整句或整段英文提示词。
- 保持当前阶段要求的 Markdown 结构，只输出修复后的完整内容，不解释修改过程。

需要修复的内容：
${output}
`.trim();
}

function containsChinese(value: string) {
  return /[\u3400-\u9fff]/u.test(value);
}

function parseImageDirectiveFields(markdown: string) {
  return Array.from(markdown.matchAll(IMAGE_DIRECTIVE_PATTERN)).map((match) => {
    const fields = new Map<string, string>();
    for (const line of (match[1] || "").split(/\r?\n/u)) {
      const field = line.match(/^\s*([a-zA-Z]+)\s*:\s*(.*?)\s*$/u);
      if (field?.[1]) fields.set(field[1].toLowerCase(), field[2] || "");
    }
    return {
      description: fields.get("description")?.trim() || "",
      prompt: fields.get("prompt")?.trim() || "",
      avoid: fields.get("avoid")?.trim() || ""
    };
  });
}

function stripGeneratedImageLines(markdown: string) {
  return markdown
    .replace(/^!\[\[assets\/ai-writer\/[^\]]+\]\].*$/gmu, "")
    .replace(/^<!--\s*aiw-image:[\s\S]*?-->$/gmu, "");
}

function stripCodeFences(markdown: string) {
  return markdown.replace(/^\s*(?:```|~~~)[\s\S]*?^\s*(?:```|~~~)\s*$/gmu, "");
}

function isMeaninglessMarker(line: string) {
  return /^\s*#{1,6}\s*$/u.test(line)
    || /^\s*>\s*$/u.test(line)
    || /^\s*(?:[-*+]|\d+\.)\s*$/u.test(line)
    || /^\s*(?:\*{2,3}|_{1,3})\s*$/u.test(line);
}

function stageName(stage: WorkflowStage) {
  if (stage === "outline") return "提纲";
  if (stage === "draft") return "初稿";
  if (stage === "review") return "审校稿";
  return "带插图文章";
}
