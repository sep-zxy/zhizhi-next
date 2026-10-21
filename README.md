# 知枝统一知识平台

本仓库从已核实的 `33b4dc1` 基线重新应用目录精简；没有找回此前丢失的后续 Git 对象，也不继承旧测试结果。当前验证与范围见 [目录审计](docs/implementation/REPOSITORY_LAYOUT.md)。

- `projects/growth/`：主应用、领域服务、viewer 与桌面入口；见 [运行说明](deploy/knowledge-platform/README.md)。
- `integrations/pi-zhizhi/`：Pi 扩展与受限模型接口。
- `projects/selfweb/`：Quartz 只读发布出口。
- `knowledge-contracts/`：知识 Schema、模板及既有操作契约。
- `projects/vault-manager/`：契约要求保留的元数据校验与原子写工具。
- `projects/continuity-widget/`：契约要求保留的 SQLite 项目库和唯一快照 CLI。
- `optional/obsidian-ai-writer/`：独立可选 Obsidian 插件，不是主平台安装/启动依赖。
- `tests/e2e/`：隔离跨模块旅程；`tests/repository/`：布局验证。
- `docs/implementation/`：实现接口与验收边界；`docs/execution/`：历史设计。
- `docs/archive/import/`：历史来源与安全排除记录，不是部署输入。

统一 CI 入口是 `.github/workflows/knowledge-platform.yml`；遵守各级 `AGENTS.md`。fixture、隔离集成与真实账号端到端必须分别报告；此前验收文档属于恢复基线的历史记录，不证明此次重应用版本已测。
