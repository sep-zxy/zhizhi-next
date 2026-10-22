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

/** Publication dates must never fall back to filesystem mtime or build wall clock.
 * The locked upstream date plugin treats epoch as invalid, so normalize its result
 * from the audited projected metadata after Markdown transformation.
 */
export function applyPublicContentDates(file: VFile): void {
  if (process.env.QUARTZ_OFFLINE_PLUGINS !== "1") return
  const metadata: Record<string, unknown> = file.data.frontmatter ?? {}
  const date = (value: unknown): Date => {
    if (!(value instanceof Date) && typeof value !== "string" && typeof value !== "number")
      return new Date(0)
    const parsed = new Date(value)
    return Number.isFinite(parsed.getTime()) ? parsed : new Date(0)
  }
  const created = date(metadata.created)
  file.data.dates = {
    created,
    modified: date(metadata.updated ?? metadata.modified ?? metadata.created),
    published: created,
  }
  file.data.defaultDateType = "modified"
}
