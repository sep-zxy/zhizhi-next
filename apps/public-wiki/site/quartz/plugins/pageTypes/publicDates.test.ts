import assert from "node:assert/strict"
import { test } from "node:test"
import { defaultProcessedContent } from "../vfile"
import { applyPublicBuildDates, applyPublicContentDates } from "./publicDates"

test("offline virtual dates derive from public evidence, not current time", () => {
  const before = process.env.QUARTZ_OFFLINE_PLUGINS
  process.env.QUARTZ_OFFLINE_PLUGINS = "1"
  try {
    const content = [
      defaultProcessedContent({
        dates: {
          created: new Date("2026-01-01T00:00:00Z"),
          modified: new Date("2026-02-01T00:00:00Z"),
          published: new Date("2026-01-01T00:00:00Z"),
        },
      }),
    ]
    const [, page] = defaultProcessedContent({})
    applyPublicBuildDates(page, content)
    assert.equal(page.data.dates?.modified.toISOString(), "2026-02-01T00:00:00.000Z")
    assert.equal(page.data.defaultDateType, "modified")
    applyPublicBuildDates(page, [])
    assert.equal(page.data.dates?.modified.toISOString(), "1970-01-01T00:00:00.000Z")
  } finally {
    process.env.QUARTZ_OFFLINE_PLUGINS = before
  }
})

test("public source dates preserve epoch and updated, never build clock fallback", () => {
  const before = process.env.QUARTZ_OFFLINE_PLUGINS
  process.env.QUARTZ_OFFLINE_PLUGINS = "1"
  try {
    const [, file] = defaultProcessedContent({
      frontmatter: {
        title: "fixture",
        tags: [],
        created: "1970-01-01T00:00:00Z",
        modified: "1970-01-01T00:00:00Z",
      },
    })
    applyPublicContentDates(file)
    assert.equal(file.data.dates?.modified.getTime(), 0)
    file.data.frontmatter!.updated = "2026-10-20"
    applyPublicContentDates(file)
    assert.equal(file.data.dates?.modified.toISOString(), "2026-10-20T00:00:00.000Z")
    file.data.frontmatter!.updated = "invalid"
    applyPublicContentDates(file)
    assert.equal(file.data.dates?.modified.getTime(), 0)
  } finally {
    if (before === undefined) delete process.env.QUARTZ_OFFLINE_PLUGINS
    else process.env.QUARTZ_OFFLINE_PLUGINS = before
  }
})
