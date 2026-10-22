# Windows 便携验收包

这是 GitHub 托管 Runner 的装配源目录，不是给用户 Windows 执行的安装工程。
最新流程由 `Windows Git acceptance candidate` 手动工作流执行。Windows 只通过
Git 获取固定交付 commit、校验并运行版本目录中的启动器。ZIP 仅为 CI 辅助产物，
不替代 Git 同步。

## GitHub 构建

入口、权限、候选发布与验收边界见 `docs/implementation/GITHUB_WINDOWS_DELIVERY.md`。
初次运行默认只构建，不推进交付分支；报告实际大小后，由协调者明确启用候选发布。
以前在隔离云电脑完成的包不属于此工作流的新产物。

Python 经已有 uv 环境执行。`build_bundle.py plan` 从项目锁文件生成 Windows CPython 3.13 x64 的纯 wheel 闭包，`fetch-wheels` 仅下载精确 hash 的官方 PyPI 文件，`prepare-runtimes` 校验官方运行时并在云端展开、交叉目标安装 wheels。遇 HTTP 拒绝或 hash 不符立即停止，不换代理或未批准镜像。

运行时固定为 CPython 3.13.16、Node 22.23.3、MinGit 2.56.0.2。原始包的来源与校验列在代码和产物 provenance 中。MinGit 2.56.0.2 修复 `GIT_CONFIG_GLOBAL=NUL` 回归，不使用存在该回归的 2.56.0。Python 原装 SQLite 静态版本为3.50.4，属于项目允许回补，仍必须实机验证，未替换原装 DLL。

模块输入见 ASSEMBLY.md。依赖、源码和原生文件清单与运行状态必须分别报告。

## 最终用户运行

1. 在 `C:\Code\zhizhi-e2e-20261020` 授权范围内，通过 Git 检出已确认的交付 commit，先运行根目录 `verify.cmd --expected-commit <SHA>`，再进入 `current.json` 指定的独立版本目录。旧根目录包只保留回退，不覆盖状态。
2. 双击 `diagnostics.cmd` 检查真实 Windows 导入、SQLite、安全路径、Node/Git。
3. 双击 `start.cmd`。首次没有已授权身份时打开 `/wiki/` 私有工作区壳，所有知识 API 保持 401；统一平台和 Pi 明确为 waiting_auth。壳 HTTP 200 只证明进程存活，不是认证、统一链路或真实 E2E 通过。学习 viewer 独立启动；跨应用导航仍需按最终宿主契约验证。
4. `stop.cmd` 只通知这个包的启动进程停止，不修改系统服务，不根据任意保存PID杀进程。

`app/wiki-private` 是独立私有工作区静态 UI，`app/viewer` 是学习专域，
`app/wiki` 是公开 Wiki 渲染运行时。三者不互相代替。私有 UI 的同源鉴权仍需实机验收，
默认 401、页面能打开或 CI 绿色都不算贯通。

`codex-diagnostic.cmd --codex-exe <已有官方CLI> --codex-home <已授权登录目录>`
默认只做脱敏诊断，不调用模型。仅显式追加 `--model-smoke` 才执行固定公开 2+2 请求；
不自动运行，不创建凭据，不将登录目录或原始错误内容写入报告。

普通启动在无账户身份时不创建测试 token，不复制任何个人登录态。后续由明确授权的部署配置接入统一平台。`config/launch.json` 可引用包内已批准身份、模型、桌面与Pi环境配置；这些文件不是默认交付内容，也不由普通启动器创建。

已批准Pi环境JSON只允许 ZHIZHI_MODEL_PROVIDER、ZHIZHI_MODEL_ID、ZHIZHI_AGENT_TOKEN、ZHIZHI_USER_TOKEN、ZHIZHI_CHINA_TERMS_VERIFIED、ZHIZHI_MAX_OUTPUT_TOKENS。token只传子进程环境，不放CLI参数或报告。真实登录和身份配置仍需单独批准。

## 单独获准的本机测试身份

这些入口只用于获准验收根内的固定 synthetic 测试 owner，不是生产登录或真实模型授权。
CI 只打包入口，不运行初始化。必须先完成 Git 成品完整性检查。

