import { promises } from "fs"
import path from "path"
import esbuild from "esbuild"
import { styleText } from "util"
import { sassPlugin } from "esbuild-sass-plugin"
import fs from "fs"
import { rm } from "fs/promises"
import chokidar from "chokidar"
import prettyBytes from "pretty-bytes"
import { execSync, spawnSync } from "child_process"
import http from "http"
import serveHandler from "serve-handler"
import { WebSocketServer } from "ws"
import { randomUUID } from "crypto"
import { Mutex } from "async-mutex"
import { globby } from "globby"
import {
  exitIfCancel,
  escapePath,
  gitPull,
  popContentFolder,
  stashContentFolder,
} from "./helpers.js"
import {
  handlePluginRestore,
  handlePluginCheck,
  handlePluginResolve,
} from "./plugin-git-handlers.js"
import { readPluginsJson, LOCKFILE_PATH } from "./plugin-data.js"
import {
  UPSTREAM_NAME,
  QUARTZ_SOURCE_BRANCH,
  QUARTZ_SOURCE_REPO,
  ORIGIN_NAME,
  version,
  fp,
  cacheFile,
  cwd,
} from "./constants.js"

/**
 * Resolve content directory path
 * @param contentPath path to resolve
 */
function resolveContentPath(contentPath) {
  if (path.isAbsolute(contentPath)) return path.relative(cwd, contentPath)
  return path.join(cwd, contentPath)
}

