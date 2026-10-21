import { describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => ({
  App: class {},
  TFile: class {},
  normalizePath: (value: string) => value.replace(/\\/gu, "/")
}));

import { inferArticleTypeFromMarkdown, sanitizeFileName } from "../src/core/articleTypes";
import { TFile } from "obsidian";
import { DEFAULT_SETTINGS } from "../src/core/types";
import {
  createArticleFromTemplate,
  createNamedArticleFromTemplate,
  renderArticleTemplate
} from "../src/core/templates";

describe("article templates", () => {
  it("infers article types from frontmatter", () => {
    expect(inferArticleTypeFromMarkdown("---\nsection: app-note\n---\n# A")).toBe("app-note");
    expect(inferArticleTypeFromMarkdown("---\nsection: software\n---\n# A")).toBe("software");
    expect(inferArticleTypeFromMarkdown("# 普通文章")).toBe("blog");
  });

  it("fills template metadata and heading", () => {
    const template = "---\ntitle: 旧标题\nsummary: 旧摘要\nsoftware_id: \"{{software_id}}\"\ncreated: YYYY-MM-DD\nupdated: YYYY-MM-DD\n---\n\n# 旧标题\n";
    const result = renderArticleTemplate(template, {
      title: "Bandizip 使用判断",
      fileName: "bandizip",
      summary: "记录压缩软件的适用场景。",
      softwareId: "bandizip",
      date: "2026-07-25"
    });

    expect(result).toContain('title: "Bandizip 使用判断"');
    expect(result).toContain('summary: "记录压缩软件的适用场景。"');
    expect(result).toContain('software_id: "bandizip"');
    expect(result).toContain("# Bandizip 使用判断");
    expect(result).toContain("created: 2026-07-25");
    expect(result).not.toContain("YYYY-MM-DD");
  });

  it("sanitizes invalid Windows filename characters", () => {
    expect(sanitizeFileName('A:B/C*D?')).toBe("A-B-C-D-");
  });

  it("creates a non-conflicting file from the configured template", async () => {
    const templateFile = Object.assign(new TFile(), { path: "templates/blog-note-template.md" });
    const existingFile = Object.assign(new TFile(), { path: "notes/blog/文章.md" });
    const files = new Map<string, unknown>([
      [templateFile.path, templateFile],
      [existingFile.path, existingFile],
      ["notes", { path: "notes" }],
      ["notes/blog", { path: "notes/blog" }]
    ]);
    let createdPath = "";
    let createdContent = "";
    const app = {
      vault: {
        getAbstractFileByPath: (path: string) => files.get(path) || null,
        cachedRead: async () => "---\ntitle: 博客标题\nsummary: 摘要\ncreated: YYYY-MM-DD\nupdated: YYYY-MM-DD\n---\n\n# 博客标题\n",
        createFolder: async (path: string) => files.set(path, { path }),
        create: async (path: string, content: string) => {
          createdPath = path;
          createdContent = content;
          return Object.assign(new TFile(), { path });
        }
      }
    };

    await createArticleFromTemplate(app as never, structuredClone(DEFAULT_SETTINGS), "blog", {
      title: "文章",
      fileName: "文章",
      summary: "摘要",
      softwareId: ""
    });

    expect(createdPath).toBe("notes/blog/文章-2.md");
    expect(createdContent).toContain("# 文章");
  });

  it("creates a named template file in the active note folder", async () => {
    const templateFile = Object.assign(new TFile(), { path: "templates/app-note-template.md" });
    const files = new Map<string, unknown>([
      [templateFile.path, templateFile],
      ["projects", { path: "projects" }],
      ["projects/current", { path: "projects/current" }]
    ]);
    let createdPath = "";
    const app = {
      vault: {
        getAbstractFileByPath: (path: string) => files.get(path) || null,
        cachedRead: async () => "---\ntitle: 应用名称\nsummary: 摘要\nsection: app-note\ncreated: YYYY-MM-DD\nupdated: YYYY-MM-DD\n---\n\n# 应用名称\n",
        createFolder: async (path: string) => files.set(path, { path }),
        create: async (path: string) => {
          createdPath = path;
          return Object.assign(new TFile(), { path });
        }
      }
    };

    await createNamedArticleFromTemplate(
      app as never,
      structuredClone(DEFAULT_SETTINGS),
      "app-note",
      "Raycast 自动化",
      "projects/current"
    );

    expect(createdPath).toBe("projects/current/Raycast 自动化.md");
  });
});
