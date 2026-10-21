# P15：Pi 主入口与现有 Codex 订阅薄桥

## 只读核验结论（2026-10-21）

基线 `653d6974a65fa7715097f8be879bdc0a8d73e8ad` 中，Pi 1.3.0 的包、原生启动器、10 个领域工具及 keep/discard/review TUI 命令已存在。`portable/launch-interactive.mjs` 仍检查 Pi 自身的 OAuth store，`subscription-policy.ts` 最终调用 Pi 内置 provider；**没有从 Pi 交互进入 CodexModelRunner 的链**。后台 `adapter=codex_cli` 不自动填补这个入口。

- `test/pi-runtime.test.ts` 启动真实 Pi，但只发送 `get_state` 或验证空 auth store，零模型调用。
- `agent/pi_tui_acceptance.py` 使用真实 TUI、HTTP、SQLite/Git，身份和键盘均为合成 fixture，零模型调用；不能充当真人批准或订阅闭环。
- `extension.test.ts` 多数注册表与 mock fetch 检查；工具存在不代表 Pi 曾通过实际订阅选择并执行它。
- 现有只读工具只按 entry_id 读取最新条目；模型工具没有独立自由学习会话命令。此桥不把尚未接入的 CLI family 或规划工具中心称作已完成。
- 默认无身份、本机 fixture 和 owner 基础启动均不自动激活 Pi。

P15 属于 V1 主入口，不是可延后的工具中心。补齐的是薄 Provider，不 fork Pi，不引入通用代理平台或公共模型端点。

## 固定版本依据

按官方 **v1.3.0** 而非 main 核对：

