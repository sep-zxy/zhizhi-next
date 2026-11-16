# 个人 Wiki 私有工作区

独立 React 前端，不属于知枝工程学习应用。正式知识、临时来源、人工决定、后台任务各自保留边界；公开 Wiki 仍是另一个只读投影。

## 入口与装配

- 源码：`apps/wiki-private`，npm 独立锁文件。
- Vite `base=/wiki/`，产物 `dist/`，由 GitHub 构建后装配为 `app/wiki-private`。
- 页面使用 hash 路由：`/wiki/#/home`、`catalog`、`entry/{id}`、`inbox`、`intake/{id}`、`reviews`、`review/{id}`、`tasks/{id?}`、`settings`、`tools`。
- 公开阅读站与知枝入口默认显示“待连接”，不构造会 404 的占位链接。当前统一后端与知枝 viewer 是不同进程；公开 `app/wiki` 只是 render-only 工具，尚无公开站挂载。只有宿主提供经过验证的 `window.__WIKI_PRIVATE_CONFIG__` 的 `publicWikiUrl`、`engineeringUrl` 时才启用跳转，且只接受同源绝对路径，不转发当前私有查询。配置不得含凭据；CSP 下使用外置受信任配置资源，不加入任意 inline script。
- 所有领域请求走同源 `/api/knowledge/v1`，健康状态走 `/health`。使用 `credentials: same-origin`、`cache: no-store`，禁止重定向。前端不生成、读取或保存 bearer token，不推断 owner。
- **生产身份仍未接入**：默认未配置时显示 401。现已接入显式开启的本机隔离测试配对层，详见下节；不能把合成测试身份说成已连接个人资料。任一 401、会话过期或注销会隐藏已读取内容。

## 开发与测试

```sh
npm ci --ignore-scripts --registry=https://registry.npmjs.org
npm run typecheck
npm test
npm run dev
```

开发端口为 `127.0.0.1:5184`，开发代理目标由 `WIKI_DEV_API_ORIGIN` 指定，默认 `http://127.0.0.1:8766`。代理不会注入任何身份。真实配对必须使用测试服务自身的精确 `http://127.0.0.1:<绑定端口>/wiki/`，不能用开发端口代理规避后端 Origin/Host 检查。不要连接未经授权的真实数据或模型。

GitHub 构建：

```sh
npm ci --ignore-scripts
npm run typecheck
npm test
npm run build
```

`build` 检查 `GITHUB_ACTIONS=true` 和 `GITHUB_RUN_ID`，不可在本地伪造 CI 环境变量。最终生产打包由 GitHub 完成，Windows 不安装开发依赖或编译。

浏览器 fixture 测试：

```sh
npx playwright install chromium
npm run test:e2e
```

可通过 `WIKI_BROWSER_EXECUTABLE` 选择环境已有的受信任 Chromium。不得为了启动浏览器绕过环境限制。测试命名明确标注 fixture，不代表真实登录、渠道、模型或 GitHub/Windows 交付通过。

依赖版本沿用已锁定的现有项目系列：React 19.2.5、Vite 6.4.3、TypeScript 5.9.3、Vitest 3.2.7、Playwright 1.59.1，格式化工具 Prettier 3.6.2；全部来自官方 npm registry。没有新增 UI framework。DOM 测试新增官方锁定 jsdom 27.0.0、@testing-library/react 16.3.0、@testing-library/dom 10.4.1、@testing-library/user-event 14.6.1，均只在 devDependencies。云端实测 Node 24.19.0；GitHub 使用兼容的受支持 Node 22/24。

## 已实现的可用路径

- 私有首页读取真实领域条目、待确认提案、收件；分类与数量仅代表本次读取范围，不伪造全库总数。
- 知识目录、分类过滤、正式知识检索、条目正文、指定历史修订读取。正文按安全文本渲染，不执行 HTML，也不自动获取外站图片/视频。
- 收件列表、真实状态过滤、链接投递临时预览、预览正文与摘要范围、明确的保留/丢弃决定。写入使用幂等键，取消没有写请求。
- 重要确认列表、完整新旧正文和元信息、真实版本绑定 challenge、有效期、原因与二次明确提交。批准回执不等于 Writer 完成或内容公开。
- 任务列表、状态详情、事件；区分 waiting_auth、waiting_quota、blocked、failed、cancelled、succeeded 等，不把任务成功说成已发布。
- 设置只显示可验证的 health 和来源解析能力；工具与 Skill 为规划，不提供安装、启用或通用执行。

## 领域 API 契约

读取采用 `{data,task_id,trace_id}`：

- `GET /entries?limit=100&offset=N` → Entry 数组。
- `GET /entries/{id}?revision=N` → 正式条目指定版本。
- `GET /search?q=...&scope=wiki&limit=100` → `{results:[...]}`，显式处理 `stale_index`。
- `GET /intakes?limit=100&offset=N&state=...`、`GET /tasks?...` → `{items:[...],has_more:boolean}`。依赖领域提交 `a38a2e4`。
- `GET /intakes/{id}`、`GET /tasks/{id}`、`GET /tasks/{id}/events?after=N&limit=100`。
- `GET /reviews?state=needs_user&limit=100`、`GET /changes/{id}`、`GET /changes/{id}/decision-challenge`。
- `GET /intakes/capabilities` → `{capabilities:[...]}`。

领域写入始终带 `Idempotency-Key`（下节独立配对协议不使用此字段）：

