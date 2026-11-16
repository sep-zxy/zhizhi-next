# 当前架构

知枝保留 Python/Starlette 模块化后端。backend/src/ahadiff/knowledge_platform 管理来源、任务、提案、审核、Git Writer、检索与公开资格；既有 growth、serve、llm、quiz、review 等提供工程学习和本地模式。

apps/learning-web 是学习 React 界面，apps/wiki-private 是独立私有 Wiki，apps/desktop 是 Electron 宿主。apps/public-wiki 使用 Quartz 对合格公开投影构建只读站点。Pi 集成调用统一领域接口，不任意操作文件或审批。

正式正文是 Markdown/Git；SQLite 保存任务、审核、索引目录和幂等账本；私有对象目录保存原件，检索与关系是可重建投影。临时预览须由本人 keep 才成为长期来源，知识变更经过规则与复核后由唯一 Writer 提交。

可选 Vault Manager、Continuity Widget、AI Writer 独立安装和运行。现有本地模式和统一平台模式均保留，后者没有配置时不偷偷回退成另一份正式 Wiki。

Obsidian 双向同步、同主题聚合呈现尚未完整实现，见 [产品提案](../.agents/notes/proposed/2026-11-16-product-direction.md)。当前知识服务的独立裸 Git 库不等同于已经连接个人 Vault。

职责与限制见 [知识子系统](subsystems/knowledge.md)、[模型与入口](subsystems/integrations.md) 及各组件 README。发布、凭据和真实验收不会因构建成功自动启用。
