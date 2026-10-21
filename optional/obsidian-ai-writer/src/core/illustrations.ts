import { App, TFile, normalizePath } from "obsidian";
import type { ImagePlaceholder } from "./types";
import { sanitizeFileName } from "./articleTypes";

const IMAGE_DIRECTIVE = /<!--\s*aiw-image:start\s*\r?\n([\s\S]*?)\r?\naiw-image:end\s*-->/giu;

export interface ParsedImageDirective {
  description: string;
  prompt: string;
  ratio: string;
  avoid: string;
}

export function parseImageDirectives(markdown: string) {
  return Array.from(markdown.matchAll(IMAGE_DIRECTIVE)).map((match) => ({
    raw: match[0],
    ...parseDirectiveBody(match[1] || "")
  }));
}

export async function materializeImagePlaceholders(app: App, targetPath: string, markdown: string) {
  const directives = parseImageDirectives(markdown);
  if (directives.length === 0) {
    throw new Error("插图阶段没有返回可解析的占位图标记，请重新生成。");
  }

  const folder = createAssetFolder(targetPath);
  await ensureFolder(app, folder);
  const placeholders: ImagePlaceholder[] = [];
  let index = 0;
  const illustrated = markdown.replace(IMAGE_DIRECTIVE, (raw, body: string) => {
    index += 1;
    const directive = parseDirectiveBody(body);
    const id = `image-${String(index).padStart(2, "0")}`;
    const assetPath = normalizePath(`${folder}/placeholder-${String(index).padStart(2, "0")}.svg`);
    placeholders.push({ id, ...directive, assetPath });
    return `![[${assetPath}]]\n\n*插图说明：${directive.description}*`;
  });

  for (let placeholderIndex = 0; placeholderIndex < placeholders.length; placeholderIndex += 1) {
    const placeholder = placeholders[placeholderIndex];
    if (!placeholder) continue;
    const svg = createPlaceholderSvg(placeholder, placeholderIndex + 1);
    const existing = app.vault.getAbstractFileByPath(placeholder.assetPath);
    if (existing instanceof TFile) {
      await app.vault.modify(existing, svg);
    } else {
      await app.vault.create(placeholder.assetPath, svg);
    }
  }

  return { markdown: illustrated.trim(), placeholders };
}

export function createPlaceholderSvg(placeholder: ImagePlaceholder, index: number) {
  const { width, height } = ratioToSize(placeholder.ratio);
  const lines = wrapText(placeholder.description, 28).slice(0, 5);
  const lineHeight = 42;
  const startY = Math.round(height / 2 - ((lines.length - 1) * lineHeight) / 2 + 40);
  const textLines = lines.map((line, lineIndex) =>
    `<text x="${width / 2}" y="${startY + lineIndex * lineHeight}" text-anchor="middle" class="description">${escapeXml(line)}</text>`
  ).join("\n  ");

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <defs>
    <linearGradient id="background" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#eef2ff"/>
      <stop offset="1" stop-color="#f8fafc"/>
    </linearGradient>
  </defs>
  <rect width="${width}" height="${height}" rx="32" fill="url(#background)"/>
  <rect x="24" y="24" width="${width - 48}" height="${height - 48}" rx="24" fill="none" stroke="#94a3b8" stroke-width="3" stroke-dasharray="12 10"/>
  <style>
    .kicker { font: 700 28px 'Segoe UI', sans-serif; fill: #4f46e5; letter-spacing: 2px; }
    .description { font: 600 32px 'Microsoft YaHei', 'Segoe UI', sans-serif; fill: #1e293b; }
    .meta { font: 22px 'Segoe UI', sans-serif; fill: #64748b; }
  </style>
  <text x="${width / 2}" y="92" text-anchor="middle" class="kicker">插图占位 ${String(index).padStart(2, "0")}</text>
  ${textLines}
  <text x="${width / 2}" y="${height - 62}" text-anchor="middle" class="meta">建议比例 ${escapeXml(placeholder.ratio || "16:9")} · 可在生成真实图片后直接替换此文件</text>
</svg>
`;
}

function parseDirectiveBody(body: string): ParsedImageDirective {
  const fields = new Map<string, string>();
  for (const line of body.split(/\r?\n/u)) {
    const match = line.match(/^\s*([a-zA-Z]+)\s*:\s*(.*?)\s*$/u);
    if (match?.[1]) fields.set(match[1].toLowerCase(), match[2] || "");
  }
  const description = fields.get("description")?.trim() || "用于解释当前段落的关键内容";
  return {
    description,
    prompt: fields.get("prompt")?.trim() || description,
    ratio: fields.get("ratio")?.trim() || "16:9",
    avoid: fields.get("avoid")?.trim() || "文字水印、无关装饰、错误界面"
  };
}

function createAssetFolder(targetPath: string) {
  const stem = targetPath.split("/").pop()?.replace(/\.md$/iu, "") || "article";
  return normalizePath(`assets/ai-writer/${sanitizeFileName(stem)}-${hashPath(targetPath)}`);
}

async function ensureFolder(app: App, folder: string) {
  const segments = folder.split("/").filter(Boolean);
  let current = "";
  for (const segment of segments) {
    current = current ? `${current}/${segment}` : segment;
    if (!app.vault.getAbstractFileByPath(current)) await app.vault.createFolder(current);
  }
}

function ratioToSize(ratio: string) {
  const match = ratio.match(/(\d+(?:\.\d+)?)\s*[:：]\s*(\d+(?:\.\d+)?)/u);
  const first = Number.parseFloat(match?.[1] || "16");
  const second = Number.parseFloat(match?.[2] || "9");
  const width = 1200;
  const height = Math.round(width * second / first);
  return { width, height: Math.min(1200, Math.max(600, height)) };
}

function wrapText(value: string, width: number) {
  const chars = Array.from(value.trim());
  const lines: string[] = [];
  for (let index = 0; index < chars.length; index += width) {
    lines.push(chars.slice(index, index + width).join(""));
  }
  return lines.length > 0 ? lines : ["插图说明"];
}

function escapeXml(value: string) {
  return value.replace(/[<>&"']/gu, (character) => ({
    "<": "&lt;",
    ">": "&gt;",
    "&": "&amp;",
    "\"": "&quot;",
    "'": "&apos;"
  })[character] || character);
}

function hashPath(value: string) {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}
