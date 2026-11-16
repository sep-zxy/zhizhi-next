# Python 后端

保留 ahadiff 包、CLI、现有分析与学习底座，以及 knowledge_platform 模块化领域服务。Python 要求由 pyproject.toml 声明，固定开发工具链由仓库 config/toolchains.json 登记。

- 平台入口：python -m ahadiff.knowledge_platform；配置须显式提供，不自动生成真实身份或启用模型。
- 既有学习服务：ahadiff serve，保留本地模式和既有工作区名称。
- 正式正文由唯一 Writer 写入独立 Git；任务、审批与幂等在 SQLite；原件位于私有对象目录。
- 学习前端源码位于 ../apps/learning-web。构建发行包时将其 dist 内容装入 ahadiff/_webui。
- 旧底座与 cloud、review、quiz 等代码仍保留；缺少界面入口不表示可删除。

安装和检查见 [开发指南](../docs/development.md)，数据语义见 [知识子系统](../docs/subsystems/knowledge.md)。MIT 及上游版权保留在 [LICENSE](LICENSE)。

复习、检索与对应 HTTP/CLI 路径拥有的 SQLite 连接通过 managed_sqlite_connection 保留事务提交/回滚，并在退出作用域时关闭。connect_review_db 的公开返回类型和调用者显式管理连接的方式保持兼容；没有数据库格式迁移。

文件描述符使用二进制模式，确保 Windows 上的 CRLF 和 Ctrl-Z 原始字节参与哈希、大小边界和导出校验，依据 [Python os.open 约定](https://docs.python.org/3.13/library/os.html#os.open)。保留原有路径身份与符号链接检查，不自动改写或重签历史完整性标记。新对象引用使用 POSIX 相对路径保存，实际数据目录不变。

Windows 预览导出复用原生句柄校验，锁定父目录后通过文件句柄提交和清理；没有 dir_fd 或原生安全后端时仍拒绝执行。生成的证据文本保留输入换行，避免 Windows 文本写入改变已计算的哈希。备份检查的临时副本在规范化的系统临时目录中创建，仍拒绝快照内的链接和重定向。
