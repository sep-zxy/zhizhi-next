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
