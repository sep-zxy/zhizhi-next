# Python 后端

保留 ahadiff 包、CLI、现有分析与学习底座，以及 knowledge_platform 模块化领域服务。Python 要求由 pyproject.toml 声明，固定开发工具链由仓库 config/toolchains.json 登记。

- 平台入口：python -m ahadiff.knowledge_platform；配置须显式提供，不自动生成真实身份或启用模型。
- 既有学习服务：ahadiff serve，保留本地模式和既有工作区名称。
- 正式正文由唯一 Writer 写入独立 Git；任务、审批与幂等在 SQLite；原件位于私有对象目录。
- 学习前端源码位于 ../apps/learning-web。构建发行包时将其 dist 内容装入 ahadiff/_webui。
- 旧底座与 cloud、review、quiz 等代码仍保留；缺少界面入口不表示可删除。

安装和检查见 [开发指南](../docs/development.md)，数据语义见 [知识子系统](../docs/subsystems/knowledge.md)。MIT 及上游版权保留在 [LICENSE](LICENSE)。
