/** Official non-secret auth check for the interactive extension, never token extraction. */
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { promisify } from "node:util";
import { lazyStream, type Provider } from "@earendil-works/pi-ai";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const executeFile = promisify(execFile);
const endpoints: Record<string, string> = {
  zai: "https://api.z.ai/api/coding/paas/v4",
  "zai-coding-cn": "https://open.bigmodel.cn/api/coding/paas/v4",
};

export async function assertSubscription(ctx: ExtensionContext): Promise<void> {
  const model = ctx.model;
  const exactModel = process.env.ZHIZHI_MODEL_ID;
  const exactProvider = process.env.ZHIZHI_MODEL_PROVIDER;
  if (exactModel && (model?.id !== exactModel || model?.provider !== exactProvider)) {
    throw new Error("MODEL_SELECTION_CHANGED");
  }
  const allowed = (process.env.ZHIZHI_ALLOWED_PROVIDERS ?? "").split(",").filter(Boolean);
  if (!model || !["openai", "zai", "zai-coding-cn"].includes(model.provider) || !allowed.includes(model.provider)) {
    throw new Error("NO_APPROVED_SUBSCRIPTION_ROUTE");
  }
  if (model.provider === "zai-coding-cn" && process.env.ZHIZHI_CHINA_TERMS_VERIFIED !== "true") {
    throw new Error("CHINA_SUBSCRIPTION_QUALIFICATION_REQUIRED");
  }
  const agentDir = process.env.PI_CODING_AGENT_DIR;
  if (!agentDir || existsSync(join(agentDir, "models.json"))) {
    throw new Error("DEDICATED_UNMODIFIED_PI_HOST_REQUIRED");
  }
  const source = ctx.modelRegistry.getProviderAuthStatus(model.provider).source;
  if (source !== "stored" && source !== "environment") throw new Error("CUSTOM_AUTH_OVERRIDE_FORBIDDEN");
  if (model.provider !== "openai" && model.baseUrl.replace(/\/$/, "") !== endpoints[model.provider]) {
    throw new Error("METERED_ENDPOINT_FORBIDDEN");
  }
  if (model.provider === "openai" && model.api !== "openai-responses") {
    throw new Error("UNSUPPORTED_SUBSCRIPTION_API");
  }
  // Fixed command/arguments; not a tool, not user/model-controlled shell interpolation.
  const cliJs = process.env.ZHIZHI_PI_CLI_JS;
  const executable = cliJs ? process.execPath : (process.env.ZHIZHI_PI_EXECUTABLE ?? "pi");
  const prefix = cliJs ? [cliJs] : [];
  const { stdout: version } = await executeFile(executable, [...prefix, "--version"], { timeout: 15000, maxBuffer: 16384 });
  if (version.trim() !== "1.3.0") throw new Error("PI_VERSION_MISMATCH");
  let stdout: string;
  try {
    ({ stdout } = await executeFile(executable, [...prefix, "auth", "check", "--provider", model.provider,
      "--json", "--no-refresh"], { timeout: 15000, maxBuffer: 16384 }));
  } catch {
    throw new Error("SUBSCRIPTION_AUTH_REQUIRED");
  }
  const auth = JSON.parse(stdout) as Record<string, unknown>;
  if (auth.status !== "ready" || auth.provider !== model.provider ||
      auth.authType !== (model.provider === "openai" ? "oauth" : "api_key")) {
    throw new Error("METERED_AUTH_FORBIDDEN");
  }
}

/** Enforcement lives in the provider dispatch, because Pi request event hooks fail open. */
export function guardProvider(
  original: Provider, authorize: () => Promise<void>, maxTokens = 4096,
): Provider {
  return {
    ...original,
    // Knowledge subscriptions do not authorize image/classifier/background API products.
    generateImages: undefined, classify: undefined, fetchDeferred: undefined, cancelDeferred: undefined,
    stream(model, context, options) {
      return lazyStream(model, async () => {
        await authorize();
        const bounded = { ...options,
          maxTokens: Math.min(options?.maxTokens ?? maxTokens, maxTokens) } as typeof options;
        return original.stream(model, context, bounded);
      });
    },
    streamSimple(model, context, options) {
      return lazyStream(model, async () => {
        await authorize();
        return original.streamSimple(model, context, { ...options,
          maxTokens: Math.min(options?.maxTokens ?? maxTokens, maxTokens) });
      });
    },
  };
}

export function installSubscriptionPolicy(pi: ExtensionAPI, background = false): void {
  pi.on("session_start", (_event, ctx) => {
    const allowed = background ? [process.env.ZHIZHI_MODEL_PROVIDER ?? ""]
      : (process.env.ZHIZHI_ALLOWED_PROVIDERS ?? "").split(",").filter(Boolean);
    const maxTokens = Number(process.env.ZHIZHI_MAX_OUTPUT_TOKENS ?? "4096");
    if (!Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 32768) {
      ctx.abort();
      ctx.shutdown();
      throw new Error("MODEL_TOKEN_BOUND_INVALID");
    }
    const providers = new Set(ctx.modelRegistry.getAll().map((model) => model.provider));
    for (const id of providers) {
      const original = ctx.modelRegistry.getProvider(id);
      if (!original) continue;
      const permitted = ["openai", "zai", "zai-coding-cn"].includes(id) && allowed.includes(id);
      const authorize = async () => {
        if (!permitted) throw new Error("NO_APPROVED_SUBSCRIPTION_ROUTE");
        // Python has already checked the exact official auth type, endpoint and version before
        // starting this isolated process. It never permits changing model via an external RPC API.
        if (!background) await assertSubscription(ctx);
        if (ctx.model?.provider !== id) throw new Error("MODEL_SELECTION_CHANGED");
        if (process.env.ZHIZHI_MODEL_ID && ctx.model?.id !== process.env.ZHIZHI_MODEL_ID) {
          throw new Error("MODEL_SELECTION_CHANGED");
        }
      };
      const guarded = guardProvider(original, authorize, maxTokens);
      if (!permitted) {
        guarded.getModels = () => [];
        guarded.getAllModels = () => [];
        guarded.refreshModels = undefined;
      }
      pi.registerProvider(guarded);
    }
  });
}