1. 明确获准创建本机测试身份后，手动运行
   `local-test-initialize.cmd --approve-local-test-identity`，可显式追加 `--port 8766`。
   cmd 不暗含批准开关；缺开关拒绝。只调用包内已封印模块，使用 Windows CurrentUser DPAPI
   加密新测试身份，不读取个人登录态、不覆盖已有身份、不更改默认配置。
2. 运行 `local-test-start.cmd`。只读取 `data/local-test/launch.json` 的固定三个键，
   状态固定到 `data/local-test/platform`，不合并 `config/launch.json`。
   仅启动测试私有 Wiki 服务和独立 `data/local-test/browser-profile`，
   不启动 learning viewer、Pi、模型、渠道或后台 worker。
3. 页面显示短期一次性码后，在同一版本目录手动运行
   `local-test-pair.cmd --pairing-code <20位大写码>`。工具直连绑定 loopback 端口；
   bearer 不交给页面。页面配对证明与 CSRF 只能存在内存，不写 URL、存储、日志或 Git。
4. `stop.cmd` 停止该版本进程；重新启动或页面内存丢失后需要重新配对，
   不能因旧 cookie 或已有加密身份而声称浏览器已登录。

默认 `start.cmd` 不自动启用上述测试身份。初始化、配对和浏览器壳就绪仍不表示
Windows DPAPI 实测、完整业务、真实模型或全部 E2E 已通过。
交付 Git 忽略所有运行私有目录；不得强制添加本机状态或提交已跟踪配置的本机修改。

## 独立 owner 验收入口（默认关闭）

这些入口仅封装 `OWNER_DPAPI_ACCEPTANCE.md` 中的既有模块，不表示已经批准或执行真实身份创建。
先取得明确的 Windows 路径、CurrentUser DPAPI 身份、权限与材料范围批准，再按范围运行：

1. `owner-acceptance-initialize.cmd --approve-owner-acceptance-identity`，可显式追加 `--port`。
   cmd 不暗含批准；不读/复用 local-test 或个人身份，不覆盖已有目录。
2. `owner-acceptance-start.cmd` 只读取 `data/owner-acceptance/launch.json` 三键配置，
   固定平台 state 为 `data/owner-acceptance/platform`，学习 repo 为 `data/owner-acceptance/desktop`，
   只使用同目录 `desktop.json` 的固定 DPAPI、设备和 loopback 绑定，不合并默认配置。
   基础入口要求 `model.enabled=false`、`providers=[]`，不启动 worker、Pi、渠道或自动 Writer。
3. `owner-acceptance-pair.cmd` 从标准输入读取页面的20位一次性码，码不能放命令行、URL、环境变量或日志。
   工具仅向绑定 loopback 服务批准一次；失败不重试，先查询原页面状态。
4. 浏览器 profile/cache 独立位于 `data/owner-acceptance`，`stop.cmd` 只停止本版本拥有的进程。

默认 `start.cmd` 与 `local-test-start.cmd` 行为不变，不自动采用 owner 身份。
该基础入口的 `pairing_required`、配置成功或模型关闭状态都不是业务或真实模型 E2E 通过。
后续真实模型/独立复核/Writer 需另行冻结并明确批准配置，不能靠修改三键 launch 隐式开启。

## 私有 Wiki 到学习服务的导航

非 local-test 启动会预分配一次 viewer 端口，把规范的 `http://127.0.0.1:<port>/`
通过 `--learning-viewer-url` 交给平台，再用同一端口启动 viewer 并检查健康。
不从 launch JSON 接受 URL 或端口覆盖；碰到平台端口冲突或启动失败直接停止，不扫描或自动换址。
local-test 不启动 viewer，也不传入导航。无身份壳可以声明地址，但仍为 `waiting_auth`。
健康响应的导航只是配置，不证明已配对、目标鉴权或业务完成；链接不携带任何令牌或正文。
详见 `LOOPBACK_LEARNING_NAVIGATION.md`。这里的包装单测不等于实际 Windows 跨端口浏览器验收。

## 限制

