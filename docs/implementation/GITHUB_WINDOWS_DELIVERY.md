# GitHub 构建、Git 成品同步与最终验收门禁

本流程落实 2026-10-20 的最新决定：云端开发、GitHub 打包、只通过 Git 同步到 Windows、全部必需端到端通过后交付。

## 两个独立事实

1. `ci_candidate_built` / `candidate_git_published`：在可核查的 GitHub run，从固定 main commit 新构建，门禁通过且字节已同步。这只是供 Windows 验收的候选。
2. 最终交付：实际 Windows、真实账号/模型、真实渠道、动态 Wiki、必要 ASR 与持久化重启等全部适用门禁通过后，由协调者另行形成验收结论。工作流不会自动生成这个结论。

所有 receipt 固定 `final_delivery: false`；fixture、历史报告、CI 绿色、`waiting_auth`、`blocked` 或 `skipped` 都不能代替真实 E2E。ASR 仍标明独立组件待验证，不从范围中悄悄删除。

## 手动工作流

`Windows Git acceptance candidate` 仅在本仓库 `main` 手动运行，输入：

- `expected_source`：已审完整 main SHA，必须等于本次实际 checkout。
- `expected_delivery`：当前交付分支完整 SHA，必须在发布前仍未变化。
- `publish_candidate`：默认 false。初次建议只构建并查看真实产物大小、测试和日志；只有协调者明确批准本次 Git 写入时才设 true。
- `maximum_product_bytes`：明确的未压缩 Git 文件总量上限，默认 1,000,000,000 字节，绝对上限 2,000,000,000。实际超限失败，不自动扩大。单文件上限 95,000,000 字节，避免 GitHub 大文件限制。

工作流只有一个 Ubuntu build job（90 分钟）和依赖其成功的 publish job（30 分钟），没有无限矩阵或后台重试。build 仅 `contents: read`，publish 才是 `contents: write`，不用长期 token、新 secrets、SSH key、外部付费服务或公开部署。

### 构建内容

- Python 后端单元/隔离旅程、交付与补丁门禁；统一用 pytest 收集 TestCase 与函数式测试，避免漏测。
- Viewer 的锁定依赖、类型检查、单测和 GitHub 真实构建。
- Viewer 的 `pnpm test` 仅为 Vitest，不能代替页面旅程。生产构建之前另有必需
  `viewer-learning-browser-fixtures`：原锁 Playwright，固定 `chromium-desktop`，执行
  `zhizhi-domain.spec.ts`（至少 16 项）和 `growth-topics.spec.ts`（至少 1 项），
  零重试且禁止 `.only`。JSON 报告必须证明两文件完整收集、每项真实通过，
  skipped/flaky/expected-failure 不算通过；缺失或失败阶段不能发布。
  JSON、失败 trace 和截图保留在原有 CI evidence，均为模拟 API 的合成 UI 旅程，
  不代表 Windows、真实领域后端、模型或渠道 E2E。没有扩大旧 15 项浏览器/视口矩阵。
  最低覆盖包括继续原会话、流中断保留原话，以及本人理解经候选/diff/复核到写入状态、
  409版本冲突、丢响应GET恢复、显式重递原快照，以及跨卡迟到响应隔离。
  新增审阅旅程可继续增加，不把17当上限。
- 私有 Wiki 使用 `projects/wiki-private` 独立 package-lock：`npm ci --ignore-scripts`、typecheck、test、build，输出 `app/wiki-private` 并供 `/wiki/` 挂载。缺项目、锁、构建或入口立即失败；公开 Wiki 与学习 Viewer 保留独立。静态打包不证明身份桥已贯通。
- GitHub 安装该锁内 Playwright 的官方 Chromium，执行私有 Wiki 浏览器 fixture，失败截图与 trace 写入 CI evidence。浏览器 fixture 不冒充真实身份 API、Windows 或模型 E2E。
- Pi 按原 lock 安装/编译、移除构建器并保留 Windows x64 native 及许可证。
- Wiki 全部 31 个保留插件按各原锁构建；两个字体按既有官方 URL 和精确 SHA 恢复，OFL 必须匹配；emoji 等源码资产不删除。
- 独立合成模板捕获固定资源，运行版禁编译；两份不同内容与五类拒绝用例通过后，按原锁 SHA-512 换入 Windows native 包。该模板基线不是用户内容公开授权。
- 官方固定 Python/Node/MinGit 和 uv.lock 的 Windows wheel 闭包在 GitHub 新准备，装配后嵌入 source commit/run URL/阶段结果并重新封印。

Node 官方校验表、运行时及字体 HTTP 拒绝或哈希变化均失败停止，不换源、不移除依赖、不伪造测试。真实网络下载与 GitHub Runner 尚未执行时，只能报告本地脚本测试通过。

## 原子候选发布

发布 job 读取同一 workflow 的产物，先校验：

1. source commit、repository、run/attempt、job 类型与嵌入 provenance 一致；
2. 必须的 CI 阶段全部 passed，无重复或缺失；
3. Pi/公开 Wiki/私有 Wiki/backend 的来源 commit 一致，运行入口/许可证齐全；
4. 整体 distribution 与每文件 SHA 一致，无多余文件、链接、私有运行数据或配置身份；
5. 实际大小不超本次明确上限。

