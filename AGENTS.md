# zhizhi-next 开发规则

使用简体中文解释；代码、命令、日志保留原文；提交信息使用中文。

- 修改前阅读 [架构](docs/architecture.md)、所属组件 README 和子目录 AGENTS.md。方案评审不直接修改产品行为。
- 小步修改，保留来源、许可证、既有行为与测试；目录整理不升级依赖，不擅改 ahadiff 包名、CLI、Electron appId 或用户数据目录。
- CLI 使用 PowerShell 7；Python 经 uv；有 rtk 时使用 rtk 代理外部命令。PowerShell 脚本设置 $ErrorActionPreference = 'Stop'，文件读写指定 UTF8。
- 本地默认仅语法校验，不自动执行测试、类型检查、构建或视觉检查。测试与构建使用 GitHub 托管 Actions runner，不伪造 CI 标记。见 [验证规则](docs/testing.md)。
- 不提交凭据、真实个人知识、运行数据库、模型权重、缓存、依赖或构建产物。不得覆盖用户编辑，未知结果不得展示成成功。
- 公开推送、生产部署、真实账号与模型调用按当前明确授权执行；历史文档中的临时授权不构成当前权限。
- 跨模块接口变化同时检查消费者；持久化变化须说明迁移与恢复。业务规则在其拥有模块执行，不能只靠前端或提示词限制。
- 行为变化同步更新所属文档；同一事实只维护一处。长期取舍记录于 [.agents/notes](.agents/notes/README.md)，机械修改不强制新增决策。
- 报告实际运行的检查和对应提交；区分 fixture、真实组件、真实账号验收，失败、跳过、未执行如实列明。

入口：[开发](docs/development.md) · [配置](docs/configuration.md) · [贡献](CONTRIBUTING.md) · [迁移](docs/migration/README.md)。
