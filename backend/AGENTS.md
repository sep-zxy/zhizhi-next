# Python 后端规则

- 保留 ahadiff 技术入口和既有数据标识；通过 uv 使用仓库工具链。
- 正式知识经 DomainService 审核及唯一 Writer 更新；适配器不直接操作领域 SQLite。
- Markdown/Git 为正式正文，SQLite 为流程账本；索引为可重建投影，不能成为第二份可编辑正文。
- 迁移递增版本并保护旧数据；未知状态停止依赖操作，保留可对账证据。
- 改接口时同时检查 apps、Pi 集成及现有本地模式；不删除暂未出现在界面的底层能力。
- 后端测试、性能基准和发行包在 GitHub Actions 执行，配置见 [测试规则](../docs/testing.md)。
