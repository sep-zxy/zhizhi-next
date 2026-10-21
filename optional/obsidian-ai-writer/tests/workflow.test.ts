import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => ({ requestUrl: vi.fn() }));

import { assembleContext, createContextItem } from "../src/core/context";
import { runWorkflowStage } from "../src/core/workflow";
import { DEFAULT_SETTINGS, EMPTY_OUTPUTS, type AiTransport } from "../src/core/types";

afterEach(() => {
  vi.useRealTimers();
});

describe("workflow retry semantics", () => {
  it("retries retryable failures before the first token", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const transport: AiTransport = {
      async generate(request) {
        calls += 1;
        if (calls < 3) throw new Error("network timeout");
        request.onToken("完成");
        return "完成";
      },
      async testConnection() {}
    };

    const promise = runWorkflowStage(createInput(transport));
    await vi.runAllTimersAsync();

    await expect(promise).resolves.toBe("完成");
    expect(calls).toBe(3);
  });

  it("does not retry after partial output", async () => {
    let calls = 0;
    const transport: AiTransport = {
      async generate(request) {
        calls += 1;
        request.onToken("部分内容");
        throw new Error("network failed");
      },
      async testConnection() {}
    };

    await expect(runWorkflowStage(createInput(transport))).rejects.toThrow("network failed");
    expect(calls).toBe(1);
  });

  it("retries compatible HTTP 429 responses", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const transport: AiTransport = {
      async generate(request) {
        calls += 1;
        if (calls === 1) throw new Error("API 请求失败：HTTP 429 rate limited");
        request.onToken("已恢复");
        return "已恢复";
      },
      async testConnection() {}
    };

    const promise = runWorkflowStage(createInput(transport));
    await vi.runAllTimersAsync();

    await expect(promise).resolves.toBe("已恢复");
    expect(calls).toBe(2);
  });

  it("repairs unfinished markers once before returning", async () => {
    let calls = 0;
    const transport: AiTransport = {
      async generate(request) {
        calls += 1;
        const value = calls === 1 ? "# 标题\n\n[待补充：经历]" : "# 标题\n\n用通用方法解释问题。";
        request.onToken(value);
        return value;
      },
      async testConnection() {}
    };

    await expect(runWorkflowStage(createInput(transport))).resolves.toContain("通用方法");
    expect(calls).toBe(2);
  });

  it("rejects output when the repair still contains unfinished markers", async () => {
    const transport: AiTransport = {
      async generate(request) {
        const value = "# 标题\n\nTODO";
        request.onToken(value);
        return value;
      },
      async testConnection() {}
    };

    await expect(runWorkflowStage(createInput(transport))).rejects.toThrow("自动修复后仍存在未完成标记");
  });

  it("removes empty headings locally without another API request", async () => {
    let calls = 0;
    const transport: AiTransport = {
      async generate(request) {
        calls += 1;
        const value = "# 标题\n\n## \n\n正文";
        request.onToken(value);
        return value;
      },
      async testConnection() {}
    };

    const result = await runWorkflowStage(createInput(transport));
    expect(result).toBe("# 标题\n\n正文");
    expect(calls).toBe(1);
  });

  it("repairs English image prompts before returning", async () => {
    let calls = 0;
    const transport: AiTransport = {
      async generate(request) {
        calls += 1;
        const value = calls === 1
          ? `# 标题\n\n<!-- aiw-image:start\ndescription: 流程图\nratio: 16:9\nprompt: A clear workflow diagram\navoid: watermark\naiw-image:end -->`
          : `# 标题\n\n<!-- aiw-image:start\ndescription: 清晰的流程图\nratio: 16:9\nprompt: 一张结构清晰的中文工作流程信息图\navoid: 避免水印、乱码和无关装饰\naiw-image:end -->`;
        request.onToken(value);
        return value;
      },
      async testConnection() {}
    };

    await expect(runWorkflowStage({ ...createInput(transport), stage: "images" })).resolves.toContain("中文工作流程");
    expect(calls).toBe(2);
  });
});

function createInput(transport: AiTransport) {
  return {
    stage: "outline" as const,
    brief: "测试",
    context: assembleContext([createContextItem("note", "素材", "内容")]),
    outputs: structuredClone(EMPTY_OUTPUTS),
    feedback: "",
    targetLength: 4000,
    settings: structuredClone(DEFAULT_SETTINGS),
    transport,
    signal: new AbortController().signal,
    onUpdate: vi.fn()
  };
}
