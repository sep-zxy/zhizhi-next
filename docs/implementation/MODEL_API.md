# ModelRunner 冻结接口 v1

导入路径：`ahadiff.knowledge_platform.model`。全部模型调用（包括既有知枝）必须通过 `await runner.run(task)`，不得未就绪时回退旧 API。

`ModelTask` 为 frozen dataclass，关键字参数：
- `task_id: str`、`purpose: Literal['preview','generate','review','answer']`
- `input_refs: tuple[str, ...]`（source/entry/outcome ID，仅标识，不自动读取路径）
- `input_classification: Literal['public','personal','third_party_private','employer','secret']`
- `allowed_provider_ids: tuple[str, ...]`（明确数据去向白名单）
- `schema_name: str`、`output_schema: dict[str, Any]`（JSON Schema）
- `payload: dict[str, Any]`（已裁剪且获准提交的输入；正文为不可信资料）
- `dedupe_key: str`
- `max_output_tokens: int = 4096`、`deadline: datetime | None = None`（aware UTC）
- `trace_id: str = ''`、`instructions: str = ''`（仅应用代码提供，不能放网页指令）

`ModelResult` 为 frozen dataclass：`task_id`、`status`；`output: dict[str, Any] | None = None`；`provider_id/model_id: str | None`；`runtime_version: str = '1.3.0'`；`input_fingerprint/result_hash: str | None`；`usage: dict[str, Any]`；`error_code: str | None`；`retry_after: datetime | None`；`fixture: bool = False`。状态为 `succeeded/waiting_auth/waiting_quota/blocked/invalid_output/failed/cancelled`。只有 `succeeded` 且 `output is not None` 才可继续依赖步骤。`invalid_output` 映射领域 task.state=partial、error.code=invalid_output，后续建立独立复核任务，不等于任务成功。

`ModelRunner` 为 Protocol：`async def run(self, task: ModelTask) -> ModelResult`。`DisabledModelRunner()` 返回 blocked，绝不网络调用。`FixtureModelRunner(outputs, *, development=True)` 只用于显式开发测试，返回 fixture=true，不能用于真实 E2E 通过证据。

预览示例：构造 purpose='preview'，schema_name='intake_preview_v1'，output_schema 要求对象字段 summary 等，payload={'source_text': text, 'url': url}，input_refs=(intake_id,)，allowed_provider_ids 取管理员已批准配置。模型不会自行收藏材料。无 runner 就保留确定性解析结果与等待状态。

Pi 路由使用 `PiModelRunner(config)`，配置 `PiRunnerConfig` 和 `SubscriptionRoute`。只支持实际 Pi v1.3.0 正式 provider；OpenAI 必须已配置 OAuth，不接受 API key；Z.AI 只能指定 Coding Plan provider。账号/地区/数据授权未就绪返回 blocked/waiting_auth。工具隔离宿主，CLI stdin/stdout RPC，不提供通用 HTTP 模型代理。每个任务独立会话；review 不复用 generator 上下文；默认最多 1 次 JSON 修复。取消退出子进程；不因 prompt accepted 认定成功，等待 agent_settled。

领域任务持久化由调用方/装配层的 DomainService 负责。runner 本身不创建另一份数据库。调用前持久化 task_id/input_fingerprint，调用后更新等待/终态；已等待额度的任务仅在 retry_after 到期或人工恢复后重试。进程内串行；同一个 agent_dir 使用 portalocker OS 锁跨进程序列化认证刷新和整个调用。多机器不能共享正在刷新的认证目录，必须由部署单宿主调度。内存额度冷却不能替代持久化任务的 retry_after。

依赖：Python `jsonschema>=4.23,<5` 和原项目 portalocker；Pi extension 固定 `@earendil-works/pi-coding-agent=1.3.0`、`@earendil-works/pi-ai=1.3.0`，TypeBox 使用官方包导出。根 Python 依赖与 lockfile 由装配层管理。测试范围及真实订阅阻塞见 extension README。

## 生产配置

