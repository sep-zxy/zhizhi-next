# 历史导入材料

保存导入时的安全审查、排除原因和来源摘要；原 handoff 的指令描述当时状态，当前限制以根 AGENTS.md 与用户最新授权为准。

provenance.json 保留原项目提交、分支、来源、未提交修改摘要和精确 restoration_commit。旧逐文件清单/哈希表只校验原导入包，既不是当前源码完整性保证，也不是运行输入，所以从当前工作树移除。

旧 handoff 引用的清单仍在 restoration_commit 中：先用 git show <restoration_commit>:SOURCE_MANIFEST.json 检查，按需用 git restore --source=<restoration_commit> -- <原路径> 恢复，勿覆盖未提交修改。当前操作不重写 Git 历史。此次是基线上的重新应用，不声称恢复丢失的后续对象。
