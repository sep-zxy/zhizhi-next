# 当前架构

知枝保留 Python/Starlette 模块化后端。backend/src/ahadiff/knowledge_platform 管理来源、任务、提案、审核、Git Writer、检索与公开资格；既有 growth、serve、llm、quiz、review 等提供工程学习和本地模式。

首期 Web 包含 `apps/learning-web` 学习界面和 `apps/wiki-private` 私有 Wiki。Pi 集成通过统一领域接口访问业务能力，文件写入和审批由服务处理。目标是由云端 CLI 统一启动核心服务，Web 和安卓共享服务接口，桌面只保留轻量 Git 采集入口。当前已有知识服务启动入口，统一 CLI、安卓客户端和桌面 Git 采集器仍待实现。

正式正文是 Markdown/Git；SQLite 保存任务、审核、索引目录和幂等账本；私有对象目录保存原件，检索与关系是可重建投影。临时预览须由本人 keep 才成为长期来源，知识变更经过规则与复核后由唯一 Writer 提交。

平台模式需要显式配置，正式知识写入统一由服务处理。现有实现的验证范围见 [首期交付范围](testing.md#首期交付范围)。

Obsidian 双向同步、同主题聚合呈现尚未完整实现，见 [产品提案](../.agents/notes/proposed/2026-11-16-product-direction.md)。当前知识服务的独立裸 Git 库不等同于已经连接个人 Vault。

职责与限制见 [知识子系统](subsystems/knowledge.md)、[模型与入口](subsystems/integrations.md) 及各组件 README。发布、凭据和真实验收不会因构建成功自动启用。