导出 `build_runner_from_config(config: dict | None) -> ModelRunner`，由主 CLI 显式读取受保护配置后传入；缺失/未启用返回 DisabledModelRunner。字段：enabled（必须 true）；agent_dir（独立 Pi 凭证目录）；guard_extension（绝对路径 integrations/pi-zhizhi/src/model-guard.ts）；executable（argv 数组，默认 ['pi']）；routes（SubscriptionRoute 字段）；timeout_seconds=120、max_json_repairs=1、max_input_bytes=512000、max_output_bytes=1048576、quota_backoff_seconds=3600；provider_env_names（仅可列 ZAI_API_KEY / ZAI_CODING_CN_API_KEY，引用部署环境，不在 JSON 保存值）。不允许 OPENAI_API_KEY、不允许任意 env、不允许 metered fallback。

SubscriptionRoute keyword 字段：provider_id（openai / zai / zai-coding-cn）、model_id（必须管理员给出实际目录精确ID，不硬编码过期默认）；account_qualified=false；allowed_classifications=('public','personal')；china_terms_verified=false。中国版必须现场核验后才 true。每任务还必须单独通过 allowed_provider_ids。未获准第三方私人/雇主资料不应扩大 allowed_classifications。

运行前检查 Pi --version 精确1.3.0；正式 pi auth check --json --no-refresh 验证 authType。OpenAI 必须 oauth，禁用 API-key 路线；ZAI 必须 api_key 且 get_state 的实际 baseUrl 是内置 Coding Plan 路径。自定义 models.json 一律拒绝。每个任务临时空工作目录，无会话落盘、默认工具/MCP/skills/context discovery关闭，仅加载维护者审核的输出限制扩展。OS 隔离与外连规则仍须部署，CLI 白名单不替代沙箱。

## 生成与复核

### 匿名外部正文的原创摘要资格（与 RAW 权利分离）

新原创知识摘要或已公开合格条目的增量可以使用领域 `read_external_document(source_id, *, actor)` 返回的独立正文。无此可信对象、存在学习原话、显式 private 意图或既有 private 目标时不走自动公开资格。该正文仅记录匿名访问事实，分类仍 personal、隐私未评估；不修改 RAW 的 private/rights=unknown，不视为许可。

生成输入只包含这些正文、已公开正文及核验过的公开目录，不包含 intake envelope、用户附言、私人目录或未知旧元数据。内部旧 aliases/tags/未知 schema 数据原样保留；模型不读取或改写其内容。提案 refs 使用 `external_document_id`。可信 Python-only `derivative_input_manifest` 包含 artifact_kind=original_factual_synthesis、documents（document_id/content_hash/acquisition_hash）、public_catalogue（entry_id/revision/content_hash）及实际 ModelTask.fingerprint 的 model_input_hash；目录不含本次被更新条目，其旧正文由 base_revision 绑定。

候选公开意图和 visibility 为 public，但资格仍待审。领域 `publication_review_context(change_id, *, actor)` 给出最终 artifact、policy_id/policy_version/candidate_hash/source_manifest_hash/input_manifest_hash；普通提案返回 None。派生 reviewer 只审核完整公开 artifact、旧公开正文和干净原始证据，不声称覆盖未知内部metadata，也不签发关系专门证明。`publication_checks` 七项分别为 facts_supported/expression_independent/privacy_clear/provenance_complete/assets_clear/attribution_complete/restrictions_clear，每项 `{passed:boolean,reason:string}`；缺项、否定、不确定均 needs_evidence，普通 approve 不发证。

代码另拦截明显抄录、私人模式、代码/媒体/表格/HTML/引文及未验证链接；这些报警阈值不是法律安全线。只有语义检查全通过且扫描通过，C 才调用 `record_review(..., publication_review={policy_id,policy_version,candidate_hash,source_manifest_hash,input_manifest_hash,review_task_id,checks})`。这是 Python-only 能力，不在HTTP JSON或模型输出中接收证书；review_task_id 必须是真实 running 的对应持久 workflow task。A 再核并保存独立证书，不能更改已审核 patch_hash 以追认资格。公开投影只消费该完整 artifact，不发布内部 quote/claims/RAW/私聊/证书细节。源、候选、目录或策略版本改变使旧证失效。

显式 private 意图始终优先；普通生成也保留该意图。真实模型、账号、渠道和公开部署仍需独立受权端到端验证，fixture 通过不等于已取得转载权或真实发布验收。

