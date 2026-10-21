/** Read-only source/lock verification for the isolated deterministic publisher. */
import fs from "node:fs"
import path from "node:path"
import { createHash } from "node:crypto"
import type { PluginSource } from "./types"

type Snapshot = {
  format_version: number
  lock_sha256: string
  plugins: Record<string, { source: string; commit: string; package_name: string }>
  files: Record<string, string[]>
  derivation?: {
    version: number
    original_manifest_sha256: string
    original_lock_sha256: string
    removed_plugins: string[]
  }
}

const hash = (file: string) => createHash("sha256").update(fs.readFileSync(file)).digest("hex")

/** An explicit runtime origin, never inferred from private local configuration. */
export function publicBuildBaseUrl(value: string): string {
  const parsed = new URL(value)
  const host = parsed.hostname.toLowerCase()
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.port ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/" ||
    !/^[a-z0-9.-]+\.[a-z]{2,}$/.test(host) ||
    host === "localhost.localdomain" ||
    host.endsWith(".local") ||
    host.endsWith(".internal")
  ) {
    throw new Error("Public build origin must be an explicit public HTTPS domain")
  }
  return host
}

export function verifyOfflinePlugins(sources: PluginSource[], root = process.cwd()): void {
  const snapshot = JSON.parse(
    fs.readFileSync(path.join(root, ".quartz/vendored-source-manifest.json"), "utf8"),
  ) as Snapshot
  if (
    snapshot.format_version !== 1 ||
    hash(path.join(root, "quartz.lock.json")) !== snapshot.lock_sha256
  ) {
    throw new Error("Offline plugin lock does not match the independently verified source snapshot")
  }
  const lock = JSON.parse(fs.readFileSync(path.join(root, "quartz.lock.json"), "utf8"))
  if (snapshot.derivation) {
    const derivation = snapshot.derivation
    const provenance = path.resolve(root, "../docs/pruning-v1")
    const originalManifest = path.join(provenance, "original-vendored-source-manifest.json")
    const originalLock = path.join(provenance, "original-quartz.lock.json")
    if (
      derivation.version !== 1 ||
      hash(originalManifest) !== derivation.original_manifest_sha256 ||
      hash(originalLock) !== derivation.original_lock_sha256
    )
      throw new Error("Pruned plugin provenance differs from the original snapshot")
    const original = JSON.parse(fs.readFileSync(originalManifest, "utf8")) as Snapshot
    const originalLocks = JSON.parse(fs.readFileSync(originalLock, "utf8"))
    const removed = new Set(derivation.removed_plugins)
    const indexPath = ".quartz/plugins/index.ts"
    const originalIndexPath = path.join(provenance, "original-plugin-index.ts.txt")
    let derivedIndex = fs.readFileSync(originalIndexPath, "utf8")
    if (!original.files[indexPath].includes(hash(originalIndexPath))) {
      throw new Error("Original plugin index differs from verified source")
    }
    for (const name of removed) {
      if (!/^[a-z0-9-]+$/.test(name)) throw new Error("Invalid pruned plugin name")
      derivedIndex = derivedIndex
        .replace(new RegExp(`^.*(?:from "\\./${name}"|plugins\\["${name}"\\]).*\\n`, "gm"), "")
        .replace(new RegExp(`^  "${name}": \\{\\n.*\\n  \\},\\n`, "gm"), "")
    }
    if (fs.readFileSync(path.join(root, indexPath), "utf8") !== derivedIndex) {
      throw new Error("Pruned plugin index differs from the deterministic removal-only derivation")
    }
    const retained = Object.keys(original.plugins)
      .filter((name) => !removed.has(name))
      .sort()
    const retainedFiles = Object.keys(original.files)
      .filter((file) => !removed.has(file.split("/")[2]))
      .sort()
    if (
      original.lock_sha256 !== derivation.original_lock_sha256 ||
      [...removed].some((name) => !(name in original.plugins)) ||
      JSON.stringify(retained) !== JSON.stringify(Object.keys(snapshot.plugins).sort()) ||
      JSON.stringify(retainedFiles) !== JSON.stringify(Object.keys(snapshot.files).sort()) ||
      retained.some(
        (name) =>
          JSON.stringify(snapshot.plugins[name]) !== JSON.stringify(original.plugins[name]) ||
          JSON.stringify(lock.plugins[name]) !== JSON.stringify(originalLocks.plugins[name]),
      ) ||
      retainedFiles.some(
        (file) =>
          file !== indexPath &&
          JSON.stringify(snapshot.files[file]) !== JSON.stringify(original.files[file]),
      )
    )
      throw new Error("Pruned plugin snapshot is not an exact subset of the original")
  }
  const names = Object.keys(snapshot.plugins).sort()
  const directories = fs
    .readdirSync(path.join(root, ".quartz/plugins"), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
  if (
    JSON.stringify(names) !== JSON.stringify(Object.keys(lock.plugins).sort()) ||
    JSON.stringify(names) !== JSON.stringify(directories)
  ) {
    throw new Error("Offline plugin names differ from the locked snapshot")
  }
  for (const [relative, hashes] of Object.entries(snapshot.files)) {
    if (!relative.startsWith(".quartz/plugins/") || relative.split("/").includes("..")) {
      throw new Error("Invalid vendored source path")
    }
    const file = path.join(root, relative)
    if (
      !fs.existsSync(file) ||
      fs.lstatSync(file).isSymbolicLink() ||
      !fs.realpathSync(file).startsWith(path.resolve(root) + path.sep) ||
      !hashes.includes(hash(file))
    ) {
      throw new Error(`Offline plugin source differs from the verified snapshot: ${relative}`)
    }
  }
  const inspect = (directory: string): void => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (["node_modules", "dist", ".git"].includes(entry.name)) continue
      const file = path.join(directory, entry.name)
      if (entry.isSymbolicLink()) throw new Error("Offline plugin source contains a symlink")
      if (entry.isDirectory()) inspect(file)
      else if (!(path.relative(root, file).split(path.sep).join("/") in snapshot.files)) {
        throw new Error(`Unverified source in offline plugin directory: ${entry.name}`)
      }
    }
  }
  inspect(path.join(root, ".quartz/plugins"))
  for (const name of names) {
    const expected = snapshot.plugins[name]
    if (
      lock.plugins[name].source !== expected.source ||
      lock.plugins[name].commit !== expected.commit
    ) {
      throw new Error(`Offline plugin version differs from snapshot: ${name}`)
    }
    const directory = path.join(root, ".quartz/plugins", name)
    const pkg = JSON.parse(fs.readFileSync(path.join(directory, "package.json"), "utf8"))
    if (pkg.name !== expected.package_name || pkg.quartz?.name !== name) {
      throw new Error(`Offline plugin package identity mismatch: ${name}`)
    }
    const entry = pkg.exports?.["."]?.import ?? pkg.main
    if (
      typeof entry !== "string" ||
      !entry.startsWith("./dist/") ||
      !fs.existsSync(path.join(directory, entry)) ||
      !fs
        .realpathSync(path.join(directory, entry))
        .startsWith(fs.realpathSync(directory) + path.sep)
    ) {
      throw new Error(`Offline plugin requires a prior build from its verified source: ${name}`)
    }
  }
  for (const source of sources) {
    if (
      typeof source !== "string" ||
      !Object.values(snapshot.plugins).some((item) => item.source === source)
    ) {
      throw new Error("Offline configuration contains an unverified plugin source")
    }
  }
}