- 只支持 Windows x64；云端检查不等于 Windows 运行成功。
- 未装Edge/Chrome时不自动安装浏览器；使用现有浏览器也只建立包内独立profile。
- 主应用、统一服务、Pi、动态Wiki、真实账号、ASR各有独立状态，不能互相替代。
- ASR独立组件和模型有自己的许可证与运行时检查；缺VC++运行时明确报阻塞，不静默系统安装。
- 普通模式的应用状态在包内；固定 owner 模式另有上述批准根目录下的锚点登记与运行账本。但操作系统自己的日志、Prefetch和安全产品记录不属于应用可控制边界。
- 启动器对路径及全部既存祖先检查 Windows reparse 属性，未知 tag 也拒绝；属性读取失败关闭。
  这不是 OS 沙箱或并发恶意路径替换的安全保证，不能将合成路径检查当 Windows 实测。

## 本地动态发布

`publish.cmd` 只消费已审核的 `config/launch.json` 中 `publication_config` 所指向的包内配置，不创建授权。配置需要 repository、revision、domain_database、owner_id、policy、template_assets、staging、output、report、public_base_url。staging/output 必须是 data/publication 下的新目录，report 必须在 data/reports 内。运行预编译渲染器，完成 Git 投影和只读安全门后生成新 HTML；不安装依赖、不转译代码、不部署到公网。

## 固定身份与独立验收运行

已有身份不再按发布版本重建。首次安装仍可显式 initialize；一旦登记固定锚点，
initialize 对新版本拒绝创建身份，缺失或损坏的锚点也不回退生成。选择锚点不是模型授权。

新版入口 `owner-acceptance-reuse.cmd --approve-owner-identity-reuse --new-acceptance-run --anchor-release <已核验的既有release-id>`
只接受批准目录中原地存在的 owner DPAPI 身份。首次选择以后省略 `--anchor-release`，
继续读取同一登记；传入不同锚点会拒绝。不能选择当前 release 作为自己的新运行。
每次运行以当前不可变 release-id 唯一标识，重复操作幂等，不支持通过同一 release 换名称重置。

精确路径及影响（实际 Windows 启用须先确认选定版本、身份和新预算）：

- 只读 `C:\Code\zhizhi-e2e-20261020\git-delivery\releases\<anchor-release>\data\owner-acceptance\identity.json`，
  在同一 Windows 用户下用 DPAPI 解密。token、device、scope、端口及原 package_root 均不改变；不复制或重新加密凭据。
- 写入固定、不可覆盖的非秘密 `C:\Code\zhizhi-e2e-20261020\data\owner-identity-anchor.json`，
  记录锚点 release 与原密文摘要；配置操作共享同目录 `owner-identity-configuration.lock`，防止与首次初始化竞态。
- 在固定 `data\owner-runs` 下写有签名的新运行预约；该运行的真正 activation、attempt、result 与 Pi账本位于
  `data\owner-runs\<current-release>\acceptance-receipts` 和 `pi-receipts`。旧锚点账本不移动、不修改。
- 当前 release 的 `data\owner-acceptance` 只创建非秘密身份引用、固定配置和新的空 platform/desktop 状态，
  不复制旧数据库、知识、浏览器会话或来源证明。删去当前运行状态不会删除固定预算账本，也不会允许自动重新初始化。
- 后续 model/Pi activation 仍分别需要原来的明确批准、完整发布清单验证和固定调用上限。
  新 activation 写入与锚点关联的 `explicit_new_acceptance_run` 标记，不继承、不补回旧运行额度。
  创建身份引用本身不创建任何模型/工具 grant，不运行模型；旧运行成功证据不能算新运行通过。
- 新版服务 health 公开绑定当前 release/source；引用模式的 launcher 必须匹配后才启动 viewer/Pi，
  避免同端口旧服务被误认。不会终止旧版本进程；旧版本占端口时应失败关闭，再由操作者正常停止旧运行。

这是同一身份在获准新版产品中持续使用的配置变更，不是扩大知识分类、工具、模型或调用上限。
运行状态仍隔离；唯一跨版本路径是上述固定身份只读锚点、配置锁和新运行账本。
保留旧版本目录和历史账本，不能为清理磁盘删除锚点。部分初始化或丢失账本应停下诊断，不能删除预约“修复”。
云端的模拟 DPAPI、文件及封装测试不是 Windows DPAPI、真实模型或最终 E2E 验收。