export async function handleBuild(argv) {
  if (argv.concurrency !== undefined && argv.concurrency < 1) {
    console.error("Concurrency must be at least 1")
    process.exit(1)
  }

  if (argv.serve) {
    argv.watch = true
  }

  console.log(`\n${styleText(["bgGreen", "black"], ` Quartz v${version} `)} \n`)
  const ctx = await esbuild.context({
    entryPoints: [fp],
    outfile: cacheFile,
    bundle: true,
    keepNames: true,
    minifyWhitespace: true,
    minifySyntax: true,
    platform: "node",
    format: "esm",
    jsx: "automatic",
    jsxImportSource: "preact",
    packages: "external",
    metafile: true,
    sourcemap: true,
    sourcesContent: false,
    logOverride: {
      "direct-eval": "silent",
      "equals-negative-zero": "silent",
      "duplicate-object-key": "silent",
    },
    plugins: [
      sassPlugin({
        type: "css-text",
        cssImports: true,
      }),
      sassPlugin({
        filter: /\.inline\.scss$/,
        type: "css",
        cssImports: true,
      }),
      {
        name: "inline-script-loader",
        setup(build) {
          build.onLoad({ filter: /\.inline\.(ts|js)$/ }, async (args) => {
            let text = await promises.readFile(args.path, "utf8")

            // remove default exports that we manually inserted
            text = text.replace("export default", "")
            text = text.replace("export", "")

            const sourcefile = path.relative(path.resolve("."), args.path)
            const resolveDir = path.dirname(sourcefile)
            const transpiled = await esbuild.build({
              stdin: {
                contents: text,
                loader: "ts",
                resolveDir,
                sourcefile,
              },
              write: false,
              bundle: true,
              minify: true,
              platform: "browser",
              format: "esm",
            })
            const rawMod = transpiled.outputFiles[0].text
            return {
              contents: rawMod,
              loader: "text",
            }
          })
        },
      },
    ],
  })

  const buildMutex = new Mutex()
  let lastBuildMs = 0
  let cleanupBuild = null
  const build = async (clientRefresh) => {
    const buildStart = new Date().getTime()
    lastBuildMs = buildStart
    const release = await buildMutex.acquire()
    if (lastBuildMs > buildStart) {
      release()
      return
    }

    if (cleanupBuild) {
      console.log(styleText("yellow", "Detected a source code change, doing a hard rebuild..."))
      await cleanupBuild()
    }

    const result = await ctx.rebuild().catch((err) => {
      console.error(
        `${styleText("red", "Failed to build Quartz.")} Check for syntax errors in your configuration or plugins.`,
      )
      console.log(`Reason: ${styleText("gray", err.message ?? String(err))}`)
      process.exit(1)
    })
    release()

    if (argv.bundleInfo) {
      const outputFileName = "quartz/.quartz-cache/transpiled-build.mjs"
      const meta = result.metafile.outputs[outputFileName]
      console.log(
        `Successfully transpiled ${Object.keys(meta.inputs).length} files (${prettyBytes(
          meta.bytes,
        )})`,
      )
      console.log(await esbuild.analyzeMetafile(result.metafile, { color: true }))
    }

    // bypass module cache
    // https://github.com/nodejs/modules/issues/307
    const { default: buildQuartz } = await import(`../../${cacheFile}?update=${randomUUID()}`)
    // ^ this import is relative, so base "cacheFile" path can't be used

    cleanupBuild = await buildQuartz(argv, buildMutex, clientRefresh)
    clientRefresh()
  }

  let clientRefresh = () => {}
  if (argv.serve) {
    const connections = []
    clientRefresh = () => connections.forEach((conn) => conn.send("rebuild"))

    if (argv.baseDir !== "" && !argv.baseDir.startsWith("/")) {
      argv.baseDir = "/" + argv.baseDir
    }

    await build(clientRefresh)
    const server = http.createServer(async (req, res) => {
      if (argv.baseDir && !req.url?.startsWith(argv.baseDir)) {
        console.log(
          styleText(
            "red",
            `[404] ${req.url} (warning: link outside of site, this is likely a Quartz bug)`,
          ),
        )
        res.writeHead(404)
        res.end()
        return
      }

      // strip baseDir prefix
      req.url = req.url?.slice(argv.baseDir.length)

      const serve = async () => {
        const release = await buildMutex.acquire()
        await serveHandler(req, res, {
          public: argv.output,
          directoryListing: false,
          headers: [
            {
              source: "**/*.*",
              headers: [{ key: "Content-Disposition", value: "inline" }],
            },
            {
              source: "**/*.webp",
              headers: [{ key: "Content-Type", value: "image/webp" }],
            },
            // fixes bug where avif images are displayed as text instead of images (future proof)
            {
              source: "**/*.avif",
              headers: [{ key: "Content-Type", value: "image/avif" }],
            },
          ],
        })
        const status = res.statusCode
        const statusString =
          status >= 200 && status < 300
            ? styleText("green", `[${status}]`)
            : styleText("red", `[${status}]`)
        console.log(statusString + styleText("gray", ` ${argv.baseDir}${req.url}`))
        release()
      }

      const redirect = (newFp) => {
        newFp = argv.baseDir + newFp
        res.writeHead(302, {
          Location: newFp,
        })
        console.log(
          styleText("yellow", "[302]") +
            styleText("gray", ` ${argv.baseDir}${req.url} -> ${newFp}`),
        )
        res.end()
      }

      let fp = req.url?.split("?")[0] ?? "/"

      // handle redirects
      if (fp.endsWith("/")) {
        // /trailing/
        // does /trailing/index.html exist? if so, serve it
        const indexFp = path.posix.join(fp, "index.html")
        if (fs.existsSync(path.posix.join(argv.output, indexFp))) {
          req.url = fp
          return serve()
        }

        // does /trailing.html exist? if so, redirect to /trailing
        let base = fp.slice(0, -1)
        if (path.extname(base) === "") {
          base += ".html"
        }
        if (fs.existsSync(path.posix.join(argv.output, base))) {
          return redirect(fp.slice(0, -1))
        }
      } else {
        // /regular
        // does /regular.html exist? if so, serve it
        let base = fp
        if (path.extname(base) === "") {
          base += ".html"
        }
        if (fs.existsSync(path.posix.join(argv.output, base))) {
          req.url = fp
          return serve()
        }

        // does /regular/index.html exist? if so, redirect to /regular/
        let indexFp = path.posix.join(fp, "index.html")
        if (fs.existsSync(path.posix.join(argv.output, indexFp))) {
          return redirect(fp + "/")
        }
      }

      return serve()
    })

    server.on("error", (err) => {
      if (err.code === "EADDRINUSE") {
        console.error(
          `Port ${argv.port} is already in use. Try a different port with --port <number>`,
        )
        process.exit(1)
      }
      throw err
    })
    server.listen(argv.port)
    const wss = new WebSocketServer({ port: argv.wsPort })
    wss.on("error", (err) => {
      if (err.code === "EADDRINUSE") {
        console.error(
          `WebSocket port ${argv.wsPort} is already in use. Try a different port with --wsPort <number>`,
        )
        process.exit(1)
      }
      throw err
    })
    wss.on("connection", (ws) => connections.push(ws))
    console.log(
      styleText(
        "cyan",
        `Started a Quartz server listening at http://localhost:${argv.port}${argv.baseDir}`,
      ),
    )
  } else {
    await build(clientRefresh)
    ctx.dispose()
  }

  if (argv.watch) {
    const paths = await globby([
      "**/*.ts",
      "quartz/cli/*.js",
      "quartz/static/**/*",
      "**/*.tsx",
      "**/*.scss",
      "package.json",
      "quartz.config.yaml",
    ])
    chokidar
      .watch(paths, { ignoreInitial: true })
      .on("add", () => build(clientRefresh))
      .on("change", () => build(clientRefresh))
      .on("unlink", () => build(clientRefresh))

    console.log(styleText("gray", "hint: exit with ctrl+c"))
  }
}

