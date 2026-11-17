# 来源与第三方许可

产品思路与仓库演进见 [README 的来源说明](README.md#来源与贡献)。Git 历史迁移基线与路径记录见 [layout.json](docs/migration/layout.json)。目录整理不改变原始版权或依赖许可。

- Python 知枝底座源于 AhaDiff；MIT 及原版权在 backend/LICENSE。
- Quartz 与 vendored 插件保留各自 LICENSE、NOTICE、原始锁文件和来源校验材料，位于 apps/public-wiki。
- Obsidian AI Writer 保留 optional/obsidian-ai-writer/THIRD_PARTY_NOTICES.md。
- Rust、Electron、字体与其他资源的许可保留在所属组件及资源目录；不能以某个模块的 MIT 概括所有第三方发行条件。
- ASR 分发许可仍有待复核边界，见 backend/scripts/asr-portable/LICENSE_REVIEW.md；不纳入默认正式发行。

仓库治理参考 DeepSeek Harness 固定提交 39163dd30f703a79079e2d2446545cff3c453738 的 AGENTS.md、docs/AGENTS.md、docs/testing.md；本次为适配现有技术栈的规则整理，未复制其运行时实现。
