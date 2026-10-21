import assert from "node:assert/strict"
import { test } from "node:test"
import { defaultProcessedContent } from "../vfile"
import { applyPublicBuildDates } from "./publicDates"

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
