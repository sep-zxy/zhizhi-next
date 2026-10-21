# Codex 去敏进程诊断

## 默认零模型调用

模块：`ahadiff.knowledge_platform.model.diagnostic_probe`。

Windows 由 GitHub 产物中的 Python 运行，PowerShell 示例：

```powershell
$ErrorActionPreference = 'Stop'
& '<包内 Python>' -B -s -m ahadiff.knowledge_platform.model.diagnostic_probe `
  --codex-exe '<已有官方 Codex CLI 的绝对路径>' `
  --codex-home '<原机器已有 Codex 登录目录的绝对路径>'
```

不带 `--model-smoke` 时，只通过适配器的同一进程/环境/Job 路径运行官方 `--version` 和 `login status`。不调用模型、不新登录、不读取认证文件内容、不复制认证、不创建新身份或 API fallback。CLI 自身可能维护原登录目录；不声称其绝不写目录。

输出 stdout 为 JSON，退出码 0 表示对应诊断步骤通过，2 表示未通过。`host_preflight_passed` 只表示版本、已有 ChatGPT 登录与这两次进程执行通过，绝不等于模型或平台 E2E 通过。测试接口若使用 fixture，则保留 `fixture:true`。

## 已授权的固定公开模型复测

同一命令显式增加 `--model-smoke` 才执行一次固定公开算术题 2+2，模型固定为 `gpt-6.1-sol`，命令行探针的默认目标版本精确固定为 `0.163.0-alpha.11`。不接受其他 prompt、资料、schema、模型或 provider 参数。该选项不放进 CI 或自动诊断链。`expected_cli_version` 报告实际使用的精确配置值；版本不匹配时不进入登录检查或模型执行。更新依据见 [alpha.11 兼容性记录](CODEX_ALPHA11_COMPATIBILITY.md)。

模型调用仍完整通过正式 `CodexModelRunner` 的分类、已有登录、版本、Schema、工具拒绝、字节/时间与进程清理限制。无新凭据、无放宽安全检查、无付费回退。JSON 结果只报告是否匹配固定答案，不输出模型原文。

真实已有同范围公开 smoke 的授权由任务协调确认；这不授权其他资料出站或新登录。

## 报告白名单

- 版本：仅预期固定版本与 verified/mismatch/not_checked。
- 登录：仅 chatgpt/api_key_rejected/not_authenticated/unrecognized/not_checked。
- 进程阶段：version/authentication/execution。
- 步骤：process_spawn、helper_started/process_started、job_create、job_created、job_assign、job_assigned、stdin_released、process_exited、cleanup、cleanup_complete。
- 步骤与总耗时、实际观察到的自然退出码、固定异常类型、数值 errno/winerror。
- stdout/stderr 字节数；固定事件类型计数（未知类型合为 unknown）；观察到的终态事件。
- ModelResult 的固定状态、错误码、受控诊断与白名单用量；requested model 与 actual unknown 分离。

不保存原始 stdout/stderr、错误原文、模型输出、prompt、命令行、路径、账户标识、PID、key、token 或认证数据。CLI 运行失败时也不输出异常文本或 traceback。错误事件到达时进程若尚未自然退出，退出码为 null；不能把适配器清理造成的终止码当成根因。

## 证据层级

本地 fixture 验证 default 无 exec、显式固定请求、错误分流、输出去敏、错误事件计数和模拟 Windows Job 创建/绑定失败。模拟 OS 分支不是 Windows 实机测试。真实 Windows 报告到达前，`PROVIDER_FAILURE` 根因仍未确认；任何主流程交付结论仍要求完整端到端通过。

## 固定版本的非致命通知

已核对官方固定版本 `rust-v0.163.0-alpha.10.2`：

