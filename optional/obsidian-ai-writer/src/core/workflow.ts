import type {
  AiTransport,
  AiWriterSettings,
  ArticleStyleId,
  ContextSnapshot,
  WorkflowOutputs,
  WorkflowStage
} from "./types";
import { compileStagePrompt } from "./prompts";
import { isRetryableError } from "./transport";
import {
  compileRepairPrompt,
  findStageOutputIssues,
  removeMeaninglessMarkdownMarkers
} from "./outputValidation";
import type { ArticleTypeConfig } from "./types";

export interface RunStageInput {
  stage: WorkflowStage;
  brief: string;
  articleStyle?: ArticleStyleId;
  stylePrompt?: string;
  context: ContextSnapshot;
  outputs: WorkflowOutputs;
  feedback: string;
  targetLength: number;
  articleType?: ArticleTypeConfig;
  articleTemplate?: string;
  settings: AiWriterSettings;
  transport: AiTransport;
  signal: AbortSignal;
  onUpdate: (value: string) => void;
  onRetry?: (attempt: number) => void;
  onRepair?: (markers: string[]) => void;
}

export async function runWorkflowStage(input: RunStageInput) {
  const compiled = compileStagePrompt(input);
  const maximumAttempts = 3;

  for (let attempt = 1; attempt <= maximumAttempts; attempt += 1) {
    let partial = "";

    try {
      const result = await input.transport.generate({
        ...compiled,
        signal: input.signal,
        onToken: (token) => {
          partial += token;
          input.onUpdate(partial);
        }
      });
      const cleanedResult = removeMeaninglessMarkdownMarkers(result);
      const markers = findStageOutputIssues(input.stage, cleanedResult);
      if (markers.length === 0) return cleanedResult;

      input.onRepair?.(markers);
      let repaired = "";
      const repairResult = await input.transport.generate({
        system: compiled.system,
        prompt: compileRepairPrompt(input.stage, cleanedResult, markers),
        signal: input.signal,
        onToken: (token) => {
          repaired += token;
          input.onUpdate(repaired);
        }
      });
      const cleanedRepairResult = removeMeaninglessMarkdownMarkers(repairResult);
      const remaining = findStageOutputIssues(input.stage, cleanedRepairResult);
      if (remaining.length > 0) {
        throw new Error(`自动修复后仍存在未完成标记：${remaining.join("、")}`);
      }
      return cleanedRepairResult;
    } catch (error) {
      const canRetry = partial.length === 0 && attempt < maximumAttempts && isRetryableError(error);
      if (!canRetry || input.signal.aborted) {
        throw error;
      }

      input.onRetry?.(attempt + 1);
      await waitForRetry(attempt, input.signal);
    }
  }

  throw new Error("生成失败。\n");
}

function waitForRetry(attempt: number, signal: AbortSignal) {
  const delay = 500 * 2 ** (attempt - 1);
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      reject(new DOMException("请求已取消。", "AbortError"));
    };
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delay);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