- [原生 Provider 扩展](https://raw.githubusercontent.com/earendil-works/pi/v1.3.0/packages/coding-agent/docs/custom-provider.md)：工厂注册完整 Provider，使用 stream/streamSimple，输入是规范化 TranscriptContext。
- [Provider 接口](https://raw.githubusercontent.com/earendil-works/pi/v1.3.0/packages/ai/src/models.ts)：原生 ambient auth 可以返回无秘密配置状态；不需创建 OAuth 或伪造 API key。
- [消息与事件类型](https://raw.githubusercontent.com/earendil-works/pi/v1.3.0/packages/ai/src/types.ts)：正确映射文本、单工具建议和终态；实际型号证明缺失时不制造 responseModel。
- [RPC 生命周期](https://raw.githubusercontent.com/earendil-works/pi/v1.3.0/packages/coding-agent/docs/rpc.md)：prompt 接受不代表完成，应等待 agent_settled 并检查最终消息和领域结果。

原生 ambient auth 使用 Pi 的 apiKey 类型槽是官方无密钥本地 provider 接口分类，不代表存在 API key、购买按量 API 或以常量冒充凭据。真实订阅认证仍由 Codex 官方 CLI 的 login status 维护。

## 架构与权限

Pi TUI / 会话 / 领域工具循环 → 私有子进程 JSONL → ModelTask → 现有 CodexModelRunner → 严格结构化结果 → Pi 验参 → 原 DomainClient → 真实领域账本。

- Provider ID `zhizhi-codex-cli`；唯一模型 `gpt-6.1-sol`；Codex 精确版本仍按已审核 pin。
- 每轮结果只能是 text 或一个工具建议。Codex 不执行该工具，Pi 用既有可信 TypeBox schema 和服务端身份执行。
- Python 读取包内 `model/pi_tools_v1.json`；请求不能指定 schema、owner/device/deployment、模型、可执行文件、URL、工具权限或新身份。
- 领域 agent 与 TUI 用户身份保持独立。模型不能调用 keep/discard/approve/apply/publish；重大确认仍走现有 TUI nonce/hash/revision，Writer 不变。
- Pi 不把系统提示中的 cwd、路径、隐藏推理或身份变量送入模型。模型上下文不授予权限。
- 现有 Codex 只读沙箱、禁用 shell/apps/MCP 等配置及原生工具事件拒绝不放宽。事件拒绝不能撤销已经发生的工具副作用，也不是完整 OS 沙箱。
- 不读取或复制 Codex auth 文件；不注册新 OAuth；不回退 Pi 内置 OpenAI provider 或收费 API。
- 对缺服务/身份/激活保持 blocked。hello ready 只是可信配置存在，不是订阅认证成功，更不是 E2E 通过。

## 私有 JSONL v1

固定入口 `python -B -s -m ahadiff.knowledge_platform.model.pi_bridge --owner-acceptance`。无任意参数，portable root 与 activation 由受信任 factory 验证；运行标志不是用户授权。默认入口不创建文件、身份或激活记录。

每条 JSON 对象以 LF 结束，支持 CRLF；不按 Unicode 换行拆帧。重复 key、NaN、未知字段拒绝。单请求最多 256000 UTF-8 字节，单响应最多 1048576 字节。

公共请求头：`{version:1,id:规范UUID,type:...}`。

1. `hello` 无额外字段。响应 `status=ready` 时包含 capabilities：provider/model、tools（name/parameters）、limits。
2. `turn` 必须且只能附 `run_id:UUID`、`turn:正整数`、`messages:数组`。
3. `cancel` 必须且只能附 `request_id:正在执行的请求UUID`。回 `cancel_requested`，原 turn 最终 cancelled。目标不在运行则 blocked / BRIDGE_REQUEST_NOT_RUNNING。

messages 不接受 Pi 原始任意对象，只接受：

- `{role:'user',text}`
- `{role:'assistant',text}`
- `{role:'assistant',tool_call:{id:UUID,name,arguments}}`
- `{role:'tool',tool_call_id:UUID,name,result:{data,task_id,trace_id,data_classification,classification_labels?}}`，data 为领域对象或列表，完整分类列表如存在必须保留。
- 失败工具仅 `{role:'tool',tool_call_id:UUID,name,error_code:'DOMAIN_REQUEST_FAILED'}`，不传原始异常。

工具 ID 必须曾由此桥签发，名称与参数原样一致；下轮必须在前轮前缀后增加该工具回执，不能偷偷改历史或插入新用户指令。新用户意图应另起 run。旧进程未知工具回执默认拒绝，不能用聊天历史恢复授权；需要重新从领域读取。

请求 ID 同内容重放只返回已有结果，改内容拒绝；未完成同 ID 不重复调用模型。输出头为 version/id/type=response/status，turn 完成响应包含 task_id、requested_model_id、actual_model_id、fixture、usage、output、error_code。

成功 output：`{type:'text',text}` 或 `{type:'tool_call',id:服务端UUID,name,arguments}`。失败/等待 output=null。实际型号没有可信元数据时为 null。usage 只投影有界非负整数，不转交任意提供方字段。

这里 task_id 是 ModelTask 调用关联 ID，不等于已创建领域持久任务；真正跨重启的任务 ID 来自工具返回的领域 envelope.task_id，并由 zhizhi_job_status 查询。对话回答结束也不等于正式知识提交。

## 可信工厂接口

`PiBridge(runner, PiBridgeConfig(...), authorize=request_validator, reserve_attempt=durable_reservation)`。

- `PiBridgeConfig` 由包内固定工厂持有 owner/device/deployment、工具子集、分类和预算，enabled 默认 false。
- authorize(request) 必须重新验证该部署的明确输入授权，不得无条件允许用户可提交的任意 transcript。
- reserve_attempt(ModelTask) 必须在模型执行前持久消费 activation 的预算。失败或异常拒绝，不因同一 CLI 命令重跑而清零。
- ModelTask 固定 purpose=answer、schema_name=pi_domain_turn_v1；payload 为 transcript 与可信 tools 列表，应用 instructions 来自固定源码。
- Generic adapter 可以有本机进程内幂等缓存，但不能代替 acceptance 工厂的持久 attempt 限额与未知结果处理。进程崩溃后未知请求不自动再次外发。

硬上限：每 run 4 次模型 / 3 次工具，每进程 16 次模型，每模型 180 秒；工厂只能收窄。最后可用模型回合只允许 text。调用失败、无效结果、超时、取消都消费已保留次数，零自动重试。取消先传播给 runner，沿现有 Codex owned Job / 进程组回收；父 Pi 仍有最终退出期限。

同一 run 不允许用新工具 ID 再执行相同名称和参数。写工具（intake/propose/radar）返回失败或未知后，该 run 只允许只读对账或文字说明，不能由模型猜测是否成功后重新写入。

相同请求 ID 的精确缓存重放也必须重新核验当前授权；来源升密、身份撤回或配置关闭时不返回旧模型内容。
该只读核验不占用新预算、不重新执行模型或工具，幂等事实不能代替持续读取许可。

## 首个真实验收槽（仍待单独批准和新版候选）

不是对原五阶段 activation 自动扩权。新 manifest 版本必须绑定候选 source/release、独立根、现有同链源条目 revision/hash、明确固定提示词、唯一模型/CLI 版本和预算；旧激活不接受新 capability。

固定提示词全文（无末尾换行）：

合成 Pi 验收 v1：请检索“正式正文与原始来源证据”，只查询当前授权知识目录，报告实际匹配条目；不要收藏、批准、修改或发布。

仅允许 zhizhi_search，参数必须为 `{query:'正式正文与原始来源证据',limit:5}`。最多 2 次模型、1 次工具，activation 总限额也为 2 次模型。第一轮必须真实返回该工具建议，由 Pi 执行真实领域查询；第二轮只允许根据核验过的安全结果回答。未选择工具不得由测试替模型补调。空结果如实报告，但不能冒充已覆盖同链有内容的检索旅程。

首槽是 P15 的最小真实模型/工具链验证，不代替后续预览→本人保留→独立复核→Writer→重启 task 查询旅程，也不代表自由学习 CLI family 已接入。正式启用时须由 root 取得精确用户批准；本地 fixture flag 不能提供这份授权。

## 单独的只读检索边界

`model.pi_search_boundary.PiSearchBoundary` 只接受工厂传入的最小 agent 能力。
此 token 不注册到通用 `TokenAuthenticator`，默认入口不装配此中间件。
仅允许精确 loopback Host、无 Origin/Cookie 的固定 GET search 和两个固定查询参数；
重复头、其他路由、非空正文、WebSocket 全部拒绝，不拥有浏览器批准或 Writer 权限。
非此 token 的原应用认证保持不变。

检索前通过工厂回调持久保留唯一工具调用，回调还须核验第一轮模型的真实工具建议签封。
由原 SearchService 对工厂独立核验的单条正式来源精确版本执行真实检索，不索引其他账本内容。
版本或正文 hash 改变、来源不可读、签封失败都关闭返回，已消耗预算不自动补发。
安全结果签封成功后才返回真实 trace、task=null、entry/revision/hash；空匹配不会伪造成成功匹配。
中间件自身不生成凭据、不激活工厂、不证明 Windows 或真实模型通过。

## 验收分层与分工

1. Python：严格结构、scope覆盖拒绝、修改历史/未知工具/分类、预算、幂等、等待、超时取消及无秘密日志。
2. 原 Pi 工人：实际锁版本 provider 注册、消息规范化、完整事件、禁原 provider 回退、工具 schema 对齐；先用 synthetic transport 驱动真实 Pi RPC，不用 get_state 冒充模型调用。
3. 打包：GitHub 原生 JS/Python 资产、固定启动器、manifest 与已激活身份读取、fail-closed，默认不启用；仅 GitHub 生产构建。
4. Windows：在获准空隔离账本用新候选和明确授权，实际 Pi prompt → Codex → 领域工具 → 再次 Codex → agent_settled；核真实 task / trace / hash，不能使用 fixture success。实际 TUI 的人工批准另走独立旅程。

截至本文件提交，代码核心与 fixture 可测；Provider/包装/新 activation/真实 Windows 验收按各自提交与实测报告，不以此合同声称全部完成。