- [事件定义](https://raw.githubusercontent.com/openai/codex/rust-v0.163.0-alpha.10.2/codex-rs/exec/src/exec_events.rs) 将 item error 与顶层致命 error 区分。
- [JSONL 映射](https://raw.githubusercontent.com/openai/codex/rust-v0.163.0-alpha.10.2/codex-rs/exec/src/event_processor_with_jsonl_output.rs) 将配置/运行警告、弃用通知、模型重路由转成 `item.completed` 内的 `type=error`，CLI 本身可能继续运行。
- [官方测试](https://raw.githubusercontent.com/openai/codex/rust-v0.163.0-alpha.10.2/codex-rs/exec/src/event_processor_with_jsonl_output_tests.rs) 明确验证运行警告是非致命 item。

适配器仍拒绝这类未经复核的通知，尤其不能忽略模型重路由或失效安全配置；没有扩大工具、模型、权限或秘密放行。现在返回 `blocked / CODEX_NOTIFICATION_REQUIRES_REVIEW` 与固定摘要 `cli_notification_requires_review`，不再把它一律误报为 `PROVIDER_FAILURE`。

probe 的事件计数同时记录外层 `item.completed` 与内层 `item.error`；后者不是提供方终态，`terminal_event` 不再据此伪造。顶层 `error`、`turn.failed` 仍保持失败。所有消息原文继续丢弃，即使后续出现合法答案也不会把被拒绝的调用标成成功。

这是已确认的协议诊断修正，不是 Windows 根因结论。旧报告未记录事件类型，必须等待真实 Windows probe 才能判断当时是否走了该通知路径。

## alpha.11 通知诊断（初版 v1，当前 v2 见下节，均全部阻断）

Windows 已见 `item.error`，但未保存原文，无法回溯确认具体原因。新版仅在显式固定公开
`--model-smoke` 中收集通知摘要；默认 host-only 不发模型请求，常规模型执行默认不收集。
无新重试、fallback、忽略警告或成功条件。通知后即停止，后续正确答案不计成功。

官方固定 commit `5425d3e625daa58eea3ad1de27c087eb3b2ffbd3`：

- [exec JSONL 映射](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
  L409–506 将 ConfigWarning、Warning、DeprecationNotice、ModelRerouted 压成同一种 error item。
- [ErrorItem 定义](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/exec_events.rs#L310-L312)
  只有 message，没有原通知类别或错误码。因此下述 code 是本地固定规则 ID，不冒充上游码。
- [官方配置警告](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/core/src/config/mod.rs)
  L2317、3429、4865–4899、5024：要求覆盖、hook 信任绕过、凭据遮罩失效三种原因。
- [官方 feature 警告](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/features/src/lib.rs)
  L815–827、2143：弃用和实验功能；[exec 流丢失警告](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/lib.rs#L1668-L1670)。

初版规则在 `notification_diagnostic` 中硬编码，`classifier_version=1`，固定 rule_id 前缀
`codex_exec_notice_v1:`，后缀与 code 相同。完整消息仅在已有 JSONL 内存限额内短暂解析；
分类最多检查前 8192 个 UTF-8 字节，只返回实际输入字节数、检查字节数和截断事实。
绝不返回原文/片段/哈希、任意上游码、模型名、账号、路径、URL 或凭据。

固定规则（大小写归一，仅诊断提示，不是允许列表）：

- `model_reroute_notice`：前缀 `model rerouted:`。
- `credential_masking_sandbox_disabled` / `credential_masking_proxy_disabled` /
  `credential_masking_environment_conflict`：官方固定凭据遮罩警告前缀。
- `required_configuration_override`：固定 `Configured value for ` 前缀与
  `is disallowed by requirements; falling back` 片段同时存在。
- `unstable_features_notice`：固定 `Under-development features enabled:` 前缀。
- `event_stream_loss_notice`：固定 in-process app-server 丢事件前缀。
- `hook_trust_bypass_notice`：固定 dangerously-bypass-hook-trust 警告前缀。
- `invalid_global_instructions_notice`：官方 JSONL 测试中的固定前缀。
- `deprecation_text_notice`：包含固定 ` is deprecated` 片段，证据较弱。
- 其他/非字符串均 `unknown_notification`，不得擅自推定无害。

所有类别均标 `evidence=bounded_text_pattern_hint` 与
`upstream_kind=not_preserved_by_exec_jsonl`。恶意文本可夹入相同关键词，所以类别仅帮助
下一步静态调查，不能证明真正来源或改变 `CODEX_NOTIFICATION_REQUIRES_REVIEW` 阻断。
下一次真实固定 smoke 必须仍由协调方按既有授权安排；这些离线测试不是模型 E2E。

## 主错误与清理失败分离

一次 Windows 固定 smoke 的内层记录了 `item.error`，外层却变成
`CODEX_HOST_INVALID`，且临时目录残留。这与旧 `TemporaryDirectory.__exit__`
覆盖主异常的源码路径相容，但没有保存当时清理异常，因此不能据此宣布实际根因。
事后只读检查没有发现 readonly/reparse，也不能反证当时不存在短暂占用。

新版取消隐式临时目录 finalizer，显式管理**本次调用创建**的独立目录：

- 先终止并回收所属进程，再检查本次目录和全部祖先/后代；任何链接、Windows
  reparse 属性、特殊文件、检查失败或超出有限枚举上限均拒绝删除。
- 仅普通 `shutil.rmtree`，无 `chmod`、ACL 修改、强删、忽略错误或递归查找旧残留。
  仅已知 sharing/busy/nonempty 错误可等待 100ms 后重试一次。权限/未知错误不重试。
- `processes[*].failure_type/errno/winerror` 保持原执行错误；新增独立 `cleanup`
  报告的 resource/status/attempts/errors/recovery，只有固定类别和数字 errno/winerror，
  不含异常文本、文件名、路径、argv、通知原文或秘密。
- 主异常（包括通知阻断、认证错误、超时/取消）不被清理异常替换。没有主异常但清理
  不完整时返回 `blocked / CODEX_CLEANUP_FAILED`，不是成功或可缓存答案。
- 任一清理未确认完成，该 runner 后续调用（包括已排队调用/缓存）返回
  `CODEX_CLEANUP_PENDING`，不再启动 CLI。进程清理未知时不尝试删除工作目录。

此策略不是抵御同用户进程竞态的 OS 沙箱。清理失败残留须由协调方在精确目录、
进程/句柄已释放和路径属性重新核验后单独处理；当前探针没有“强制清理历史目录”入口。
不能为清理问题重跑模型，也不能通过换目录重置已消耗的验收预算。

144 字节、未截断的 `unknown_notification` 不足以恢复上游类型。只有完整消息与已审
官方公开静态模板**精确相同**时，才可增补固定模板 ID；其他输入保持 unknown，绝不输出
未知原文或其哈希，也不把模板命中当成忽略通知/换模型的许可。真实下一次固定请求仍由
主协调按获准范围安排，不由代码诊断自动重试。

### 清理路径补充

reader cancel/join 与 owned stop 现在同属一个受控清理段。join 超时/取消/异常先锁存
未完成状态，仍尝试有界 stop；再次取消分别记录，不会丢掉原通知或误删工作目录。
`errors[*].operation` 仅为固定 `reader_join`、`owned_stop`、`job_setup_handle_close`。
Windows `CloseHandle` 返回 false 必须立即记录对应 last error，不能清掉尚未确认关闭的
job handle；创建/绑定与 handle-close 同时失败时，创建/绑定原错优先，次错单列。
这一修复由合成取消和 ABI 返回值注入覆盖，不证明当次 Windows 残留由它造成。

### 分类器 v2：精确公开模板

当前输出 `classifier_version=2`、`codex_exec_notice_v2:`；原 v1 的弱模式提示继续标为
`bounded_text_pattern_hint`。新增 7 个全文等值诊断 ID，命中时标为
`exact_public_template_text`，但上游类别仍标记未由 JSONL 保留。不得 trim、lower、
前缀匹配、动态模型名展开、按字节数判断或用截断文本宣称精确命中。

完整公开文本、Git blob SHA、固定 commit、行号和官方链接位于
`deploy/windows-portable/codex-notification-review.json`，与运行时代码有等值回归：

- model_metadata_missing_fixed_model：固定 gpt-6.1-sol metadata 缺失模板。
- service_tier_priority_unsupported_fixed_model：仅固定 priority/gpt-6.1-sol 公开展开。
- code_mode_unsupported_fixed_model：固定 gpt-6.1-sol Code Mode metadata 提示。
- stop_hook_empty_continuation、compaction_accuracy_notice：公开固定全文。
- guardian_thread_context_deprecation、transcript_v2_deprecation：summary 加完整 details。

这些候选均不是已观察到的 144 字节通知，目录也不是全部通知的穷举。特别是 Guardian
的 details 单段恰为 144 字节，但 exec 实际全文含 summary/括号为 212 字节，不能误判。
WebSocket fallback、transcript 保存失败及 Code Mode host 失败含动态错误后缀，不作为
精确规则、不输出后缀。后续只有获准的固定公开输入才能产生新的诊断，仍仅在内存匹配
已审模板并输出固定 ID；若未命中，根因继续明确未知，不以原文/哈希输出换取结论。
