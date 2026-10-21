# 云端预编译的动态 Wiki 运行组件

## 边界

Windows 只运行 `node app/wiki/render.mjs --staging <投影目录> --output <不存在的新目录> --public-base-url https://example.org`。输入必须是后端已审核的公开投影；运行入口不接收数据库、仓库、账号或凭据。生成后仍须由 Python 使用原投影清单、独立模板资源 allowlist、Domain SQLite readonly resolver 和固定 Git revision 执行最终证书门禁。成功生成页面不是公开发布，也不是门禁成功。输出必须放在 `data/publication/outputs/<新运行目录>`，投影在独立 staging。

Node 入口绕开 Quartz CLI，固定 concurrency=1，不启动 parser worker、不编译 TypeScript。编译发生在云端 `site/portable/build.mjs`：原 SCSS 和浏览器 inline TS 预先构建；ComponentResources 的编译器 import 替换成精确输入 SHA256 回放适配器。云端捕获全部模板资源集合与编译结果，运行期未知 CSS/JS 输入、资源集合直接拒绝，禁用原 CSS 编译失败后回退原文的分支。运行版捕获开关编译成 false，环境变量不能重新启用。原上游源码和锁未改；差异只作用于已记录的派生 JS。

当前配置为 KaTeX，未用 Typst 的静态 import 在派生插件 dist 中替换为抛错函数，原源码/hash 保留，原/派生 dist hash 另存 derived-artifacts.json。包内不带 esbuild、lightningcss、Sass、TypeScript、tsx、tsup、Typst 编译器。sharp 是图像运行库，simple-git 是只读文件日期支持，必须保留。Windows 分包仅用原 npm 锁精确版本的 Windows x64 官方预编译 native 包，校验原 sha512；Linux native 包被移除。

完整性检查逐个验证 component.json 文件 SHA256，拒绝额外文件、符号链接、资源漂移及输出复用；Markdown 内主动脚本/样式和单独动态代码输入拒绝。component.json 的可信 hash 应由总装配清单固定，单独可写 manifest 不是签名。使用隔离最小子进程环境，不继承 NODE_OPTIONS/NODE_PATH、用户配置或秘密。

## 云端重建

在已按原 npm 锁准备并构建 31 插件的 site 目录执行：

1. `node portable/build.mjs capture portable/capture.generated.mjs`，以模板 fixture 调用导出 render，设置 QUARTZ_OFFLINE_PLUGINS=1、显式 QUARTZ_PUBLIC_BASE_URL、QUARTZ_RESOURCE_STORE=<新 JSON>。只云端 capture 版本允许收集资源。
2. `node portable/build.mjs runtime portable/runtime.generated.mjs`。
3. `uv run --no-project --python <锁定 Python> portable/package.py <新 Linux 测试组件> <资源 JSON>`。package.py 保留 source provenance、全部必要 licenses、固定字体和包锁。
4. 通过官方 registry `npm pack @img/sharp-win32-x64@0.34.5 @napi-rs/simple-git-win32-x64-msvc@0.1.22 --json` 得到归档及 pack.json。`portable/windows.py <测试组件> <新 Windows 组件> <native 归档目录>` 校验原 lock sha512 后转换。
5. `portable/test-runtime.py <Linux 组件> <投影 A> <投影 B> <新证据目录>`，之后分别运行原 Python 最终 readonly gate。不能把不同内容的旧静态文件复制来通过。

## 当前证据

两套独立审核的 synthetic model fixture 经真实 Domain SQLite/Git 投影后，在完全独立且无编译器依赖的目录成功动态生成各 54 文件。内容页面 hash 不同，41 个固定资源 hash 相同且匹配先前独立模板 allowlist。两个最终 readonly publication gate 通过。另有 5 个拒绝用例通过：复用输出、额外 JS、Markdown script、组件额外文件、未知资源（即便注入 capture 环境变量）。Node typecheck 已通过。

证据在 evidence/。这不是 Windows 实机、真实账号、真实模型或公网部署验收；Windows 执行仍待最终便携包黑盒测试。

## 一键调用完整发布链

后端已安装的 Python 运行组件内 `publish.py`，参数为：
`--node <包内 node.exe> --repository <包内 vault Git> --revision <精确 commit SHA> --domain-database <可信 SQLite> --owner-id <已授权 owner> --policy <显式审核 policy.json> --template-assets <独立审核模板 allowlist.json> --staging <新投影目录> --output <新产物目录> --report <新内部报告文件> --public-base-url https://example.org`。

它按顺序执行 readonly 资格 resolver + 固定 Git 投影、最小环境 Node 动态渲染、重新打开 readonly ledger 复核、最终 artifact gate。三个写入路径必须新建且相互隔离，失败不生成成功报告，不改变既有公开站点。总装可调用该入口，不需要 Windows 现场编写或编译脚本。policy 和 template-assets 不得从待发布内容中自我生成。

便携模式由 `ZHIZHI_PORTABLE_ROOT` 显式启用：复用后端公共路径校验，全部输入在包内，staging/output/report 进一步限制在 `ZHIZHI_PORTABLE_STATE`。所有已存在祖先的 symlink/junction 都拒绝，Node 只能是包内 `runtime/node/node.exe`；Node 子进程保留后端已验证的包内 HOME/USERPROFILE/APPDATA/临时与 XDG 路径以及 root/state，不继承代理或秘密。普通云端测试模式兼容原有独立目录。

便携路径补丁回归：16 个边界分支通过，完整 Git 投影至最终 readonly gate 再次通过；Node 直接入口也拒绝便携根外路径及非包内运行时。

Node 最低运行声明经补测为 22.23.3：官方 npm node-linux-x64@22.23.3 实际运行双内容与5拒绝用例全部通过，完整投影至最终 readonly gate 通过。24.19.0 仍为已测云端构建版本；Windows native执行仍待实机。