/**
 * Handles `npx quartz upgrade`
 * Upgrades the Quartz framework itself by pulling latest changes from upstream.
 * @param {*} argv arguments for `upgrade`
 */
export async function handleUpgrade(argv) {
  const contentFolder = resolveContentPath(argv.directory)
  console.log(`\n${styleText(["bgGreen", "black"], ` Quartz v${version} `)} \n`)
  console.log("Backing up your content")
  execSync(`git remote show upstream || git remote add upstream ${QUARTZ_SOURCE_REPO}`)
  await stashContentFolder(contentFolder)

  const lockfileBackup = LOCKFILE_PATH + ".bak"
  const hasLockfile = fs.existsSync(LOCKFILE_PATH)
  if (hasLockfile) {
    fs.copyFileSync(LOCKFILE_PATH, lockfileBackup)
  }

  console.log(
    "Pulling updates... you may need to resolve some `git` conflicts if you've made changes to components or plugins.",
  )

  let pullOk = false
  try {
    gitPull(UPSTREAM_NAME, QUARTZ_SOURCE_BRANCH)
    pullOk = true
  } catch {
    if (hasLockfile) {
      try {
        fs.copyFileSync(lockfileBackup, LOCKFILE_PATH)
        execSync(`git add ${LOCKFILE_PATH}`)
        const remaining = execSync("git diff --name-only --diff-filter=U", {
          encoding: "utf-8",
        }).trim()
        if (remaining.length === 0) {
          execSync("git commit --no-edit")
          pullOk = true
          console.log(styleText("cyan", "Resolved quartz.lock.json merge conflict automatically."))
        }
      } catch {
        // Could not auto-resolve, fall through to manual resolution
      }
    }

    if (!pullOk) {
      console.log(
        styleText("red", "An error occurred while pulling updates.") +
          "\nCheck your network connection and git credentials. If you see merge conflicts, resolve them manually and run `npx quartz sync --no-pull`.",
      )
      await popContentFolder(contentFolder)
      if (fs.existsSync(lockfileBackup)) fs.unlinkSync(lockfileBackup)
      return
    }
  }

  if (hasLockfile && fs.existsSync(lockfileBackup)) {
    fs.copyFileSync(lockfileBackup, LOCKFILE_PATH)
    fs.unlinkSync(lockfileBackup)
  }

  await popContentFolder(contentFolder)

  // Read the new version after pulling
  const newPkg = JSON.parse(fs.readFileSync("./package.json").toString())
  const newVersion = newPkg.version
  if (newVersion !== version) {
    console.log(styleText("cyan", `Upgraded Quartz: v${version} → v${newVersion}`))
  } else {
    console.log(styleText("gray", `Quartz is already up to date (v${version})`))
  }

  console.log("Ensuring dependencies are up to date")

  /*
  On Windows, if the command `npm` is really `npm.cmd', this call fails
  as it will be unable to find `npm`. This is often the case on systems
  where `npm` is installed via a package manager.

  This means `npx quartz upgrade` will not actually update dependencies
  on Windows, without a manual `npm i` from the caller.

  However, by spawning a shell, we are able to call `npm.cmd`.
  See: https://nodejs.org/api/child_process.html#spawning-bat-and-cmd-files-on-windows
  */

  const opts = { stdio: "inherit" }
  if (process.platform === "win32") {
    opts.shell = true
  }

  const res = spawnSync("npm", ["i"], opts)
  if (res.status === 0) {
    console.log(styleText("green", "Dependencies updated!"))
  } else {
    console.log(
      styleText("red", "An error occurred while installing dependencies.") +
        "\nTry running `npm install` manually to see detailed errors.",
    )
  }

  console.log("Restoring plugins from lockfile...")
  await handlePluginRestore()

  console.log("Checking plugin compatibility...")
  await handlePluginCheck()

  console.log(styleText("green", "Done!"))
}

