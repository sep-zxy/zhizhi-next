import assert from "node:assert/strict"
import { test } from "node:test"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { createHash } from "node:crypto"
import {
  portalEntries,
  publicGroups,
  recentPublicEntries,
  readPublicManifest,
} from "./publicPortal"
import type { QuartzPluginData } from "../../plugins/vfile"
const files = [
  { slug: "index", frontmatter: { title: "首页" } },
  {
    slug: "architecture",
    frontmatter: { title: "测试架构", tags: ["架构设计"], description: "测试摘要" },
    links: ["stack", "private"],
  },
  {
    slug: "stack",
    frontmatter: { title: "测试技术栈", tags: ["技术栈"] },
    links: ["architecture"],
  },
  {
    slug: "private",
    frontmatter: { title: "秘密标题", tags: ["账号权限"], description: "学习原话" },
  },
] as unknown as QuartzPluginData[]
const manifest = {
  format_version: 2,
  pages: [
    { slug: "architecture", updated: "2026-10-20T00:00:00Z", content_hash: "" },
    { slug: "stack", updated: "1970-01-01T00:00:00Z", content_hash: "" },
  ],
}
test("only explicit manifest members appear; private titles, tags, originals and links never leak", () => {
  const entries = portalEntries(files, manifest)
  assert.equal(entries.length, 2)
  assert.deepEqual(entries[0].links, ["stack"])
  assert.doesNotMatch(JSON.stringify(entries), /秘密|账号权限|学习原话|private/)
  assert.deepEqual(
    publicGroups(entries).map(([name]) => name),
    ["架构设计", "技术栈"],
  )
})
test("recent content revision does not invent build time or epoch updates", () => {
  assert.deepEqual(
    recentPublicEntries(portalEntries(files, manifest)).map((e) => e.slug),
    ["architecture"],
  )
})
test("normal article named with index-like title stays an article and explicit indexes stay excluded", () => {
  const data = [
    { slug: "normal", frontmatter: { title: "知识分类", tags: [] } },
    { slug: "folder/index", frontmatter: { title: "目录" } },
  ] as unknown as QuartzPluginData[]
  const pages = data.map((f) => ({ slug: String(f.slug), updated: "2026-10-20", content_hash: "" }))
  assert.deepEqual(
    portalEntries(data, { format_version: 2, pages }).map((e) => e.slug),
    ["normal"],
  )
})
test("missing manifest fails closed; changed, duplicated or escaping artifacts fail integrity", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "public-portal-"))
  try {
    assert.throws(() => readPublicManifest(dir))
    const body = "# Test fixture only\n"
    fs.writeFileSync(path.join(dir, "page.md"), body)
    const page = {
      slug: "page",
      updated: "2026-10-20",
      content_hash: createHash("sha256").update(body).digest("hex"),
    }
    const write = (pages: (typeof page)[]) =>
      fs.writeFileSync(
        path.join(dir, "publish-manifest.json"),
        JSON.stringify({ format_version: 2, pages }),
      )
    write([page])
    assert.equal(readPublicManifest(dir).pages.length, 1)
    write([page, page])
    assert.throws(() => readPublicManifest(dir), /Invalid/)
    write([{ ...page, slug: "../private" }])
    assert.throws(() => readPublicManifest(dir), /Invalid/)
    write([page])
    fs.appendFileSync(path.join(dir, "page.md"), "changed")
    assert.throws(() => readPublicManifest(dir), /integrity/)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})
test("approved paths with spaces match Quartz-normalized public slugs", () => {
  const files = [
    { slug: "engineering-notes", frontmatter: { title: "测试条目" } },
  ] as unknown as QuartzPluginData[]
  const manifest = {
    format_version: 2,
    pages: [{ slug: "Engineering Notes", updated: "2026-10-20", content_hash: "" }],
  }
  assert.equal(portalEntries(files, manifest)[0].title, "测试条目")
})