`GenerationService(domain, runner, *, allowed_provider_ids=(), input_classification='personal', allow_fixture=False)`：
`async generate(evidence_refs:list[dict], *, task_id:str, actor:Actor, idempotency_key:str, entry_id:str|None=None, trace_id:str|None=None, workflow_task_id:str|None=None) -> dict`。
必须用 agent actor。父 workflow 持有任务/lease，传入已收藏 source 或明确提交 outcome 的证据引用。显式 entry_id 优先；未指定时，先按 outcome.metadata.concept_id/suggested_entry_id 找唯一已有条目，否则给模型有限目录，生成后按规范化完整 title/alias/concept ID 唯一精确匹配，携带旧文最多重生成一次。多目标冲突返回 needs_evidence，不合并、不重复创建。无匹配才新建，并保留 outcome 的稳定 suggested_entry_id 和 concept_id。返回 model_attempts 记录两次调用，装配层须一起持久化。读取原文、旧文后生成，验证 quote 原文存在，个人表达必须有真实 outcome 原话；提案默认 requires_ai_review。只 propose，不 apply。

生成和复核均优先架构设计、技术栈机制/边界/取舍/深度应用；语法仅保留有助于这些解释的部分，不能无来源推断系统设计原因。证据、旧条目和目录的分类只能提高任务分类，不能被 public 标签降级；不公开的目录最低 personal。第三方私人资料和雇主资料同时出现时，以 secret 阻断，待部署细分授权处理。

增量更新保留旧条目的 evidence_refs，并按当前 actor 重新读取原始证据；新增引用在前，旧引用按 source/outcome 身份去重追加，保持新 claim 的索引稳定。不可读或缺失的旧来源阻断更新，不能在保留旧正文的同时丢失其溯源。

生成 schema 可选 `unresolved_questions: [{text, quote, evidence_index}]`（最多 10 项）。仅提取原文明确提出的未解决问题，text 必须逐字位于 quote，quote 必须逐字位于对应原文；不能推断用户薄弱项或将来源作者疑问归到用户。代码生成稳定 `question_id`，写入 `metadata.open_questions`（去重字符串列表）及 `metadata.open_question_records`（question_id/text/state/origin_kind/evidence_ref/quote）。origin_kind 由证据类型确定为 source 或 user_statement，不能由模型自报。旧问题和记录保留，resolved/dismissed 不由模型重开或删除；新增问题可形成正文不变的元数据提案，仍须独立复核及提交后才进入 Radar。独立 reviewer 同时取得旧/新问题记录与原始证据，检查疑问是否确实未解决。这里记录问题不是掌握程度评分，也不自动创建学习成果。

新知识 `metadata.publication_intent='public'`。只有真实 source 权利为 public/public_domain/licensed/summary_allowed、证据不含私人分类、无个人立场/隐私风险时，新派生条目 visibility 默认 public。权利未知、用户学习原话、私人/雇主资料仍 private，记录 publication_block_reason，RAW 从不随派生条目公开。模型上下文分类 input_classification 与真实证据分类 evidence_classification 分开记录，不让私人目录导致已合格公开材料永久私有。已有 private 条目的可见性/意图不能自动放宽。

standalone generate 不传 workflow_task_id，领域提案同事务排 knowledge.review 任务。父 workflow 必须显式传已领取的当前 knowledge.generate 任务 ID，领域验证归属、种类与 running 状态后只关联现有任务。不得从任意 task_id 自动猜测该绑定。needs_user 直接返回，不调用模型越过用户门禁。

`AIReviewService` 构造同上：`async review(change_id:str, *, task_id:str, actor:Actor, idempotency_key:str, trace_id:str|None=None) -> dict`。使用不同 agent actor_id；独立读取旧revision、diff与原始证据，不复用生成会话。核对 patch_hash/引用，四类重大风险保留用户门禁。

review 输入的 old_semantic_patch/new_semantic_patch 覆盖 entry_id/base_revision/operation/title/body/entry_type/visibility/publication_intent/publication_eligible/metadata/evidence_refs；不能只审正文。关系目标另按 reviewer 身份读取，所有目标分类并入模型传输限制。代码固定 reviewed_fields 通过 `domain.record_review` 的仅 Python keyword 记录，HTTP JSON 或模型输出不得自报此证明。