/**
 * Handles `npx quartz restore`
 * @param {*} argv arguments for `restore`
 */
export async function handleRestore(argv) {
  const contentFolder = resolveContentPath(argv.directory)
  await popContentFolder(contentFolder)
}

/**
 * Handles `npx quartz sync`
 * @param {*} argv arguments for `sync`
 */
export async function handleSync(argv) {
  const contentFolder = resolveContentPath(argv.directory)
  console.log(`\n${styleText(["bgGreen", "black"], ` Quartz v${version} `)}\n`)
  console.log("Backing up your content")

  if (argv.commit) {
    const contentStat = await fs.promises.lstat(contentFolder)
    if (contentStat.isSymbolicLink()) {
      const linkTarg = await fs.promises.readlink(contentFolder)
      console.log(styleText("yellow", "Detected symlink, trying to dereference before committing"))

      // stash symlink file
      await stashContentFolder(contentFolder)

      // follow symlink and copy content
      await fs.promises.cp(linkTarg, contentFolder, {
        recursive: true,
        preserveTimestamps: true,
      })
    }

    const currentTimestamp = new Date().toLocaleString("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    })
    const commitMessage = argv.message ?? `Quartz sync: ${currentTimestamp}`
    spawnSync("git", ["add", "."], { stdio: "inherit" })
    spawnSync("git", ["commit", "-m", commitMessage], { stdio: "inherit" })

    if (contentStat.isSymbolicLink()) {
      // put symlink back
      await popContentFolder(contentFolder)
    }
  }

  await stashContentFolder(contentFolder)

  if (argv.pull) {
    console.log(
      "Pulling updates from your repository. You may need to resolve some `git` conflicts if you've made changes to components or plugins.",
    )
    try {
      gitPull(ORIGIN_NAME, QUARTZ_SOURCE_BRANCH)
    } catch {
      console.log(
        styleText("red", "An error occurred while pulling updates from your repository.") +
          "\nCheck your network connection and git credentials.",
      )
      await popContentFolder(contentFolder)
      return
    }
  }

  await popContentFolder(contentFolder)
  if (argv.push) {
    console.log("Pushing your changes")
    const currentBranch = execSync("git rev-parse --abbrev-ref HEAD").toString().trim()
    const res = spawnSync("git", ["push", "-uf", ORIGIN_NAME, currentBranch], {
      stdio: "inherit",
    })
    if (res.status !== 0) {
      console.log(
        styleText("red", `An error occurred while pushing to remote ${ORIGIN_NAME}.`) +
          "\nCheck that you have push access to the remote repository.",
      )
      return
    }
  }

  console.log(styleText("green", "Done!"))
}
