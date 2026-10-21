import type { ProcessedContent } from "../vfile"
import type { VFile } from "vfile"

/** Virtual pages inherit source dates, never the wall clock of an offline build. */
export function applyPublicBuildDates(file: VFile, content: ProcessedContent[]): void {
  if (process.env.QUARTZ_OFFLINE_PLUGINS !== "1") return
  const dates = content.flatMap(([, item]) => {
    const value = item.data.dates as { created?: Date; modified?: Date } | undefined
    return value ? [value] : []
  })
  const created = dates.map((value) => value.created?.getTime()).filter(Number.isFinite) as number[]
  const modified = dates
    .map((value) => value.modified?.getTime())
    .filter(Number.isFinite) as number[]
  file.data.dates = {
    created: new Date(created.length ? Math.min(...created) : 0),
    modified: new Date(modified.length ? Math.max(...modified) : 0),
    published: new Date(created.length ? Math.min(...created) : 0),
  }
  file.data.defaultDateType = "modified"
}
