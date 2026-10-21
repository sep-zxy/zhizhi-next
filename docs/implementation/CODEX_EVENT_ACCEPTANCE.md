# Codex exec JSONL：协议事实与结果接受分离

本修正不改认证、订阅、固定模型、工具限制或进程清理。只修复适配器把所有
`item.completed/item.error` 当致命错误、立即终止 CLI，因而自行丢失真实终态的问题。
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

## 接受矩阵

| 协议/自然退出事实 | 独立接受策略 | 输出与缓存 |
| --- | --- | --- |
| completed + exit 0，无通知 | 既有 schema、秘密与输出上限校验 | 全部通过才可返回/缓存 |
| completed + exit 0，仅两种已审纯信息全文 | 仅已验证 alpha.11 可继续校验 | 同上 |
| completed + exit 0，有 unknown/关键配置/重路由/事件丢失通知 | blocked / CODEX_NOTIFICATION_REQUIRES_REVIEW | 隔离，不返回、不缓存 |
| 顶层 error 后 completed + exit 0 | blocked / CODEX_STREAM_ERROR_REQUIRES_REVIEW | 隔离；保留 completed 事实，不猜 auth/quota |
| turn.failed | 自然失败；既有受控错误摘要 | 无输出/缓存 |
| 任意自然非零退出 | 失败；若有正式 error/failed，保留其受控诊断，否则 CODEX_PROCESS_FAILED | 无输出/缓存 |
| EOF/exit 0 无终态 | incomplete / CODEX_INCOMPLETE_TURN | 无输出/缓存 |
| 本地超时/取消且未观察到终态 | interrupted；既有 deadline/cancel 错误 | 无输出/缓存 |
| 工具事件、非法协议、第二终态或终态后事件 | 立即拒绝并有界清理 | 无输出/缓存 |
| 任何清理未确认完成 | 既有主错优先，独立 cleanup 报告，runner 锁存 | 后续含成功缓存均不可访问 |

两个纯信息模板来自 `EXACT_NOTIFICATION_TEMPLATES`，必须完整字符串相等：
`transcript_v2_deprecation`（仅 TUI 显示配置弃用）和 `compaction_accuracy_notice`
（准确性提醒）。不 trim/lower、不按模式提示放行。旧 alpha.10.2 不继承许可。
其他精确模板也不是允许列表。混合/重复通知中任何拒绝项优先，不因顺序洗白。

## 有界读取与去敏

- 保留原总超时、stdout/stderr 总字节上限；error item 和顶层 error 各最多 16 条。
- startup 配置通知可以先于 thread.started；正常 turn/item 仍须遵守线程与回合顺序。
- 收到非致命 item 不再杀进程。只有自然 EOF 和 process.wait 完成后才执行接受策略。
  这不新增重试，不调用工具，不执行 Writer，也不意味着提供方输出可信。
- `provider_turn_state` 记录 not_observed/in_progress/completed/failed/interrupted/incomplete；
  `notification_policy` 记录 not_observed/informational/requires_review；
  `stream_error_observed` 是布尔值。`terminal_event` 只记录实际看到的 turn 终态。
  exec 没有独立 interrupted JSONL 类型，不能从缺失终态推断提供方中断。
- 普通 collector 关闭时执行相同接受策略，不新增通知日志。诊断 opt-in 时只保留首条
  通知的既有受控投影，另以总计数和累积 policy 表明混合拒绝；首条投影不是整体结论。
  原文、候选输出、prompt、路径和账号均不落盘，不进入 ModelResult/失败报告。
- 真实模型 ID 仍未知，不从请求模型或无 reroute 通知推断实际模型。
- 现有 no-tools 配置及事件检测不是 OS 回滚边界。此补丁没有扩大原生 Codex 工具权限。

## 回归要求

新矩阵覆盖真实合成子进程的自然结束标记、completed/failed/非零/无终态/超时，
普通与诊断模式一致、跨版本拒绝、混合通知、独立数量上限、协议顺序、工具立即停止、
输出不入缓存和秘密哨兵不入报告。原 reader join/stop 次错、工作目录保护与缓存锁存
测试保留，并让正常协议流先产生真实终态再注入清理错误，而非依赖旧错误的提前终止。
