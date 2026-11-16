# Pi 知识集成

固定 Pi 1.3.0，保留现有 18 个领域工具、本人交互命令、Codex/Pi 模型桥和便携适配源码。模型工具不能代替本人 keep、重要审批或直接 Git 写入。

依赖使用 npm ci，命令由根 scripts/dev.ps1 的 pi-zhizhi 目标调用；类型、单元、协议装配与构建在 GitHub Actions 执行。模型与账号不随安装自动启用。

后端工具清单在 ../../backend/src/ahadiff/knowledge_platform/model/pi_tools_v1.json；改动需同时检查 TypeScript、JSONL 与真实隔离 Python 桥。协议脚本位于 test/*.mts，不属于普通 test/*.test.ts 的收集范围，须单独执行。

完整自然语言消息入口、统一 CLI 细项与真实账号旅程仍有待补齐；历史固定发行验收槽不因本仓建立而获得新预算或授权。参见 [入口子系统](../../docs/subsystems/integrations.md)。
