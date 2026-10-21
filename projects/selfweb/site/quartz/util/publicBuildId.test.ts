import assert from "node:assert/strict"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { test } from "node:test"
import { publicBuildId } from "./publicBuildId"

test("public cache identity includes the approved offline font manifest", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "quartz-font-build-id-"))
  const site = path.join(root, "site")
  const previous = process.cwd()
  try {
    fs.mkdirSync(path.join(site, ".quartz"), { recursive: true })
    fs.mkdirSync(path.join(root, "docs/build-assets"), { recursive: true })
    fs.writeFileSync(path.join(site, "index.md"), "synthetic public input")
    fs.writeFileSync(path.join(site, "quartz.lock.json"), "{}")
    fs.writeFileSync(path.join(site, ".quartz/vendored-source-manifest.json"), "{}")
    const fonts = path.join(root, "docs/build-assets/fonts-manifest.json")
    fs.writeFileSync(fonts, "font-manifest-one")
    process.chdir(site)
    const first = publicBuildId(site, {})
    fs.writeFileSync(fonts, "font-manifest-two")
    assert.notEqual(publicBuildId(site, {}), first)
    fs.writeFileSync(fonts, "font-manifest-one")
    assert.equal(publicBuildId(site, {}), first)
  } finally {
    process.chdir(previous)
    fs.rmSync(root, { recursive: true })
  }
})