之后新增不可覆盖的 `releases/<source前12位>-<run>-<attempt>/`，以交付旧 SHA 为唯一 parent。最后使用明确 SHA 的 lease 作原子 CAS，并独立检查新提交是旧 SHA 的直接子提交，只允许 fast-forward，不允许历史重写或删除旧版本。任何构建、门禁、上传或并发检查失败，不推进 ref。结果不确定时先查远端，不盲重试。

发布后的本地清理与远端 Git 更新是不同事实：只有 push 后再次读取远端并确认新 SHA，
才记录 `candidate_git_published`。该收据在删除临时目录之前原子写入，随后补记
`cleanup.status: completed` 或 `warning`；清理故障仅记录错误类型和 errno，不泄露路径或凭据，
也不触发再次发布。临时 bare repo 单独关闭自动 maintenance/gc，避免后台维护与删除竞争。
push、租约、读回或收据写入失败仍然失败，不被清理异常覆盖；收据写入失败时日志保留
已确认的 commit，必须先核验远端再决定下一步。不得把工作流整体失败等同于 ref 没有更新，
也不得把已发布候选等同于最终交付。

同一运行时字节由 Git blob 去重，首轮完整产品会增加仓库体积，不能把旧“只发数 MB 补丁”的估算当新完整候选的大小。实际字节数在构建 receipt 中报告后再批准发布。

## Windows 操作边界

1. 在既定验收根目录下独立 Git 检出，固定协调者确认的完整 delivery SHA。
2. `verify.cmd --expected-commit <SHA>` 使用已存在验收包中的 Python，只读检查 Git SHA、current 指针、release 和嵌套 distribution。
3. 只有 `git_candidate_integrity_passed` 后，才运行该 release 的 diagnostics/start。无编译、依赖安装、源码恢复或凭据创建。
4. 默认没有身份，保持 waiting_auth。新版本不会自动复制旧配置、token、数据库或浏览器资料；需要迁移时另行确认并保持可回滚。
5. 运行前做 pristine 校验；运行后出现的合法状态不提交回交付分支。持续 immutable 检查使用应用 diagnostics。
6. 回退先停新进程，固定旧 Git commit/旧 release 运行；不在正在运行的目录 checkout，不用取消任务冒充撤销已提交知识。

交付根生成 `.gitignore`，忽略根及 `releases/*/` 下的 `data`、`temp`、`cache`、
`reports`、`browser-profile`，包括本机加密测试身份与配对运行状态。
这是防误提交措施，不是安全授权或发布检查的替代品：发布器同时精确限制暂存根文件集合，
已封印 release 之外的任何多余文件都拒绝上传。已跟踪的默认配置仍不能提交本机修改，
也不得使用 `git add -f` 把忽略的状态加入交付分支。

新版本路径锁定为 `C:\Code\zhizhi-e2e-20261020\git-delivery\releases\<id>`；
旧精确根仅作兼容回退。缺身份时，launcher 只为平台传入包内 `--private-web-root` 等非特权参数，
知识 API 必须保持 401，worker、渠道、模型和 auto-commit 不得启用。
`/health` 的 `waiting_auth` 仅在预期缺身份模式接受为壳存活；有身份模式只接受 `ready/configured`。
学习 viewer 的 `/healthz` 独立检查。公开渲染器不自动成为 `/public/` 站点，
跨应用导航与浏览器身份桥未验证前不得宣称可用。

`codex-diagnostic.cmd --codex-exe <已存在的官方CLI> --codex-home <已授权登录目录>`
只执行去敏诊断，输出固定阶段与错误类型，不创建登录态，不保存原始 stderr、prompt、路径或凭据。
只有操作者明确追加 `--model-smoke` 才执行约定的固定公开 2+2 请求；CI 和普通启动诊断不自动运行它。

### 显式的本机测试身份入口

后端契约 `LOCAL_TEST_BROWSER_AUTH.md` 的测试身份模块必须随最新源码一起封印，
交付门禁要求模块、会话边界和三个 cmd 全部存在：
`local-test-initialize.cmd`、`local-test-pair.cmd`、`local-test-start.cmd`。
前两者只传操作者明确参数，初始化必须由操作者给出 `--approve-local-test-identity`，
不能嵌入 cmd、由默认启动追加或在 GitHub 运行。

launcher 先验证 Windows/固定根/完整 distribution，再设置隔离环境并调用固定模块。
测试启动只读专属三键配置，固定测试 state，拒绝任何生产配置键和任意身份路径。
浏览器 profile 也与默认启动分离；不启动 viewer/Pi/模型/worker。状态保持
`fixture: true`、`browser_authentication: pairing_required`、`live_e2e: not_run`，
不会把健康检查视为已配对。实际页面完成配对及后续领域操作必须另行留证。

本地包装测试使用 synthetic 文件、模拟模块与模拟进程；它们没有调用 DPAPI、
生成真实凭据、启动 Windows 浏览器或构建生产产物。真实验证仍等待 GitHub 候选经 Git 同步。

## 本地证据与待完成项

本地测试仅证明打包/校验代码的拒绝路径、哈希、来源和原子更新前置条件。GitHub 下载、完整构建、首次候选 Git 发布和 Windows 运行需要真实执行并保留各自 receipt。没有通过这些步骤，不得把本文件当作完成报告。
