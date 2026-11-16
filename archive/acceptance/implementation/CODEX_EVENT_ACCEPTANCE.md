# Codex exec JSONL：协议事实与结果接受分离

本修正不改认证、订阅 provider、请求模型、工具限制或进程清理。修正两项自加假设：
非致命 item.error 不能提前终止收流，也不因无法识别通知文本而必拒合法结果。
用户要求订阅覆盖、合法数据路由和端到端功能，没有要求只有两个已知警告可通过，
也没有要求证明精确 actual model。未知实际型号仍如实记录 null。
本地合成通过不等于真实 Windows/模型通过，旧 144 字节通知的具体内容仍未知。

## 已审官方依据

2026-10-21 复核的 alpha.11 固定提交是
`5425d3e625daa58eea3ad1de27c087eb3b2ffbd3`；当时官方 main 为
`430a16799d90e296fb27b1e94f206023902b7e56`，下列事件处理与 TypeScript SDK 文件相同。

- [ErrorItem 定义](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/exec_events.rs#L129-L135)
  明确是非致命问题；顶层 error 与 turn.failed 是不同事件。
- [JSONL 映射](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L409-L556)
  把配置/运行/弃用通知以及模型重路由压缩为同一种 error item，仍返回 Running。
- [官方非致命警告测试](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/event_processor_with_jsonl_output_tests.rs#L67-L90)
  明确验证 Warning 不结束流。
- [TypeScript SDK](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/sdk/typescript/src/thread.ts#L119-L141)
  收集 item 和终态，只对 turn.failed 显式抛错；子进程非零退出另由 exec 包装处理。
  这不是允许忽略权限/路由变更的依据，也不证明任意 error/completed 组合可接受。

- [exec 的恢复/退出判定](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/lib.rs#L1340-L1407)
  只有 !will_retry 的 error、failed/interrupted 会设置 error_seen 并最终 exit 1。
  JSONL 映射丢失 willRetry，却仍输出顶层 error；Completed 分支明确输出完成事件，
  不因过去存在 error 改成失败。因此完整合法 completed/exit0 是已恢复的终态证据，
  不是忽略任何 error 后任意出现的 completed。turn.failed、重复/非法终态、非零退出、
  流丢失、工具/授权违约和清理失败仍拒绝，不按中间错误字符串触发 auth/quota。

## 接受矩阵

| 协议/自然退出事实 | 独立接受策略 | 输出与缓存 |
| --- | --- | --- |
| completed + exit 0，无通知 | 既有 schema、秘密与输出上限校验 | 全部通过才可返回/缓存 |
| completed + exit 0，有合法非致命 item.error（包括 unknown） | 通知不是失败；继续既有 schema/秘密检查 | 通过后返回/缓存，并保留固定 warning_codes |
| 完整明确的 sandbox-disabled 告警或真实丢流声明 | blocked / CODEX_RUNTIME_BOUNDARY_VIOLATION | 隔离，不返回、不缓存 |
| 顶层 error 后单一合法 completed + exit 0，无其他违约 | 官方可恢复流；继续 schema/秘密检查 | 通过后带 codex_recovered_stream_error 摘要，不猜 auth/quota |
| turn.failed | 自然失败；既有受控错误摘要 | 无输出/缓存 |
| 任意自然非零退出 | 失败；若有正式 error/failed，保留其受控诊断，否则 CODEX_PROCESS_FAILED | 无输出/缓存 |
| EOF/exit 0 无终态 | incomplete / CODEX_INCOMPLETE_TURN | 无输出/缓存 |
| 本地超时/取消且未观察到终态 | interrupted；既有 deadline/cancel 错误 | 无输出/缓存 |
| 工具事件、非法协议、第二终态或终态后事件 | 立即拒绝并有界清理 | 无输出/缓存 |
| 任何清理未确认完成 | 既有主错优先，独立 cleanup 报告，runner 锁存 | 后续含成功缓存均不可访问 |

不再使用两个模板允许列表，也不以诊断 weak hint 推断未知通知的风险。正常版本门禁
仍在启动前验证；普通 ModelTask 和固定公开 smoke 使用同一接受策略，不设探针绕过。

仅以下已审完整语义能证明本适配依赖的边界被破坏：

- [官方 config/mod.rs:4873–4877](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/core/src/config/mod.rs#L4873-L4877)
  的完整 sandbox-disabled 静态告警只在 PermissionProfile::Disabled 时产生，
  与我们显式 read-only 矛盾。必须全文相等，不 trim/lower/关键词匹配。
- [官方 exec/lib.rs:1668–1670](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/lib.rs#L1668-L1670)
  完整 event stream lagged/dropped 正整数语法明确表明流不完整，不能再证明无工具事件。

这不是新增通知分类目录。proxy/masking 的可选防护未开启、hook-trust 的 may-run 提示、
metadata/弃用提示和同一 OpenAI 订阅内 model reroute 本身均不能证明越权，不因它们
单独拒绝。未获准 provider 仍在可信路由与任务分类检查处拒绝，不从文本猜跨 provider。
任何未来有可信证据的真正权限变化应在相应边界验证，不把 unknown 一概当风险证据。

## 有界读取与去敏

- 保留原总超时、stdout/stderr 总字节上限；error item 和顶层 error 各最多 16 条。
- startup 配置通知可以先于 thread.started；正常 turn/item 仍须遵守线程与回合顺序。
- 收到非致命 item 不再杀进程。只有自然 EOF 和 process.wait 完成后才执行接受策略。
  这不新增重试，不调用工具，不执行 Writer，也不意味着提供方输出可信。
- `provider_turn_state` 记录 not_observed/in_progress/completed/failed/interrupted/incomplete；
  `notification_policy` 记录 not_observed/nonfatal/boundary_violation；
  `stream_error_observed` 是布尔值。`terminal_event` 只记录实际看到的 turn 终态。
  exec 没有独立 interrupted JSONL 类型，不能从缺失终态推断提供方中断。
- 普通 collector 关闭时执行相同接受策略，不新增通知日志。诊断 opt-in 时只保留首条
  通知的既有受控投影，另以总计数和累积 policy 表明边界结论；首条投影不是整体结论。
  通知原文、prompt、路径和账号不落盘，不进入 ModelResult/失败报告。成功结果只新增
  warning_codes 仅含 codex_nonfatal_notification / codex_recovered_stream_error 固定摘要；
  result_metadata、probe、
  workflow 再以常量白名单过滤。通过校验的模型答案仍按原业务流程返回，不与警告混淆。
- 真实模型 ID 仍未知，不从请求模型或无 reroute 通知推断实际模型。
- 现有 no-tools 配置及事件检测不是 OS 回滚边界。此补丁没有扩大原生 Codex 工具权限。

## 回归要求

新矩阵覆盖真实合成子进程的自然结束标记、completed/failed/非零/无终态/超时，
普通与诊断模式一致、未知/混合非致命通知、独立数量上限、协议顺序、工具立即停止、
非法输出不入缓存、成功带安全 warning 和秘密哨兵不入报告。原 reader join/stop 次错、工作目录保护与缓存锁存
测试保留，并让正常协议流先产生真实终态再注入清理错误，而非依赖旧错误的提前终止。
