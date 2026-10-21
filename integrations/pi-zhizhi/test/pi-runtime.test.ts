/** Real pinned Pi CLI, isolated empty auth directory and no model prompts/network. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { assertSubscription } from "../src/subscription-policy.ts";

test("installed Pi 1.3.0 accepts hardened RPC flags and audited extension without model calls", async () => {
  const cli = join(dirname(fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"))), "cli.js");
  const directory = await mkdtemp(join(tmpdir(), "zhizhi-pi-smoke-"));
  const processChild = spawn(process.execPath, [cli, "--mode", "rpc", "--no-session", "--offline",
    "--no-approve", "--no-tools", "--no-mcp", "--no-extensions", "--no-skills",
    "--no-prompt-templates", "--no-context-files", "--no-themes",
    "--extension", resolve("src/model-guard.ts")], {
    cwd: directory,
    env: { PATH: process.env.PATH, HOME: directory, PI_CODING_AGENT_DIR: join(directory, "agent"),
      PI_OFFLINE: "1", PI_SKIP_VERSION_CHECK: "1", ZHIZHI_MODEL_PROVIDER: "openai",
      ZHIZHI_MAX_OUTPUT_TOKENS: "128" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  let stderr = "";
  processChild.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  const timeout = setTimeout(() => processChild.kill("SIGKILL"), 20000);
  try {
    const response = new Promise<Record<string, unknown>>((resolveResponse, reject) => {
      let buffer = "";
      processChild.stdout.on("data", (chunk) => {
        buffer += chunk.toString();
        let index: number;
        while ((index = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
          if (!line) continue;
          const record = JSON.parse(line) as Record<string, unknown>;
          if (record.id === "isolated-smoke") resolveResponse(record);
        }
      });
      processChild.on("error", reject);
      processChild.on("exit", (code) => reject(new Error(`Pi exited ${code}: ${stderr}`)));
    });
    processChild.stdin.write(JSON.stringify({ type: "get_state", id: "isolated-smoke" }) + "\n");
    assert.equal((await response).success, true);
    assert.ok(!/failed to load|extension error|unknown option/i.test(stderr), stderr);
  } finally {
    clearTimeout(timeout);
    processChild.stdin.end();
    if (processChild.exitCode === null) {
      processChild.kill("SIGTERM");
      await new Promise<void>((resolveExit) => processChild.once("exit", () => resolveExit()));
    }
    await rm(directory, { recursive: true, force: true });
  }
});

test("pinned Node CLI auth preflight rejects an empty official auth store without model calls", async () => {
  const directory = await mkdtemp(join(tmpdir(), "zhizhi-pi-auth-check-"));
  const cli = join(dirname(fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"))), "cli.js");
  const updates = {
    HOME: directory, PI_CODING_AGENT_DIR: join(directory, "agent"),
    ZHIZHI_ALLOWED_PROVIDERS: "openai", ZHIZHI_PI_CLI_JS: cli,
    PI_OFFLINE: "1", PI_SKIP_VERSION_CHECK: "1", OPENAI_API_KEY: undefined,
  };
  const saved = new Map(Object.keys(updates).map((key) => [key, process.env[key]]));
  for (const [key, value] of Object.entries(updates)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
  try {
    const ctx = {
      model: { provider: "openai", api: "openai-responses" },
      modelRegistry: { getProviderAuthStatus: () => ({ source: "stored" }) },
    } as unknown as ExtensionContext;
    await assert.rejects(assertSubscription(ctx), /SUBSCRIPTION_AUTH_REQUIRED|METERED_AUTH_FORBIDDEN/);
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await rm(directory, { recursive: true, force: true });
  }
});
