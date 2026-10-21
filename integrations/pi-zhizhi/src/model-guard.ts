/** Loaded alone by Python's background RPC host, never with ambient extensions. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { installSubscriptionPolicy } from "./subscription-policy.ts";

export function boundedPayload(payload: unknown, provider: string, maxTokens: number): unknown {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("MODEL_PAYLOAD_INVALID");
  }
  if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 32768) {
    throw new Error("MODEL_TOKEN_BOUND_INVALID");
  }
  const record = { ...payload } as Record<string, unknown>;
  if (provider === "openai") {
    record.max_output_tokens = maxTokens;
    record.store = false;
    record.stream = true;
    delete record.background;
    delete record.previous_response_id;
  } else if (provider === "zai" || provider === "zai-coding-cn") {
    record.max_tokens = maxTokens;
  } else {
    throw new Error("MODEL_PROVIDER_FORBIDDEN");
  }
  return record;
}

export default function modelGuard(pi: ExtensionAPI): void {
  installSubscriptionPolicy(pi, true);
  const provider = process.env.ZHIZHI_MODEL_PROVIDER ?? "";
  const maxTokens = Number(process.env.ZHIZHI_MAX_OUTPUT_TOKENS);
  pi.on("tool_call", () => ({ block: true, reason: "Background model tasks have no tools" }));
  pi.on("user_bash", () => ({ result: { output: "Shell is disabled", exitCode: 1,
                                       cancelled: false, truncated: false } }));
  pi.on("before_provider_request", (event) => boundedPayload(event.payload, provider, maxTokens));
  pi.on("cache_warming_decision", () => ({ action: "stop" }));
}
