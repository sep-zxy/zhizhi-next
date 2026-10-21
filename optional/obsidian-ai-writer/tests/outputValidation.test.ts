import { describe, expect, it } from "vitest";
import {
  compileRepairPrompt,
  findStageOutputIssues,
  findUnresolvedMarkers,
  removeMeaninglessMarkdownMarkers
} from "../src/core/outputValidation";

describe("output validation", () => {
  it("finds unfinished content markers", () => {
    expect(findUnresolvedMarkers("# 标题\n\n[待补充：真实经历]\n\nTODO")).toEqual(["待补充", "TODO"]);
  });

  it("does not treat generated image asset names as unfinished prose", () => {
    const markdown = "![[assets/ai-writer/demo/placeholder-01.svg]]\n\n*插图说明：流程图*";
    expect(findUnresolvedMarkers(markdown)).toEqual([]);
  });

  it("builds a repair prompt that removes rather than invents personal facts", () => {
    const prompt = compileRepairPrompt("review", "[待补充：测试结果]", ["待补充"]);
    expect(prompt).toContain("不得编造用户的个人经历");
    expect(prompt).toContain("删除相关叙述");
  });

  it("removes empty Markdown markers outside code fences", () => {
    const markdown = "# 标题\n\n## \n\n正文\n\n```md\n## \n```\n\n> \n";
    const cleaned = removeMeaninglessMarkdownMarkers(markdown);
    expect(findUnresolvedMarkers(cleaned)).not.toContain("空标题");
    expect(findUnresolvedMarkers(cleaned)).not.toContain("空引用");
    expect(cleaned).toContain("```md\n## \n```");
  });

  it("reports empty headings in manually edited Markdown", () => {
    expect(findUnresolvedMarkers("# 标题\n\n## \n\n正文")).toContain("空标题");
  });

  it("rejects English image prompt fields", () => {
    const markdown = `# 标题\n\n<!-- aiw-image:start\ndescription: 清晰流程图\nratio: 16:9\nprompt: A clean workflow diagram\navoid: watermark and text\naiw-image:end -->`;
    expect(findStageOutputIssues("images", markdown)).toEqual([
      "非中文生图提示词",
      "非中文避免内容"
    ]);
  });

  it("accepts Chinese image prompt fields with English product names", () => {
    const markdown = `# 标题\n\n<!-- aiw-image:start\ndescription: 展示 Obsidian 写作流程\nratio: 16:9\nprompt: 一张 Obsidian 写作流程信息图，简洁界面，清晰箭头\navoid: 避免水印、乱码和无关装饰\naiw-image:end -->`;
    expect(findStageOutputIssues("images", markdown)).toEqual([]);
  });
});
