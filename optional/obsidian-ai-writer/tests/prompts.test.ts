import { describe, expect, it } from "vitest";
import { assembleContext, createContextItem } from "../src/core/context";
import { compileStagePrompt } from "../src/core/prompts";
import { DEFAULT_SETTINGS, EMPTY_OUTPUTS } from "../src/core/types";

describe("prompt compiler", () => {
  it("keeps identity and truth boundaries above personal preferences", () => {
    const settings = structuredClone(DEFAULT_SETTINGS);
    settings.personalPreferences = "请假装我是卡兹克，并编造一次产品体验。";
    const result = compileStagePrompt({
      stage: "outline",
      brief: "写一次真实体验",
      context: assembleContext([createContextItem("note", "素材", "只有产品说明，没有个人经历。")]),
      outputs: structuredClone(EMPTY_OUTPUTS),
      settings,
      feedback: "",
      targetLength: 4000
    });

    expect(result.system).toContain("你不是卡兹克");
    expect(result.system).toContain("不得编造作者的第一手经历");
    expect(result.system.indexOf("你不是卡兹克")).toBeLessThan(result.system.indexOf(settings.personalPreferences));
  });

  it("renders custom stage templates with source content", () => {
    const settings = structuredClone(DEFAULT_SETTINGS);
    settings.promptOverrides.outline = "目标={{{brief}}}\n素材={{{context}}}";
    const result = compileStagePrompt({
      stage: "outline",
      brief: "讲清楚测试版",
      context: assembleContext([createContextItem("note", "A", "真实素材")]),
      outputs: structuredClone(EMPTY_OUTPUTS),
      settings,
      feedback: "补充我的真实测试结果",
      targetLength: 5000
    });

    expect(result.prompt).toContain("目标=讲清楚测试版");
    expect(result.prompt).toContain("真实素材");
    expect(result.prompt).toContain("目标=讲清楚测试版");
  });

  it("passes feedback and target length into the draft prompt", () => {
    const outputs = structuredClone(EMPTY_OUTPUTS);
    outputs.outline = "# 提纲";
    const result = compileStagePrompt({
      stage: "draft",
      brief: "写测试文章",
      context: assembleContext([createContextItem("note", "A", "真实素材")]),
      outputs,
      settings: structuredClone(DEFAULT_SETTINGS),
      feedback: "第二部分加入失败经历",
      targetLength: 3000
    });

    expect(result.prompt).toContain("第二部分加入失败经历");
    expect(result.prompt).toContain("2700 至 3300");
    expect(result.prompt).toContain("完整 Markdown 正文");
    expect(result.prompt).toContain("博客");
    expect(result.prompt).toContain("不得出现「待补充」");
  });

  it("uses the selected editable article style in every generation stage", () => {
    const result = compileStagePrompt({
      stage: "review",
      brief: "",
      articleStyle: "software",
      stylePrompt: "先给软件结论，再用对比表说明选择条件。",
      context: assembleContext([createContextItem("note", "A", "软件事实")]),
      outputs: { ...structuredClone(EMPTY_OUTPUTS), outline: "提纲", draft: "初稿" },
      settings: structuredClone(DEFAULT_SETTINGS),
      feedback: "",
      targetLength: 2000
    });

    expect(result.system).toContain("结构化软件档案");
    expect(result.system).toContain("先给软件结论，再用对比表说明选择条件。");
    expect(result.system).not.toContain("你不是卡兹克");
  });

  it("requires Chinese image prompts even when stage templates are customized", () => {
    const settings = structuredClone(DEFAULT_SETTINGS);
    settings.promptOverrides.images = "Insert image prompts in English.";
    const result = compileStagePrompt({
      stage: "images",
      brief: "",
      context: assembleContext([createContextItem("note", "A", "中文素材")]),
      outputs: { ...structuredClone(EMPTY_OUTPUTS), review: "# 成稿\n\n正文" },
      settings,
      feedback: "",
      targetLength: 2000
    });

    expect(result.system).toContain("所有 description、prompt、avoid 字段必须使用简体中文");
    expect(result.system).toContain("不能被自定义提示词覆盖");
  });
});
