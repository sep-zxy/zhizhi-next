# 可选组件规则

- 三个组件分别维护依赖、锁文件、README 和数据说明，不进入主平台必装或默认启动链。
- Vault Manager 与 AI Writer 的文件编辑不等于平台审核写入；统一同步能力尚需产品开发。
- Continuity SQLite 为其自身实时数据源，Markdown 快照不作为编辑主库。
- Rust、Tauri、Obsidian 插件的构建与测试交 GitHub Actions；保留既有用户数据目录。
