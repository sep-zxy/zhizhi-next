import { describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => ({
  App: class {},
  TFile: class {},
  normalizePath: (value: string) => value.replace(/\\/gu, "/")
}));

import { createPlaceholderSvg, materializeImagePlaceholders, parseImageDirectives } from "../src/core/illustrations";

describe("image placeholders", () => {
  it("parses inline image directives", () => {
    const markdown = `正文\n\n<!-- aiw-image:start\ndescription: 展示生成流程\nratio: 16:9\nprompt: 一张清晰的流程图\navoid: 水印\naiw-image:end -->\n\n后文`;
    expect(parseImageDirectives(markdown)).toEqual([expect.objectContaining({
      description: "展示生成流程",
      ratio: "16:9",
      prompt: "一张清晰的流程图",
      avoid: "水印"
    })]);
  });

  it("escapes generated text before writing SVG", () => {
    const svg = createPlaceholderSvg({
      id: "image-01",
      description: "A < B & C",
      prompt: "test",
      ratio: "4:3",
      avoid: "none",
      assetPath: "assets/a.svg"
    }, 1);
    expect(svg).toContain("A &lt; B &amp; C");
    expect(svg).not.toContain("A < B & C");
  });

  it("writes local SVG assets and replaces directives inline", async () => {
    const entries = new Map<string, unknown>();
    const created: Array<{ path: string; content: string }> = [];
    const app = {
      vault: {
        getAbstractFileByPath: (path: string) => entries.get(path) || null,
        createFolder: async (path: string) => entries.set(path, { path }),
        create: async (path: string, content: string) => {
          created.push({ path, content });
          entries.set(path, { path });
        },
        modify: async () => undefined
      }
    };
    const markdown = `# 标题\n\n前文。\n\n<!-- aiw-image:start\ndescription: 展示流程\nratio: 16:9\nprompt: 流程图\navoid: 水印\naiw-image:end -->\n\n后文。`;
    const result = await materializeImagePlaceholders(app as never, "notes/blog/demo.md", markdown);

    expect(result.markdown).toContain("![[assets/ai-writer/demo-");
    expect(result.markdown).toContain("*插图说明：展示流程*");
    expect(result.placeholders).toHaveLength(1);
    expect(created[0]?.path).toMatch(/placeholder-01\.svg$/u);
    expect(created[0]?.content).toContain("插图占位 01");
  });
});
