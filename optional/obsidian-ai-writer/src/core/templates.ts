import { App, TFile, normalizePath } from "obsidian";
import type { AiWriterSettings, ArticleTypeId } from "./types";
import { normalizeVaultPath, sanitizeFileName } from "./articleTypes";

export interface NewArticleInput {
  title: string;
  fileName: string;
  summary: string;
  softwareId: string;
}

export interface RenderTemplateInput extends NewArticleInput {
  date: string;
}

export function renderArticleTemplate(template: string, input: RenderTemplateInput) {
  const softwareId = input.softwareId.trim() || sanitizeFileName(input.fileName).toLowerCase();
  let rendered = template
    .replace(/\{\{\s*software_id\s*\}\}/gu, softwareId)
    .replace(/YYYY-MM-DD/gu, input.date);

  rendered = setFrontmatterField(rendered, "title", input.title.trim());
  rendered = setFrontmatterField(rendered, "summary", input.summary.trim() || input.title.trim());
  rendered = setFrontmatterField(rendered, "created", input.date);
  rendered = setFrontmatterField(rendered, "updated", input.date);
  if (/^related_software\s*:/mu.test(rendered)) {
    rendered = setFrontmatterField(rendered, "related_software", softwareId);
  }
  if (/^software_id\s*:/mu.test(rendered)) {
    rendered = setFrontmatterField(rendered, "software_id", softwareId);
  }

  if (/^#\s+.+$/mu.test(rendered)) {
    rendered = rendered.replace(/^#\s+.+$/mu, `# ${input.title.trim()}`);
  } else {
    rendered = `${rendered.trimEnd()}\n\n# ${input.title.trim()}\n`;
  }
  return `${rendered.trimEnd()}\n`;
}

export async function createArticleFromTemplate(
  app: App,
  settings: AiWriterSettings,
  articleType: ArticleTypeId,
  input: NewArticleInput
) {
  const config = settings.articleTypes[articleType];
  return await createArticleFromTemplateInFolder(app, settings, articleType, input, config.outputFolder);
}

export async function createArticleFromTemplateInFolder(
  app: App,
  settings: AiWriterSettings,
  articleType: ArticleTypeId,
  input: NewArticleInput,
  targetFolder: string
) {
  const config = settings.articleTypes[articleType];
  const templatePath = normalizePath(normalizeVaultPath(config.templatePath));
  if (!templatePath) throw new Error(`请先配置${config.label}模板路径。`);
  const templateFile = app.vault.getAbstractFileByPath(templatePath);
  if (!(templateFile instanceof TFile)) {
    throw new Error(`找不到${config.label}模板：${templatePath}`);
  }

  const folder = normalizePath(normalizeVaultPath(targetFolder));
  if (folder) await ensureFolder(app, folder);
  const baseName = sanitizeFileName(input.fileName || input.title);
  const targetPath = findAvailablePath(app, folder, baseName);
  const template = await app.vault.cachedRead(templateFile);
  const content = renderArticleTemplate(template, {
    ...input,
    date: formatLocalDate(new Date())
  });
  return await app.vault.create(targetPath, content);
}

export async function createNamedArticleFromTemplate(
  app: App,
  settings: AiWriterSettings,
  articleType: ArticleTypeId,
  name: string,
  targetFolder: string
) {
  const title = name.trim();
  if (!title) throw new Error("请先输入文章名称。");
  const fileName = sanitizeFileName(title);
  return await createArticleFromTemplateInFolder(app, settings, articleType, {
    title,
    fileName,
    summary: title,
    softwareId: fileName.toLowerCase()
  }, targetFolder);
}

function setFrontmatterField(markdown: string, key: string, value: string) {
  const frontmatter = markdown.match(/^---\s*\r?\n[\s\S]*?\r?\n---/u)?.[0];
  if (!frontmatter) return markdown;
  const line = `${key}: ${formatYamlScalar(value)}`;
  const pattern = new RegExp(`^${escapeRegExp(key)}\\s*:.*$`, "mu");
  if (pattern.test(frontmatter)) {
    return markdown.replace(pattern, line);
  }
  return markdown.replace(/\r?\n---/u, `\n${line}\n---`);
}

function findAvailablePath(app: App, folder: string, baseName: string) {
  let suffix = 1;
  while (true) {
    const name = suffix === 1 ? baseName : `${baseName}-${suffix}`;
    const path = normalizePath(folder ? `${folder}/${name}.md` : `${name}.md`);
    if (!app.vault.getAbstractFileByPath(path)) return path;
    suffix += 1;
  }
}

async function ensureFolder(app: App, folder: string) {
  if (!folder) return;
  const segments = folder.split("/").filter(Boolean);
  let current = "";
  for (const segment of segments) {
    current = current ? `${current}/${segment}` : segment;
    if (!app.vault.getAbstractFileByPath(current)) {
      await app.vault.createFolder(current);
    }
  }
}

function formatYamlScalar(value: string) {
  const normalized = value.replace(/\r?\n/gu, " ").trim();
  return /^\d{4}-\d{2}-\d{2}$/u.test(normalized) ? normalized : JSON.stringify(normalized);
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function formatLocalDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