普通 approve 不确认 `metadata.relations`。模型可额外返回 `relation_checks`，每条含 relation_index/relation_hash/supported/reason/evidence_checks；后者每项是 evidence_index/quote/supports_relation。代码逐项核对声明 hash、实际可读目标、relation_type/statement 结构、关系来源是已读证据子集、content_hash 及 locator.quote 精确匹配；只有所有关系均有完整肯定检查，才计算整组 canonical hash 并经 Python keyword reviewed_relations_hash 传给领域。缺检查、部分覆盖或否定检查不授此证明；错误 hash/quote 返回 needs_evidence。关系投影仍独立核当前 committed revision、patch hash 和原始来源约束，无专门证明时最多 candidate。依赖领域 be17984 的 Python-only 复核关键字契约。

两者返回 `{status, task_id, change_id?, change?, model?, reason?}`。model 是无原文审计信息。生成 status=proposed/no_change/needs_evidence；review=approved/rejected/needs_evidence/needs_user；模型等待/失败沿用 ModelResult.status。fixture 默认拒绝，只有测试显式 allow_fixture 才接受。父 workflow 持久化等待/结果；仅 approved 后可由独立 service actor 调用 domain.apply_change，且 auto_commit 初始 false。

关系专门证明还要求声明 `target_revision` 为正整数，且精确等于 reviewer 实际读取并提供给模型的目标快照 revision。缺失、非法或漂移时 supported=true 返回 needs_evidence；未确认 candidate 可无此字段。投影读取时再次核对当前目标版本，目标后续改写会将旧关系降为 candidate，不能将旧复核套用于新目标语义。此版本约束依赖领域 f230511。

Pi 的 before_provider_request 事件异常会被运行时捕获，不能作为权限门。扩展在官方原生 Provider.stream/streamSimple 分发前封装拒绝逻辑，未授权时不触发上游请求；事件 hook 只做非安全性的 payload 优化。后台不提供工具；交互仅固定领域 HTTP 工具。keep/discard/review 是真实 TUI 命令，RPC 不可执行，用户令牌独立且用户批准须携带领域单次 nonce 与 patch hash/base revision/policy version。

## 可选 Codex CLI 现有登录适配

`adapter` 缺省仍为 `pi`。仅显式 `adapter="codex_cli"` 使用 `CodexModelRunner/CodexRunnerConfig`，详见 [完整契约与限制](CODEX_CLI_ADAPTER.md)。允许官方 Codex CLI 自己复用已有 ChatGPT 登录，不读取/搬运认证文件，不接受 API key、代理地址或备用 provider。版本精确核对 0.163.0-alpha.10.2。

`ModelResult` 新增可选 `requested_model_id: str | None = None`。Codex exec JSONL 不提供可信实际型号时，精确请求模型在此字段，`model_id=None`；不可混写或把模型自报当元数据。已有 Pi 的实际 `model_id` 行为不变。领域等待/失败审计保留两个字段。

Codex 不提供通用无工具开关；已禁用命令和应用功能，read-only，加上非文本工具事件拒绝与清理。事件检测不是副作用回滚，不能作为绝对无工具保证。Schema 二次验证、整体输出字节/时限、幂等与额度冷却生效；没有已验证的提供方硬 token 预算，输出 token 数仅事后检查。任何 fixture 测试都不代替真实模型/Windows E2E。

### 受控失败诊断

`ModelResult.failure: ModelFailure | None = None` 为可选结构化诊断，不改变既有状态或 `error_code`。字段为 `stage`（队列/版本/登录/执行/输出等固定值）、`kind`（固定失败类别）、`summary`（固定枚举摘要，绝不摘录原错误）、`event_type`（仅 `error` / `turn.failed` / `item.error` 或空）及 `exit_code`（观察到的原生进程退出码或空）。被适配器终止后的退出码不能冒充提供方自然退出；未知保持空。

Codex 的原始提供方消息只在内存中分类，既不写日志或账本，也不返回 UI。摘要是排错线索，不是根因已验证的声明；不能据此自动换模型、切 provider、创建登录、放宽沙箱或开启 API fallback。持久化必须经过 `sanitized_failure` 白名单校验，不允许任意追加 message / prompt / stderr / 路径等字段。
