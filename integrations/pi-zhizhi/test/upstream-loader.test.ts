/** A .js suffix does not guarantee that the upstream jiti path avoids transpilation. */
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createJiti } from "jiti";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { assertSubscription } from "../src/subscription-policy.ts";

test("upstream jiti tryNative false invokes a transformer even for valid ESM JavaScript", async () => {
  const directory = await mkdtemp(join(tmpdir(), "pi-upstream-jiti-"));
  let transforms = 0;
  try {
    const file = join(directory, "already-built.js");
    await writeFile(file, "export default function factory() {}\n");
    const jiti = createJiti(import.meta.url, { tryNative: false, fsCache: false, moduleCache: false,
      transform: () => { transforms++; throw new Error("TRANSFORMER_REACHED"); } });
    await assert.rejects(jiti.import(file), /TRANSFORMER_REACHED/);
    assert.ok(transforms > 0);
  } finally { await rm(directory, {recursive: true, force: true}); }
});

test("exact model and provider cannot be changed before subscription auth or dispatch", async () => {
  const keys = ["ZHIZHI_MODEL_ID", "ZHIZHI_MODEL_PROVIDER"] as const;
  const saved = keys.map(key => process.env[key]);
  process.env.ZHIZHI_MODEL_ID = "approved-exact-model";
  process.env.ZHIZHI_MODEL_PROVIDER = "openai";
  try {
    for (const model of [{id:"other-model",provider:"openai"}, {id:"approved-exact-model",provider:"zai"}]) {
      await assert.rejects(assertSubscription({model} as ExtensionContext), /MODEL_SELECTION_CHANGED/);
    }
  } finally {
    keys.forEach((key,index) => {if(saved[index] === undefined) delete process.env[key]; else process.env[key] = saved[index];});
  }
});
