# Windows 便携验收包：装配契约

本目录负责 GitHub 托管 Runner 上的最终装配。源码开发与脚本单测可在获准云端进行，
不得把本地构建冒充 GitHub 产物。用户 Windows 仅在
`C:\Code\zhizhi-e2e-20261020` 授权范围内通过 Git 获取固定版本、校验并运行，
不安装开发依赖、不编译、不操作个人浏览器配置。新版本独立目录不覆盖旧包状态；
未通过 Windows 实机与全部必需真实 E2E 前不得宣称交付完成。

## 输入

每路交付一个无凭据目录与 `component.json`，包含 `name`、`version`、`source_commit`、`entrypoints`、`files`（相对路径到 SHA256）、`licenses`、`runtime_requirements`、`known_limits`。拒绝符号链接、绝对路径、目录穿越、源目录外引用及操作系统不匹配的原生二进制。模板或示例不得含真实 token、登录会话、个人素材。

候选工作流先用 `uv sync --frozen --no-install-project` 安装锁定的第三方依赖（保留 dev、wechat、captions），而不是提前构建依赖 `viewer/dist` 的项目 wheel。后续驱动使用 `uv run --no-sync`，源码测试显式设置 `PYTHONPATH`；真实 viewer 生产构建通过后，才装配其 dist 与受 Git 跟踪的后端源码。不得创建占位 dist、删除 wheel 的 force-include 或省略前端构建来使依赖阶段通过。

viewer 的 Vitest 包含真实 Chromium DOM fixture。GitHub 驱动先通过该应用冻结锁中的 `pnpm exec playwright install --with-deps chromium` 安装官方浏览器与系统依赖，失败立即停止；`viewer-browser-install` 是发布证据必需阶段。私有 Wiki 仍保留自己的 `npx --no-install playwright install --with-deps chromium`，不假设两份锁永远同版，不用系统浏览器或跳过浏览器测试兜底。这些隔离 fixture 不代表 Windows、真实身份或渠道 E2E 已验收。

- `backend/`：项目 Python wheel 或完整运行源码，锁文件、Python 版本要求、启动入口及资源目录；由总装配准备 Windows 第三方 wheels。`viewer/` 必须是云端已构建静态资源。
- `wiki-private/`：独立 `projects/wiki-private` 的锁定依赖、类型检查、测试与 GitHub 生产构建 `dist/`。装配参数 `--private-wiki`，固定静态入口 `/wiki/`；不使用学习 viewer 代替，不与公开 Wiki renderer 混淆。后端同源身份桥与浏览器操作必须独立验证。
- `pi/`：固定 Pi 版本的已发布运行 JS/WASM/Windows x64 native 资源、云端预编译自有 JS 扩展、启动入口。不得在 Windows 调用 npm、npx、tsx、tsup、esbuild 或自动下载。扩展加载器的运行时转译和缓存必须明确验证并关闭/隔离。保留官方许可证及锁文件。
- `wiki/`：已预编译 render-only JS 入口、固定插件和模板资源、字体、完整性清单及来源证据。Windows 允许解析新 Markdown、生成新 HTML/索引，但不编译 TS/SCSS/JS。保留投影、发布权限、固定 Git revision、最终产物与领域复核门槛。静态旧页面不能冒充动态发布验收。
- `asr/`：可独立资源包，必须列出完整本地模型、模型许可证、精确 Windows wheels、必要运行时和验证状态。不得将可选分包解释为删除真实音视频验收范围。

## 输出

```
runtime/python/             # 官方 Windows CPython 与经锁定的 Windows wheels
runtime/node/               # 官方 Windows Node
runtime/git/                # 官方 portable Git/MinGit
app/backend/
app/viewer/
app/pi/
app/wiki/
app/wiki-private/
config/                     # 无秘密默认配置；显式等待已授权身份
data/platform/
data/vault/
data/pi-interactive/
data/pi-worker/
data/publication/staging/
data/publication/outputs/
cache/
temp/
browser-profile/
reports/logs/
reports/screenshots/
reports/receipts/
provenance/                 # 来源、版本、校验、许可证、构建证据
start.cmd
stop.cmd
diagnostics.cmd
codex-diagnostic.cmd        # 默认只做去敏诊断；显式 --model-smoke 才调用模型
```

所有启动器由云端制作。所有应用管理的写入目录限定在包根目录内；Windows 本身的系统审计和安全日志不在此承诺范围。子进程环境必须显式隔离 HOME、USERPROFILE、APPDATA、LOCALAPPDATA、TEMP/TMP、Pi agent/session 路径及浏览器 profile。只绑定 `127.0.0.1`，不得建立防火墙规则、系统服务或自动启动。

## 装配与验收门槛

1. 官方来源与锁文件 hash 验证成功。无静默源替换、权限绕过或 Linux native 依赖混入。
2. Python SQLite 满足现有安全版本门槛；不修改/降低门槛。
3. Git 使用包内专用仓库，禁系统/个人配置与 hooks/template 继承；launcher 环境设置不得被服务清理后失效。
4. 最终包不含开发构建入口；Windows 不运行 pytest 或现场安装依赖。诊断只读或写包内报告。
5. 未授权身份、订阅或外部渠道保持 `waiting_auth`/`blocked`；不创建凭据、不复制个人浏览器登录态、不调用付费 fallback。
6. 实机黑盒截图可由云端预制 JS 验收器启动已有 Edge 独立 profile 生成，截图和脱敏报告写包内。
7. 真实模型、渠道、ASR、动态 Wiki 和持久化重启验收逐项记载；fixture 或静态页面成功不能代替这些门槛。
8. 阶段源码提交和最终包均须持久备份；只有临时云盘文件不算交付完成。
