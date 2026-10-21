import { describe, expect, it } from "vitest";
import { appendRevision, normalizeWriterSession, restoreRevision, restoreStageRevision, SESSION_HISTORY_LIMIT } from "../src/core/session";
import { createEmptySession } from "../src/core/types";

describe("writer session history", () => {
  it("keeps only the latest twenty revisions", () => {
    let session = createEmptySession("notes/a.md");
    for (let index = 0; index < SESSION_HISTORY_LIMIT + 5; index += 1) {
      session.brief = `版本 ${index}`;
      session = appendRevision(session, `版本 ${index}`);
    }
    expect(session.history).toHaveLength(SESSION_HISTORY_LIMIT);
    expect(session.history.at(0)?.reason).toBe("版本 5");
  });

  it("backs up the current state before restoring", () => {
    let session = createEmptySession("notes/a.md");
    session.brief = "旧版本";
    session = appendRevision(session, "旧版本");
    const revision = session.history[0]!;
    session.brief = "当前版本";
    const restored = restoreRevision(session, revision);
    expect(restored.brief).toBe("旧版本");
    expect(restored.history.at(-1)?.reason).toContain("恢复前备份");
    expect(restored.history.at(-1)?.snapshot.brief).toBe("当前版本");
  });

  it("restores the selected style and edited prompt", () => {
    let session = createEmptySession("notes/software/a.md", "software");
    session.articleStyle = "software";
    session.stylePrompt = "使用紧凑对比表。";
    session = appendRevision(session, "软件风格");
    const revision = session.history[0]!;
    session.articleStyle = "custom";
    session.stylePrompt = "自由结构";

    const restored = restoreRevision(session, revision);
    expect(restored.articleStyle).toBe("software");
    expect(restored.stylePrompt).toBe("使用紧凑对比表。");
  });

  it("restores one stage and invalidates its downstream outputs", () => {
    let session = createEmptySession("notes/a.md");
    session.outputs = {
      outline: "旧提纲",
      draft: "旧初稿",
      review: "旧审校稿",
      images: "旧插图标记",
      illustrated: "旧插图稿"
    };
    session.imagePlaceholders = [{
      id: "image-01",
      description: "旧插图",
      prompt: "旧提示词",
      ratio: "16:9",
      avoid: "水印",
      assetPath: "assets/a.svg"
    }];
    session = appendRevision(session, "旧初稿", "draft");
    const revision = session.history[0]!;
    session.outputs.draft = "新初稿";
    session.outputs.review = "新审校稿";

    const restored = restoreStageRevision(session, revision, "draft");
    expect(restored.outputs.draft).toBe("旧初稿");
    expect(restored.outputs.review).toBe("");
    expect(restored.outputs.images).toBe("");
    expect(restored.outputs.illustrated).toBe("");
    expect(restored.imagePlaceholders).toEqual([]);
    expect(restored.activeStage).toBe("draft");
    expect(restored.history.at(-1)?.reason).toContain("恢复初稿前备份");
  });

  it("restores image metadata with an illustrated revision", () => {
    let session = createEmptySession("notes/a.md");
    session.outputs.images = "插图标记";
    session.outputs.illustrated = "带图成稿";
    session.imagePlaceholders = [{
      id: "image-01",
      description: "流程图",
      prompt: "中文流程图",
      ratio: "16:9",
      avoid: "水印",
      assetPath: "assets/a.svg"
    }];
    session = appendRevision(session, "插图版本", "images");
    const restored = restoreStageRevision(createEmptySession("notes/a.md"), session.history[0]!, "images");
    expect(restored.outputs.illustrated).toBe("带图成稿");
    expect(restored.imagePlaceholders[0]?.prompt).toBe("中文流程图");
  });

  it("infers the latest available stage for sessions without navigation state", () => {
    const normalized = normalizeWriterSession("notes/a.md", {
      outputs: {
        outline: "提纲",
        draft: "初稿",
        review: "审校稿",
        images: "",
        illustrated: ""
      }
    });
    expect(normalized.activeStage).toBe("review");
  });
});
