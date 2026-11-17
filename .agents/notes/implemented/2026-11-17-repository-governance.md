# 仓库治理与职责布局

Status: implemented

## 问题

来源快照将应用、历史授权、验收过程和当前开发入口混在一起，造成目录与说明不一致。

## 决定

保留 Git 历史，以 backend、apps、integrations、optional 区分职责。根 AGENTS 保留长期共性规则，子目录补充专属规则；历史授权和退役交付进入 archive。代码名、数据目录、组件锁文件和行为保持兼容。

本地默认仅语法解析；类型、测试和构建在 GitHub 托管 runner 执行，真实验收、发行分别手动触发。工具链统一登记，各组件保留既有依赖管理方式。

日常 CI 按组件所有权运行 Linux 标准检查；文档改动只校验维护元数据，知识契约变动覆盖实际消费者。后端或 Pi 改动保留协议组合；原有后端单元与隔离集成继续执行。跨平台重复回归、独立浏览器旅程、性能基准和应用构建由同一工作流的手动 full 模式承接，具体入口与证据边界以 [验证规则](../../../docs/testing.md) 为准。

参考 DeepSeek Harness 的 [主 CI](https://github.com/deepseek-ai/deepseek-harness/blob/master/.github/workflows/ci.yml) 与 [Python CI](https://github.com/deepseek-ai/deepseek-harness/blob/master/.github/workflows/ci-python.yml) 的分层与稳定汇总方式，范围按本仓库的组件和发布阶段确定。main 通过 PR 与 Required checks 保护，禁止强推和删除，要求解决审阅对话；个人维护不强制额外审批人数。

## 替代方案

重新初始化历史会失去变更追溯，未采用；全部转为 DeepSeek Harness/Cordis 插件属于产品重写，未采用；仅改文档保留 projects 混合目录不能清楚表达职责，未采用。

每次改动都执行所有组件、三系统后端和完整浏览器旅程，会放大反馈耗时；只留语法检查则不能约束业务回归。选择日常按组件保留行为检查、重检查显式触发，而不是删除测试或降低原有断言。

## 代价

相对路径、打包资源和 CI 需要同步迁移。源文档在 archive 保留原始证据，不能直接作为当前验收结论。检查结果以对应提交的 Actions 为准。

标准检查不证明 Windows/macOS 兼容性、浏览器旅程、性能或产物可安装；相应发布或跨平台改动需主动触发完整回归和目标构建。Required checks 成功只代表本次计划范围内的任务通过。
