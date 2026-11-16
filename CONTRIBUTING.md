# 开发与贡献

1. 阅读 AGENTS.md、架构和所属模块 README，明确变更范围与兼容要求。
2. 使用功能分支，小步修改；路径搬迁、依赖升级和行为改变分别提交。
3. 本地通过 scripts/dev.ps1 check 做语法检查；不要顺带执行测试、构建或真实模型调用。
4. PR 说明问题、最终行为、实际检查和限制；提交信息使用中文。
5. 对应 GitHub Actions 通过后合并。缺少凭据、跳过或被阻塞不能写成验收成功。

命令见 [开发指南](docs/development.md)，长期决定见 [Agent Notes](.agents/notes/README.md)。
