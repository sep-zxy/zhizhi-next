# 可选组件

三个组件保留完整源码、锁文件与现有数据格式；不进入主平台默认依赖与启动链。

- [Vault Manager](vault-manager/README.md)：Obsidian 属性管理与校验。
- [Continuity Widget](continuity-widget/README.md)：独立 SQLite 项目工作区与快照 CLI。
- [Obsidian AI Writer](obsidian-ai-writer/README.md)：独立写作插件，确认后直接修改选定文件，不是平台同步或审核接口。

统一目标分别为 vault-manager、continuity-widget、obsidian-ai-writer；测试和构建交 GitHub Actions。组件原文中的个人机器路径仅为既有使用示例，不是新环境默认配置。
