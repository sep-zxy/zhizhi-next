# 验证规则

本地默认只进行语法解析。类型检查、测试和构建在 GitHub 托管 runner 执行。

## 首期交付范围

知枝 Next 首期将核心能力集中在云端 CLI 和知识服务。Web 与安卓对接同一套服务。已纳入 CI 和构建的是 Linux 后端 `backend`、学习 Web `learning-web`、私有 Wiki `wiki-private` 和现有服务端 Agent 适配 `pi-zhizhi`。

安卓客户端和轻量桌面 Git 采集器尚无可构建工程。共享 API 验证覆盖现有服务，安卓设备验收待客户端接入后执行。原知枝仓库和历史归档仅供参考，历史验收、退役应用和独立工具不参与首期检查与发行。

## 合并检查

PR、main 推送与手动触发都运行 `Repository CI`。`Required checks` 核对当前范围内的任务结果和跳过状态，保留分支保护所需的稳定名称。仅文档或历史参考改动的 PR 运行仓库规则，产品或 CI 变更的 PR、main 推送及手动触发运行首期全套检查。

| 任务 | 验证内容 |
|---|---|
| Repository policy | 维护文档链接、锁文件和配置示例、CI 范围规则及活跃源码空白检查 |
| Cloud CLI and API | CLI 与知识服务入口、SQLite、严格类型回归、后端单元和隔离集成 |
| Web | 学习 Web 和私有 Wiki 各自运行类型、单元与 Chromium 合成交互，私有 Wiki 在此构建 |
| Agent tool protocol | Pi 适配类型、单元、Python / TypeScript 组合和构建 |
| Cloud CLI package | 调用可复用构建工作流，构建学习 Web 和 Python wheel，验证内嵌 Web 资源，再安装 wheel 并启动 CLI 和知识服务的帮助入口 |

后端隔离集成使用真实的本地 HTTP、Git、SQLite 和知识服务进程，覆盖鉴权、入库和进程重启。外部模型、账号和渠道保持禁用或使用隔离 fixture。历史交付套件保留供单独运行，在首期后端测试中的范围见 `scripts/dev.ps1`。未安装 ASR 模型时相关用例如实跳过。性能预算有独立测试入口。

类型回归使用当前锁定工具链和 Next 自身的不可变源码基线 `a8749ee3`，保留完整诊断并阻断新增错误。CI 保留对应提交与运行尝试的报告。

## 构建与实际验收

发布步骤见 [开发指南](development.md#构建与发行)。合成交互使用 Chromium 与测试数据，关闭截图、录屏和追踪。真实部署、账号、模型和安卓设备验收独立执行。

| 证据 | 证明范围 |
|---|---|
| 语法和静态检查 | 代码可解析，配置和维护资料符合仓库规则 |
| 单元测试 | 已知条件下的组件行为 |
| 隔离集成 | 临时 SQLite、Git、HTTP、浏览器或进程的组合 |
| 构建和安装检查 | 对应提交的产物可构建，并通过安装和入口检查 |
| 真实验收 | 获准账号、模型、资料和安装环境下的用户旅程 |

每次报告记录对应源码 SHA 和运行链接。区分通过、失败、跳过和未执行。必须保存的证据上传失败时，工作流保持失败。

CI 的任务拆分、稳定汇总和独立发布流程参考了 [DeepSeek Harness 的 GitHub Actions 规范](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70496aa02986ca6a820c5d497e816b69d7e/.github/AGENTS.md)。
