import assert from "node:assert/strict"
import { test } from "node:test"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { createHash } from "node:crypto"
import { publicBuildBaseUrl, verifyOfflinePlugins } from "./offline-plugins"

test("public origin override preserves original config and rejects private or credential origins", () => {
  assert.equal(publicBuildBaseUrl("https://example.org"), "example.org")
  for (const origin of [
    "http://example.org",
    "https://localhost",
    "https://127.0.0.1",
    "https://host.internal",
    "https://user:secret@example.org",
    "https://example.org/?token=x",
  ]) {
    assert.throws(() => publicBuildBaseUrl(origin))
  }
})

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "quartz-offline-"))
  const directory = path.join(root, ".quartz/plugins/fixture")
  fs.mkdirSync(path.join(directory, "dist"), { recursive: true })
  const source = "github:quartz-community/fixture"
  const plugins = {
    fixture: { source, commit: "a".repeat(40), package_name: "@quartz-community/fixture" },
  }
  const file = ".quartz/plugins/fixture/package.json"
  fs.writeFileSync(
    path.join(root, file),
    JSON.stringify({
      name: "@quartz-community/fixture",
      quartz: { name: "fixture" },
      main: "./dist/index.js",
    }),
  )
  fs.writeFileSync(path.join(directory, "dist/index.js"), "export default {}")
  fs.writeFileSync(path.join(root, "quartz.lock.json"), JSON.stringify({ plugins }))
  const digest = (name: string) =>
    createHash("sha256")
      .update(fs.readFileSync(path.join(root, name)))
      .digest("hex")
  fs.writeFileSync(
    path.join(root, ".quartz/vendored-source-manifest.json"),
    JSON.stringify({
      format_version: 1,
      lock_sha256: digest("quartz.lock.json"),
      plugins,
      files: { [file]: [digest(file)] },
    }),
  )
  return { root, source, directory }
}

test("verified offline sources need no Git metadata, installation or network", () => {
  const { root, source } = fixture()
  try {
    verifyOfflinePlugins([source], root)
  } finally {
    fs.rmSync(root, { recursive: true })
  }
})

test("offline missing build output is rejected, never fetched or skipped", () => {
  const { root, source, directory } = fixture()
  try {
    fs.unlinkSync(path.join(directory, "dist/index.js"))
    assert.throws(() => verifyOfflinePlugins([source], root), /prior build/)
    assert.equal(fs.existsSync(path.join(directory, "dist/index.js")), false)
  } finally {
    fs.rmSync(root, { recursive: true })
  }
})

test("offline modified source and injected extra source are rejected", () => {
  const { root, source, directory } = fixture()
  try {
    fs.writeFileSync(path.join(directory, "injected.js"), "export default 'untrusted'")
    assert.throws(() => verifyOfflinePlugins([source], root), /Unverified source/)
    fs.unlinkSync(path.join(directory, "injected.js"))
    fs.appendFileSync(path.join(directory, "package.json"), " ")
    assert.throws(() => verifyOfflinePlugins([source], root), /differs from the verified snapshot/)
  } finally {
    fs.rmSync(root, { recursive: true })
  }
})

test("offline lock version and unverified configured source cannot be substituted", () => {
  const { root, source } = fixture()
  try {
    assert.throws(
      () => verifyOfflinePlugins(["github:someone/other"], root),
      /unverified plugin source/,
    )
    fs.appendFileSync(path.join(root, "quartz.lock.json"), " ")
    assert.throws(() => verifyOfflinePlugins([source], root), /lock does not match/)
  } finally {
    fs.rmSync(root, { recursive: true })
  }
})

test("pruned deployment validates exact retained closure and provenance", () => {
  const root = path.resolve(import.meta.dirname, "../../..")
  const snapshot = JSON.parse(
    fs.readFileSync(path.join(root, ".quartz/vendored-source-manifest.json"), "utf8"),
  )
  assert.equal(Object.keys(snapshot.plugins).length, 31)
  assert.deepEqual(snapshot.derivation.removed_plugins, [
    "cname",
    "explicit-publish",
    "explorer",
    "recent-notes",
  ])
  verifyOfflinePlugins(
    Object.values(snapshot.plugins).map((plugin: any) => plugin.source),
    root,
  )
})

test("pruned metadata cannot rehash retained sources or inject aggregate exports", () => {
  const originalRoot = path.resolve(import.meta.dirname, "../../..")
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "quartz-pruned-"))
  const root = path.join(temp, "site")
  fs.mkdirSync(path.join(root, ".quartz/plugins"), { recursive: true })
  fs.cpSync(path.resolve(originalRoot, "../docs/pruning-v1"), path.join(temp, "docs/pruning-v1"), {
    recursive: true,
  })
  for (const name of [
    "quartz.lock.json",
    ".quartz/vendored-source-manifest.json",
    ".quartz/plugins/index.ts",
  ]) {
    fs.copyFileSync(path.join(originalRoot, name), path.join(root, name))
  }
  try {
    const manifest = path.join(root, ".quartz/vendored-source-manifest.json")
    const snapshot = JSON.parse(fs.readFileSync(manifest, "utf8"))
    const retained = Object.keys(snapshot.files).find((name) => name.endsWith("/package.json"))!
    snapshot.files[retained] = ["0".repeat(64)]
    fs.writeFileSync(manifest, JSON.stringify(snapshot))
    assert.throws(() => verifyOfflinePlugins([], root), /exact subset/)
    fs.copyFileSync(path.join(originalRoot, ".quartz/vendored-source-manifest.json"), manifest)
    fs.appendFileSync(
      path.join(root, ".quartz/plugins/index.ts"),
      "\nexport const injected = true\n",
    )
    assert.throws(() => verifyOfflinePlugins([], root), /removal-only derivation/)
  } finally {
    fs.rmSync(temp, { recursive: true })
  }
})
