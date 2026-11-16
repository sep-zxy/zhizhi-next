# Codex CLI 登录复用适配（显式启用）

本适配属于原 `impl/agent` 实现线，保留统一 `ModelRunner.run(ModelTask) -> ModelResult` 契约与 Pi 默认。没有为应用增加 HTTP 模型代理，也不会读取、复制、导出认证文件或接收 API key。只有已安装的官方 Codex CLI 自己使用和维护其现有 ChatGPT 登录。

## 启用契约

只有明确配置 `adapter: "codex_cli"`、`enabled: true`、`reuse_existing_login: true`、一条已获准 `openai` 路由才执行。默认版本精确固定为已审阅的 `0.163.0-alpha.11`；显式配置也允许已审阅的 `0.163.0-alpha.10.2`，但每次执行必须与配置完全一致，不自动侦测或切换版本。其他版本先阻断，不接受通配或前缀匹配。生产配置不能启用 fixture transport。来源比对见 [alpha.11 兼容性记录](CODEX_ALPHA11_COMPATIBILITY.md)，静态审阅不等同于真实运行通过。

```json
{
  "enabled": true,
  "adapter": "codex_cli",
  "reuse_existing_login": true,
  "executable": ["C:\\path-to-existing-official-cli\\codex.exe"],
  "expected_version": "0.163.0-alpha.11",
  "codex_home": "C:\\path-to-existing-user-home\\.codex",
  "routes": [{
    "provider_id": "openai",
    "model_id": "gpt-6.1-sol",
    "account_qualified": true,
    "allowed_classifications": ["public", "personal"]
  }],
  "timeout_seconds": 120,
  "max_input_bytes": 512000,
  "max_output_bytes": 1048576,
  "quota_backoff_seconds": 3600
}
```

路径是占位符，必须由部署方选择已有 CLI 与登录目录；不创建或复制登录。`codex_home` 可省略，省略时官方 CLI 只使用子进程现有的 HOME/USERPROFILE/CODEX_HOME。便携启动器原本隔离 HOME，因此复用包外现有登录通常需要显式设置该路径。此字段是仅供官方 CLI 访问原身份目录的任务级例外，不改变通用便携路径检查，不授权应用直接写包外文件。

官方 CLI 可能刷新原登录；其身份存储仍在原机器，绝不传到云端。会话不持久化，日志和 SQLite 路径放在包内临时目录并在调用结束后清理。CLI 自身是否更新认证/目录缓存由其实现决定，不能声称“CLI 对身份目录零写入”。当前运行器只做进程内串行，不承诺与用户同时打开的其他 Codex 进程跨进程互斥。

## 调用和结果

1. 先验证分类、来源去向、敏感模式、输入体积、Schema 与 deadline，不通过则不启动子进程。
2. 仅执行官方 `--version` 和 `login status`。登录状态必须精确表示 ChatGPT；API-key 或未知状态拒绝。不会调用 login/start、读取 auth.json 或制造凭据。
3. `exec --ignore-user-config --ephemeral --json --sandbox read-only --model <exact-id> --output-schema <temporary-schema> -`。stdin 承载正文，不放到进程参数或日志。
4. 显式要求 `forced_login_method="chatgpt"`、`model_provider="openai"`、`approval_policy="never"`，清除环境中的 API keys、代理、Node 注入、API endpoint overrides 等非白名单变量。不切换 provider/model，不自动 JSON 修复或自动重新发起请求。
5. 禁用 shell、unified exec、shell snapshot、apps、plugins、hooks、memories、multi-agent、skill MCP 安装、浏览器、电脑、图像、JS REPL 等功能；关闭 Web search、项目文档输入和计划/用户提问工具。系统/组织策略仍由官方 CLI 执行，不绕过 managed restrictions。
6. 结构化输出使用一个固定 `{ "result_json": "..." }` envelope。内层字符串还必须是完整单个 JSON 对象并通过原任务 Schema。这样支持现有任务的 optional fields 和本地 refs，而不会为适应提供方 strict-schema 子集放松原 Schema。重复 key、NaN、围栏、额外字段与敏感模式均拒绝。
7. 必须有正确 JSONL 顺序、最终助手对象、`turn.completed` 和进程 exit 0。失败、缺终态、非零退出和未知事件不能视为成功。

## 不夸大隔离或模型证据

官方 exec 没有通用 `tool_choice=none`。read-only 不等于无工具能力。适配禁用已知命令/应用能力并检查所有 item 事件；除 completed/error 非致命通知外，任何非 agent_message/reasoning item（包括命令、文件变更、MCP、协作、搜索、计划和未知工具）立即阻断并结束 CLI。事件可能在工具已经开始后才发出，所以这只是检测阻断，不能回滚已发生副作用，也不替代 OS 沙箱。工具隔离不能靠提示词证明。

exec JSONL 的标准事件没有可信的实际 model 字段。`requested_model_id` 记录传给 CLI 的精确模型；`model_id=null` 明确表示实际型号未知，绝不复制模型正文的自报值来补空。`runtime_version` 带 `codex-cli` 前缀。provider_id 是显式选定的官方 OpenAI 路由，不是从模型回答推断。调用者不得把 requested 当 actual。

总 stdout + stderr 字节数（含所有 JSONL 事件）受限，单行也受限；超时和取消结束进程。POSIX 结束自有进程组，Windows 使用本次调用独有的 kill-on-close Job；先绑定等待 stdin 放行的宿主，再允许它启动 CLI，父进程先退出也能清理剩余后代。Windows 路径尚须实际验证，不能用 Linux fixture 测试代替。stderr 不落入应用审计；错误记录固定代码及受控诊断，usage 只保留白名单数值。

受控诊断为 `ModelResult.failure`：固定阶段、固定类别、固定摘要、允许的错误事件类型，以及实际观察到的自然退出码。提供方原始错误不保存、不回显；只在内存中区分网络/结构化 Schema/请求模型/沙箱/配置等类别。未知错误保持 `provider_reported_failure`，不能把分类当根因证明。非致命 error item 继续有界收流，不替代正式终态；未知告警不自动拒绝合法答案。只有工具/协议违约、超时取消等会提前终止进程树，此时不将清理产生的终止码当提供方退出。登录、版本和自然非零退出的已观察退出码可以保留。

CLI 未提供本适配可核验的上游 `max_output_tokens` 开关。任务的 token 上限在请求提示里表达，并在完成后按 CLI usage 检查；它不是提供方硬预算。调用前有输入字节限制，调用中有 wall-clock/总输出字节限制，超 token 的结果拒绝，但已消耗的订阅额度不能撤回。额度错误进入冷却期，不改投付费 API。

## 验证层级和来源

- 云端只跑 fixture 子进程和源码/接口测试，无账号、无网络模型调用、无真实认证。fixture 结果始终 `fixture=true`。
- Windows 已有独立官方 CLI smoke 不等同于本适配或整个平台 E2E；本适配的 Windows 实机、真实结构化输出、真实订阅/领域完整旅程仍需单独验收。
- 官方接口参考：[Non-interactive mode](https://developers.openai.com/codex/noninteractive/)、[Configuration reference](https://developers.openai.com/codex/config-reference/)、[Exec event types](https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs)、[Tool construction](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/spec_plan.rs)。云端本地 CLI 0.159.2 的 help 用于核对接口形态，不用来冒充目标 0.163.0-alpha.10.2 的真实执行结果。
