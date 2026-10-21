# MCP 只读数据库契约

MCP 的只读边界是：不修改主数据库、schema 或业务内容；拒绝写语句；读取最新已提交记录，包括尚未 checkpoint 的 WAL 数据。不将它描述为物理文件系统零写入。

连接仍使用原有安全路径核验、`mode=ro`、`trusted_schema=OFF` 和 `query_only=ON`，未扩大权限。SQLite 在读取 WAL 数据库时可能建立 `-wal` / `-shm` 协调文件。这属于 SQLite 自身的正常行为，不等于业务写入。参见 [SQLite WAL 只读数据库说明](https://www.sqlite.org/wal.html#read_only_databases)。

不能为消除这些文件改用 `immutable=1`：该标志会跳过锁和变更检测，不能用于仍可能更新的账本；也不能丢弃 WAL 中尚未 checkpoint 的已提交内容。参见 [SQLite immutable URI 说明](https://www.sqlite.org/uri.html#uriimmutable)。本次不增加快照层，不删除 sidecar，不执行额外的生产 checkpoint。

## 合成回归边界

- 已关闭并 checkpoint 的 WAL 库：主文件逐字节不变，schema、user_version 和业务行不变，写入被拒绝。
- 活跃 WAL 库：最新已提交修改只存在 WAL 时仍可读；读取及被拒绝的写入前后，主文件与原 WAL 逐字节不变，不偷偷 checkpoint。
- DELETE 模式：保留独立的零新增 `-wal` / `-shm` / `-journal` 检查，不按已有文件跳过断言。
- 原有 SQL 写入拒绝、附加数据库写入拒绝、工具输出和安全路径测试继续保留。

测试种子连接显式关闭，避免将 Python 的事务上下文误当成连接关闭而受 GC 时序影响。这些测试是云端合成 SQLite 验证，不代表真实账户、模型或 Windows 端到端验收。
