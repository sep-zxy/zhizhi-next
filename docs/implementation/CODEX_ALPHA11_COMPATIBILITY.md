# Codex CLI alpha.11 精确兼容性审阅

2026-10-21 Windows 实测只到版本检查：已有官方 CLI 报告 `0.163.0-alpha.11`，
旧候选精确要求 `0.163.0-alpha.10.2`，因此拒绝；尚未进入登录检查或模型请求。
本修复不在 Windows 降级、安装或编译，不读取、复制或创建任何凭据。

## 固定官方来源

- [alpha.11 官方标签](https://github.com/openai/codex/releases/tag/rust-v0.163.0-alpha.11)
  解引用 commit `5425d3e625daa58eea3ad1de27c087eb3b2ffbd3`。
- [alpha.10.2 官方标签](https://github.com/openai/codex/releases/tag/rust-v0.163.0-alpha.10.2)
  解引用 commit `4ebc485ca321243727539187169425fcf972f2c4`。

两者历史分叉，不能凭版本号排序推断兼容。审阅直接比较两份完整文件的 Git blob，
不是仅依赖 GitHub compare 的共同祖先差异。12 个文件及各自 blob SHA 保存在
`deploy/windows-portable/codex-version-review.json`，7 个文件字节完全相同。

## 已核对的适配接口

- [exec CLI 参数](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/cli.rs)
  与 [exec 执行入口](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/lib.rs)
  均相同：既有 ignore-user-config、ephemeral、JSON、read-only sandbox、精确 model、
  output-schema 和 stdin 形态不变。
- [事件定义](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/exec_events.rs)、
  [JSONL 映射](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
  及对应官方测试均相同。仍要求单一结构化助手结果和 turn.completed；
  item.error 仍需审查，不能忽略模型重路由或策略警告。
- [登录状态实现](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/cli/src/login.rs)
  与 [工具构造](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/core/src/tools/spec_plan.rs)
  均相同。原 ChatGPT-only 状态检查、工具禁用和未知 item 拒绝继续保留。
- 其余5文件的直接 diff：CLI main 仅变更 daemon restart 的 feature 覆盖；
  config types/core config 只改 TUI 复制注释；feature config 只改 classifier 注释；
  [feature 注册表](https://github.com/openai/codex/blob/5425d3e625daa58eea3ad1de27c087eb3b2ffbd3/codex-rs/features/src/lib.rs)
  新增 `executor_capability_discovery_v2`，默认关闭，原禁用名称未变。

## 修复与验收边界

默认 pin 改为 alpha.11；精确允许集合仅 alpha.10.2 和 alpha.11。
旧版必须显式配置其完整版本；每次 `--version` 与所选 pin 不一致即拒绝，
不会探测后自动切换。未知未来版本、稳定版、前缀及通配均拒绝。
探针报告配置中的精确版本，不把默认值冒充显式旧 pin。

固定公开算术 smoke 仍仅为 `gpt-6.1-sol`，必须显式 `--model-smoke`；
不增加模型 fallback、账号权限、网络源或凭据复制。实际型号仍标 unknown，
不把 requested_model_id 当上游实际型号证明。历史 `codex-smoke/` 的固定旧包收据不改写，
新验收用新候选中的 `codex-diagnostic.cmd`。

本记录只建立静态兼容依据和 fixture 回归，未运行真实 CLI、登录或模型。
新 GitHub 候选经 Git 同步后，仍必须在 Windows 重新通过完整性、host-only preflight，
再在已批准范围执行固定模型请求；不能据本记录宣布模型或平台 E2E 通过。