- `POST /intakes`：显式选择链接或直接文本，发送 `{url,...}` 或 `{text,...}`，共同字段为 `requested_action:"preview",data_classification:"personal"`。不把正文放 URL，不发送身份或权限字段。
- `POST /intakes/{id}/decision`：`{decision:"keep"|"discard",preview_revision}`。
- `POST /changes/{id}/decisions`：`{decision,reason,expected_patch_hash,base_revision,decision_nonce}`。

任务安全投影不显示 lease、payload、原始错误或模型输出。证据与基础版本变化由后端重新校验；前端检查不能替代后端授权与门禁。

## 明确未完成

- 生产浏览器身份和个人资料接入仍未完成。本机测试配对的真实 Windows DPAPI、浏览器交互及成品 E2E 仍须验证，合成测试不能替代。
- 预览补料/上传、来源全文证据阅读、条目编辑/删除/合并、任务恢复/取消。
- 从指定条目直接导入学习路线；宿主入口待连接；配置后也仅跳转独立知枝，不声称已建立学习关系。
- 模型/渠道账号操作、OpenBao、Git 同步、备份的实时管理接口。
- 工具注册中心 P13，以及真正发布资格、构建、部署状态的完整读模型。
- 公开站分类布局保持既有实现，本模块不将 RAW、private 或内部配置复制到公开产物。

## 验证边界

见 `TEST-EVIDENCE.md`。单元测试、隔离 API 组件测试、fixture 浏览器测试和真实端到端分别记录；blocked/未执行不能视为通过。

## 真实领域契约检查（仍非真实账号 E2E）

用集成后的 growth 源码及其锁定 Python 测试依赖设置 `PYTHONPATH`，经 `uv run --no-project --offline python tests/integration/capture_domain_contract.py` 在临时 SQLite/Git 中捕获真实响应，再运行 `npm run test:domain-contract`。捕获文件写入被忽略的 `test-results/`，不进入生产包。缺少捕获文件时检查会失败，不会跳过冒充通过。

## 默认关闭的本机测试配对

依赖后端 `4e9bc32` / `8d3bb09` 及其安全加固；完整宿主约束见仓库 `archive/acceptance/implementation/LOCAL_TEST_BROWSER_AUTH.md`。用户批准范围是专用 Windows 验收目录，前端不生成 Windows 身份、不取得 bearer、不执行初始化或配对批准命令。

1. 只有用户点击“申请本机测试配对码”才 `POST /api/knowledge/v1/auth/local-test/request {}`。只展示返回的 20 位一次性码，不显示 proof。
2. 用户在已初始化成品包中使用包内配对工具批准后，显式点击“检查本机批准”，调用 `GET /session`。waiting_approval 不能显示成功，也不自动轮询或重复申请。
3. pairing_proof 与 csrf_token 只存在 `WikiApi` 的 JavaScript 私有内存字段；公开 React 快照仅状态、码和到期时间。所有领域请求带 `X-Wiki-Pairing-Proof`，写请求再带 `X-Wiki-CSRF`，始终 same-origin/no-store/禁止重定向；健康检查不带这些头。
4. 严格验证 `fixture:true`、状态、码格式、证明/CSRF 格式及 120 秒/900 秒上限。刷新新实例不能仅凭 cookie 恢复 proof，必须重新配对。浏览器 cookie 由后端以 HttpOnly 管理，前端不读取它。
5. 过期、401 或 Origin/CSRF 拒绝会清理内存并隐藏私有内容。注销即使结果不确定也清理本页内存，但明确不能确认服务端撤销。没有自动续期或重试；认证请求 15 秒超时。
6. 配对模式只有空的合成测试账本、无 worker/model/渠道。真实文本与 URL 投递在组件和 API 客户端都禁用，不把 personal 改标 public，不生成示例条目。完整写入链须另行准备固定合成种子。

## 首次投递文本

统一收件箱的“投递材料”保留链接路径，并提供“直接文本”模式。正文原样发送，保留首尾空白及输入换行；全空白拒绝，不自动 trim 正文。沿后端限制检查最多 100,000 个 Unicode 字符及完整 JSON 最多 512 KiB。现有服务会识别文本内单个 HTTP(S) 链接并按链接解析，多个链接拒绝；不承诺该模式完全禁止链接识别或抓取。

写入只请求临时预览，不自动 keep、发布或启动新 provider。结果不确定时冻结正文及幂等键，只允许显式重试同一请求。切换模式或关闭表单清除当前草稿；关闭不撤销已经送达服务的请求，旧回执不能覆盖新草稿或改变新页面。正文不写 URL、storage 或日志。权限仍由现有 Owner 会话与后端复核；未认证、过期、注销仍清理私有界面。

新增文本入口的 DOM/API、真实 ASGI 合成契约及四项浏览器 fixture 定义，均不能替代真实 Windows/账户/模型验收；浏览器实际执行仍交 GitHub，未在受限本机运行。

码/proof/CSRF/cookie/bearer 禁止进入 URL、localStorage、sessionStorage、日志、Git 或测试审计。真实配对不得开启 trace/HAR/网络响应日志，也不要保存含一次性码的截图；浏览器 fixture 套件仅使用明确的惰性常量。JavaScript 内存不能保证底层缓冲区被安全擦除，不将此模式宣称为生产安全登录。

额外协议检查：通过 `uv run --no-project --offline python tests/integration/check_local_test_auth.py` 调用真实 ASGI、临时空账本和合成身份，17 项检查覆盖申请/批准/会话、空列表、缺证明的刷新、注销。该脚本不落盘或输出任何会话资料，只打印固定汇总，不算 Windows 或浏览器 E2E。
