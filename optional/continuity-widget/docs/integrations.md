# 工程事件适配

Continuity 的外部集成只生成或提交 version 1 工程事件，不直接读写 SQLite。所有样例默认不安装，也不会仅凭标题相似度自动关闭事项。

## Claude Code

`integrations/claude-code/task-completed.ps1` 只在 Task metadata 同时包含 `continuityProjectId` 和 `continuityItemId` 时生成完成事件。缺少精确目标时返回 `audit_only`。添加 `-Submit` 才会调用本机包装脚本。

## Git

`integrations/git/post-commit.example.ps1` 需要提交信息同时包含：

```text
Continuity-Project: <project-uuid>
Continuity-Item: <item-uuid>
```

调用方还必须提供 `CONTINUITY_TEST_EVIDENCE`。默认只输出 JSON；只有显式设置 `CONTINUITY_SUBMIT=1` 才提交事件。样例失败不得阻断提交。

## GitHub Actions

GitHub Actions 样例只生成无密钥 JSON artifact，不连接本机数据库。下载 artifact 后仍应由本机用户核对精确目标和证据，再通过 `continuity-event.ps1` 提交。

## 安全边界

- Hook 失败不能关闭项目、归档项目或阻断 Git 提交。
- failure/cancelled 只登记证据，不完成事项。
- 不写入 token、完整日志、聊天正文或私密 payload。
- 重试必须复用原 `eventId`；冲突时停止并检查既有 receipt。
