# 知枝便携 Pi 原生 JavaScript 适配

本组件基于 @earendil-works/pi-coding-agent 1.3.0，**不是未经修改的官方二进制**。上游许可证保留于 node_modules 对应包，component.json 列出所有许可证及文件哈希。没有内含账号、认证存储或模型结果。

上游扩展 loader 调用 jiti.import；tryNative:false 路径对有效 ESM JavaScript 也会进入 transformer，仓库测试实际验证了此行为。因此仅将扩展后缀改为 .js 并不足够。

云端 TypeScript 5.9.3 预编译四个扩展模块，重写相对 .ts 引用为 .js。构建仅在上游 loader SHA256 精确匹配时，以原生 import 替换 loadExtensionModule 内部的 jiti 路径。允许的 factory 仅 extensions/index.js 与 extensions/model-guard.js，其他 JS/TS 均失败关闭，无 jiti fallback。缓存和 Pi 事件/工具加载流程保持上游行为。

cli-native.mjs 安装原生 Node resolve hook，拒绝 TS/TSX/MTS/CTS 与 jiti/tsx/typescript；生产交付移除了 jiti、tsx、typescript、esbuild 和 Pi 替代 bundled-loader。启动还应传 --no-experimental-strip-types。没有现场 npm/pip、构建命令或缺包下载步骤。缺失依赖须返回错误，不得在 Windows 修复安装。

## 总装接口

目录整体映射 app/pi。Windows Node x64 22.23.3 位于 runtime/node/node.exe。交互调用：

    node.exe --no-experimental-strip-types app/pi/launch-interactive.mjs

交互必须配置绝对 PI_CODING_AGENT_DIR=data/pi-interactive，先创建该目录；ZHIZHI_MODEL_PROVIDER 与 ZHIZHI_MODEL_ID 只能使用用户已批准的精确订阅路由，不允许别名、通配符、备用列表。未配置即输出 waiting_auth，不猜账号、不调用模型。ZHIZHI_ALLOWED_PROVIDERS 由交互入口固定为该 provider。还需 ZHIZHI_API_URL、经授权的 ZHIZHI_AGENT_TOKEN / ZHIZHI_USER_TOKEN。不要写入交付文件。OpenAI 禁止普通API key，必须通过官方OAuth；Coding Plan资格、国内条款仍由已有策略核查。

Python PiRunnerConfig 的 executable 应为 [node.exe绝对路径, "--no-experimental-strip-types", app/pi/cli-native.mjs绝对路径]；guard_extension 指向 app/pi/extensions/model-guard.js；agent_dir 必须为独立 data/pi-worker，不能与交互共享。后台入口由既有 Python 预检/配置控制，默认 enabled=false/routes=[]，没有账号时 waiting_auth。不要用 raw CLI 绕过 Python 授权预检；后台 guard 没有任何工具权限。

交互入口禁用自动批准、内置工具、MCP、环境扩展/skills/prompts/context/themes，只有审计过的领域工具与本地 TUI 人类审批命令。路由模型若被切换，provider dispatch 拒绝。域服务仍独立执行审批/owner权限，模型不能替用户批准。CLI原始入口是后台适配接口，不是供最终用户任意传参的快捷方式。

继承环境须由总装清理 NODE_OPTIONS/NODE_PATH、未授权凭据与代理。交互入口进一步移除常见付费API key；后端既有白名单更严格。PI_OFFLINE/PI_SKIP_VERSION_CHECK 禁止自动目录及版本探测，不阻止未来明确授权的模型请求。Windows数据、TEMP/TMP、APPDATA/LOCALAPPDATA、HOME/USERPROFILE由总装限制在包内新目录。

## 原生依赖和验证边界

Pi TUI官方包附带 native/win32/prebuilds/win32-x64/win32-platform.node（Windows控制台/剪贴板，kernel32/user32）。最终交付裁除Linux/macOS及Windows ARM预构建，仅保留Windows x64原生文件。Linux的无界面RPC冒烟不依赖这些剪贴板预构建，不能替代Windows TUI验收。photon与quickjs-wasi使用已交付WASM，不在目标机编译C/C++。quickjs-wasi的五个extensions/*/*.so按文件魔数确认为WASM，不是Linux ELF共享库，需保留。

云端测试使用真实Pi RPC、空认证目录、两枚已构建扩展，禁用Node类型擦除且生产组件不存在转译器。另检验外部JS/TS拒绝及默认waiting_auth。它证明云端原生JS加载链路，不等于Windows实机验收，也不等于真实订阅账号/模型端到端通过。Windows控制台、授权登录与实际模型链路仍须后续授权验收。
