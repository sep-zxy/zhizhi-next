import fs from "node:fs"
import path from "node:path"
import { createHash } from "node:crypto"

/** Stable cache key from approved public input and locked template configuration. */
export function publicBuildId(directory: string, configuration: unknown): string {
  const manifest = path.join(directory, "publish-manifest.json")
  const digest = createHash("sha256")
  digest.update(
    fs.readFileSync(fs.existsSync(manifest) ? manifest : path.join(directory, "index.md")),
  )
  digest.update(fs.readFileSync("quartz.lock.json"))
  digest.update(fs.readFileSync(".quartz/vendored-source-manifest.json"))
  digest.update(fs.readFileSync("../docs/build-assets/fonts-manifest.json"))
  digest.update(JSON.stringify(configuration))
  return digest.digest("hex")
}
