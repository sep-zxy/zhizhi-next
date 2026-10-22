import fs from "node:fs"
import path from "node:path"
import { createHash } from "node:crypto"
import { slugifyFilePath, type FilePath } from "../../util/path"
import type { QuartzPluginData } from "../../plugins/vfile"

export interface PublicManifest {
  format_version: number
  pages: { slug: string; content_hash: string; updated: string }[]
}
export interface PortalEntry {
  slug: string
  title: string
  description: string
  tags: string[]
  updated: string
  links: string[]
}

/** Presentation is a projection of the audited staging, never a second knowledge store.
 * This integrity check is defence in depth, not a substitute for publication review.
 */
export function readPublicManifest(directory: string): PublicManifest {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(directory, "publish-manifest.json"), "utf8"),
  ) as PublicManifest
  if (manifest.format_version !== 2 || !Array.isArray(manifest.pages))
    throw new Error("Audited public manifest required")
  const seen = new Set<string>()
  for (const page of manifest.pages) {
    if (
      typeof page.slug !== "string" ||
      /[\\\x00:?%#]/.test(page.slug) ||
      path.isAbsolute(page.slug) ||
      page.slug.split("/").some((p) => !p || p === "." || p === "..") ||
      seen.has(page.slug)
    )
      throw new Error("Invalid public slug")
    seen.add(page.slug)
    const file = path.join(directory, page.slug + ".md")
    if (
      fs.lstatSync(file).isSymbolicLink() ||
      !fs.realpathSync(file).startsWith(fs.realpathSync(directory) + path.sep) ||
      createHash("sha256").update(fs.readFileSync(file)).digest("hex") !== page.content_hash
    )
      throw new Error("Public artifact integrity mismatch")
  }
  return manifest
}

export function portalEntries(files: QuartzPluginData[], manifest: PublicManifest): PortalEntry[] {
  const allowed = new Map(
    manifest.pages.map((page) => [String(slugifyFilePath((page.slug + ".md") as FilePath)), page]),
  )
  return files
    .flatMap((file) => {
      const slug = String(file.slug ?? "")
      const page = allowed.get(slug)
      if (!page || slug === "index" || slug.endsWith("/index")) return []
      const fm: Record<string, unknown> = file.frontmatter ?? {}
      return [
        {
          slug,
          title: String(fm.title ?? "无标题"),
          description: String(fm.description ?? ""),
          tags: Array.isArray(fm.tags)
            ? fm.tags.filter((tag): tag is string => typeof tag === "string")
            : [],
          updated: page.updated,
          links: (file.links ?? []).filter((link) => allowed.has(link)),
        },
      ]
    })
    .sort((a, b) => a.slug.localeCompare(b.slug, "zh-CN"))
}
export function publicGroups(entries: PortalEntry[]): [string, PortalEntry[]][] {
  const groups = new Map<string, PortalEntry[]>()
  for (const entry of entries)
    for (const tag of entry.tags.length ? entry.tags : ["未分类"])
      groups.set(tag, [...(groups.get(tag) ?? []), entry])
  const preferred = ["架构设计", "技术栈", "核心机制", "工程实践"]
  return [...groups].sort(([a], [b]) => {
    const ai = preferred.indexOf(a),
      bi = preferred.indexOf(b)
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.localeCompare(b, "zh-CN")
  })
}
export function recentPublicEntries(entries: PortalEntry[]): PortalEntry[] {
  return entries
    .filter((entry) => Number.isFinite(Date.parse(entry.updated)) && Date.parse(entry.updated) > 0)
    .sort((a, b) => b.updated.localeCompare(a.updated) || a.slug.localeCompare(b.slug))
    .slice(0, 6)
}
