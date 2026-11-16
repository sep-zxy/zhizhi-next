# 知枝 Windows Git 成品交付

此分支只含云端已经完成的 Codex 小补丁、只运行验收工具及校验清单。
不包含新的运行时、开发依赖、私人知识、凭据、运行数据或截图。
源代码在 main 分支继续维护；此分支不是 Windows 开发环境。

## 获取与运行

在经过授权的独立目录检出本分支，必须保留 `.gitattributes`；
它关闭 Git 换行转换，保证文件字节与云端已验证成品完全一致。
先运行 `verify.cmd`，只有 `delivery_integrity_passed` 才继续。
该命令只读检查，不编译、不安装、不登录、不修改原应用。

目标应用固定为 `C:\Code\zhizhi-e2e-20261020`。
停止原应用后运行 `patch\check.cmd`；在既定补丁安装授权范围内运行
`patch\apply.cmd`，必要时使用 `patch\rollback.cmd`。这些脚本验证原包，
只替换清单列出的 immutable 文件，不覆盖 config、state、data 或 runtime。

`smoke\run.cmd` 的运行条件、现有官方 Codex CLI 与登录路径参数见
`smoke\README.md`。这会调用真实模型，仍须具备对该请求的既有授权；
获取 Git 文件或通过完整性校验本身不代表模型测试通过。

## 更新规则

Windows 只 fetch、检出经确认的成品版本、校验并运行，不改源码或编译。
不在原应用目录执行 Git checkout，不覆盖用户数据，不现场修补脚本。
固定具体 Git commit 后运行，避免更新过程中混用两个版本。
每次成品有可核查的文件 SHA-256 与原云端归档 SHA-256；Git 只传变化。
约 205 MB 的既有基础运行时不重复纳入此分支。

`delivery.json` 不是数字签名；它与 Git commit 身份及既有预期校验值一起使用。
本次重新包装只验证交付字节，不声称 Windows、真实账号或全平台 E2E 已通过。
