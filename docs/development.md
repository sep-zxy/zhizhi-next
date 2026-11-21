# 开发指南

## 环境

使用 Git、PowerShell 7 和 config/toolchains.json 中登记的 Python、Node、uv、npm、pnpm、Rust。现有运行时基线保留，工具链的 CI 验证状态以 Actions 为准。不要修改用户全局 PATH 或自动安装全局工具。

## 本地语法检查

在仓库根运行：

~~~powershell
$ErrorActionPreference = 'Stop'
rtk proxy pwsh -NoProfile -File './scripts/dev.ps1' check
~~~

check 只解析本次变更中的 Python、JavaScript、PowerShell；TS/TSX 使用已安装 TypeScript 解析器，Rust 使用已安装 rustfmt 的解析诊断；缺工具时报告未检查并非零退出。它不安装依赖、不执行类型检查、测试、构建或应用。全仓源码解析可显式使用 -All。archive 和构建目录不属于默认范围。

## CI 命令

scripts/dev.ps1 的 install、lint、typecheck、test、test-integration、build、package 目标仅在 GitHub 托管 runner 执行。-Target 指定 backend、learning-web、wiki-private、desktop、public-wiki、pi-zhizhi、vault-manager、continuity-widget 或 obsidian-ai-writer。

Python 按 uv.lock 安装，不安装缺少前端成品的本项目；测试从 backend/src 导入。前端与桌面使用各自原有锁文件。Quartz 保留 site 和 vendored 插件的 npm 锁文件；安装与插件编译由 public-wiki 构建阶段完成。

普通依赖安装不得更新锁文件。升级依赖单独提交，说明原因、兼容性与验证结果。各模块测试留在其拥有模块，根 tests 管跨模块和仓库规则。

## 构建与发行

从 `main` 手动运行 `build.yml` 构建所需产物。发行时在 `release.yml` 提交 Build run ID、产物名称、完整源码 SHA 和新标签。默认只核验。同一个 SHA 必须有通过的 main CI，产物需要匹配构建尝试号和完整校验清单。明确获准发布后才设置 `publish=true`。首期目标和 CI 范围见 [验证规则](testing.md)。

真实模型、渠道与安装验收由 live-acceptance.yml 单独登记和预检；没有获准的范围、素材和凭据时必须标 blocked，不能使用 fixture 假装通过。详细证据含义见 [测试规则](testing.md)。
