# 领域 API 契约 v1

此文先冻结跨路调用边界。实现位于 `projects/growth/src/ahadiff/knowledge_platform/`。领域操作同步；HTTP、模型与媒体网络 I/O 留在适配层。所有返回值是可 JSON 序列化的对象或对象列表，HTTP 包装 `{data, task_id, trace_id, data_classification, classification_labels}`。所有时间 UTC ISO-8601。

## 身份与错误

`from ahadiff.knowledge_platform.auth import Actor`

`Actor(owner_id: str, actor_id: str, kind: str, scopes: frozenset[str], allowed_classifications: frozenset[str] = frozenset({'public', 'personal'}))` 是服务端认证后的不可变对象。kind 只能为 `user`、`agent`、`service`。不得从请求 JSON 构造 Actor，不得让 `owner_id`、`actor_kind`、`user_approved`、`scopes`、`allowed_classifications` 等请求字段授权操作。HTTP 从 `request.state.actor` 读取已认证身份，否则返回 401。适配器内部测试可直接建立 Actor；生产由认证中间件验证凭据后注入。

分类统一接口位于 `auth`：`classification_labels(record, *, default='unknown') -> frozenset[str]`、`classify_record(record, *, default='unknown') -> str`、`enforce_read_classification(actor, record, *, default='unknown') -> str`。递归读取 `data_classification/classification/evidence_classification/input_classification/classification_labels`，不得用外层 public 覆盖内层敏感标签。展示标签按 public、personal、third_party_private、employer、secret、unknown 排序，但授权检查全部标签，而不是仅最高标签。机器主体每个标签都须在部署 allowset 中；secret/unknown 永不允许机器主体读取。用户本人可读自己的材料，但 Pi 向模型返回时仍须客户端传输守卫，不可因 user token 豁免。

领域记录通过真实 source/intake/learning/change/task 引用继承标签，原有无标签领域数据保守 personal；任意无标签外部字典默认 unknown。新摄取规范字段为 payload.data_classification；可信公开来源需明确 public，混合敏感标签不会被覆盖。公开意图仍可 public，但实际来源含非 public 标签时 publication_eligible=false。列表过滤无权记录，单项读取返回 CLASSIFICATION_FORBIDDEN(403)，任务领取跳过无权材料，幂等回执重放也不能绕过分类边界。

所有方法必须显式传 `actor`。访问错误用 `DomainError(code, detail, retryable=False, status_code=400)`；提供 `as_dict(trace_id)`。常见错误：`UNAUTHENTICATED`(401)、`FORBIDDEN`(403)、`NOT_FOUND`(404)、`IDEMPOTENCY_CONFLICT`、`BASE_STALE`、`REVIEW_REQUIRED`、`INVALID_STATE`(409)、`INVALID_INPUT`(422)。

## 初始化

`DomainService(database_path: str | Path, vault_path: str | Path | None = None, *, object_path: str | Path | None = None, cache_path: str | Path | None = None, policy_version: str = "1")`

SQLite 是唯一任务/流程账本；vault 是正式 Markdown 的 Git 存储；对象默认在 database 同目录的 `objects/`。不使用已有知枝数据库，不更改 ahadiff 命名空间。所有写方法要求非空幂等键；相同 owner、操作、键和输入返回第一次结果，输入变化返回 409。

## 跨路固定方法

所有 `trace_id` 参数均为 `str | None = None`，`actor`、`idempotency_key` 是 keyword-only。下列 `payload`、`patch`、`descriptor`、`state` 是 `dict[str, Any]`。

### 任务与事件

- `create_task(kind: str, payload: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `read_task(task_id: str, *, actor: Actor) -> dict`
- `list_tasks(*, actor: Actor, state: str | None = None, limit: int = 100, offset: int = 0) -> dict`：只读，返回 `{items, has_more}`，同 owner 且分类可读，按创建记录逆序。先过滤身份/分类与 state，再对可见记录分页；不返回全库或隐藏记录计数。
- `complete_learning_task(task_id: str, outcome_id: str, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`：仅真实 user 完成人工练习；task.kind 必须 learning、state为queued/waiting_user，outcome 必须同owner且其metadata.radar_suggestion_id匹配task.payload.suggestion_id、entry_id属于targets。还核验真实radar_suggestion和已accepted/completed状态中的learning_id。事务succeeded+learning.task.completed.v1，重复同outcome不重复事件，不允许机器身份/任意worker任务绕租约。
- `update_task(task_id: str, patch: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `claim_task(worker_id: str, *, actor: Actor, kinds: list[str] | None = None, lease_seconds: int = 60) -> dict | None`
- `renew_task_lease(task_id: str, lease_token: str, *, actor: Actor, lease_seconds: int = 60) -> dict`
- `resume_due_quota_tasks(*, actor: Actor, limit: int = 100) -> list[dict]`：仅 service + tasks:work + knowledge:write，扫描同 owner 且分类允许的 waiting_quota；result.model.retry_after 必须为含时区的有效 ISO 时间且已到期，attempt < max_attempts。事务切 queued、保留 checkpoint/历史结果、记录 task.resumed.v1；并发/重复调用不会重复排队。不会复活 waiting_auth/blocked/waiting_user/人工审查等其他等待态。维护周期由装配层调用，不以后台线程隐式启动。
- `list_events(*, actor: Actor, after: int = 0, types: list[str] | None = None, aggregate_id: str | None = None, limit: int = 100) -> list[dict]`
- `task_events(task_id: str, *, actor: Actor, after: int = 0, limit: int = 100) -> list[dict]`

Task 含 `task_id, owner_id, kind, state, payload, result, error, attempt, lease_token, lease_until, revision, trace_id, created_at, updated_at`。state 对齐 v0.3：`queued, running, succeeded, partial, waiting_user, waiting_auth, waiting_quota, retry_wait, blocked, failed, cancelled`，另兼容 `waiting_review, waiting_input`。invalid_output 映射 partial+error.code；不伪造 succeeded。运行中结果须 patch.lease_token，actor 还须 tasks:work；旧 lease 禁止写结果。patch 支持 checkpoint/result/error/next_run_at/expected_revision。等待态可 CAS 改 queued 恢复，终态不可变。事件含 `sequence, event_id, type, version, owner_id, trace_id, aggregate_id, aggregate_revision, occurred_at, payload`。

`GET /api/knowledge/v1/tasks?limit=100&offset=0&state=waiting_auth` 返回 envelope 的 `data={items:[...],has_more:boolean}`。limit 为 1–100，offset 为 0–100000，state 省略即所有状态，非法参数或未知状态返回 422；未认证 401、缺 read scope 403。没有内容时真实返回空列表，不借助 mock 填充；只含公开分类权限的身份也可读取已过滤后的空结果。

任务列表和 `GET /tasks/{task_id}` 均使用 `public_task_record` 安全投影：包含任务身份/kind/state/revision、attempt/max_attempts、时间、trace、分类、必要结果 ID/版本与受控模型审计；不包含 owner_id、payload、checkpoint、lease_token/lease_owner、模型 output、任意错误 detail 或服务器路径。`result.model.failure` 仅含固定 stage/kind/summary/event_type 与数值或空 exit_code。内部 `DomainService.read_task` 仍完整供受权 worker 使用租约；HTTP 投影不改变账本。

`POST /tasks/{task_id}/resume` 回执使用相同任务投影；`GET /tasks/{task_id}/events` 只返回事件身份、版本、时间、序号、trace、分类及白名单时间线 payload（合法 state、数值 attempt、布尔 reclaimed、固定 quota_retry_due reason），不带 owner 或任意扩展 payload。内部 outbox 与 `DomainService.task_events` 保持完整，不因显示投影改写。

### Intake、来源与渠道游标

- `create_intake(payload: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `read_intake(intake_id: str, *, actor: Actor) -> dict`
- `list_intakes(*, actor: Actor, state: str | None = None, limit: int = 100, offset: int = 0) -> dict`：返回同样的可见分页 `{items, has_more}`，只读快照，不自动 keep、expire、生成任务或调用模型。
- `read_intake_lifecycle(intake_id: str, *, actor: Actor) -> dict`：维护专用，仅 user 或具 tasks:work + knowledge:read 的 service；同 owner 返回 intake_id/state/source_id/expires_at/revision 与 public 控制回执标签，不返回标题、URL、原文或预览，agent 禁止使用。用于分类受限但已经保留/失效收件的残留缓存协调，绝不开放 Source 正文。
- `update_intake(intake_id: str, patch: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `retain_source(intake_id: str, descriptor: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `get_source(source_id: str, *, actor: Actor) -> dict`
- `read_evidence(evidence_ref: dict, *, actor: Actor) -> dict`
- `get_channel_state(channel: str, *, actor: Actor) -> dict`
- `set_channel_state(channel: str, state: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`

Intake payload 可含 `channel, external_message_id, url, title, metadata`。`(owner_id, channel, external_message_id)` 额外去重。初始 `received`；preview 写入 patch.preview/parse_status，状态为 `preview_ready` 或 `partial`。丢弃写 `state=discarded`。只有显式用户 keep 才能调用 retain_source，agent 身份禁止 keep；已保留再次调用返回同一 source。descriptor 支持 `title, url, canonical_url, content_type, raw_text, raw_path, metadata, capability_report`。raw_path 是本服务已获准的 intake 暂存文件，不接受目录/符号链接；A 将原件按 SHA-256 复制进私有对象目录，失败不置 kept。对象私有，不能把 raw 当默认公开素材。source 含 `source_id, owner_id, intake_id, content_hash, object_path, descriptor, visibility=private`。`get_source` 返回元数据；`read_evidence` 检查引用归属与内容摘要后返回带原文的私有 evidence。

`GET /api/knowledge/v1/intakes?limit=100&offset=0&state=preview_ready` 返回 `data={items:[...],has_more:boolean}`，分页与认证规则同 tasks。items 使用既有 intake `_public_record`：intake_id/state/channel/url/title/preview_revision/parse_status/分类/source_id/task_id/到期与创建更新时间、去除 cache_ref 的 preview；不返回 payload、外部消息身份、内部 metadata 或对象路径。`GET /intakes/capabilities` 继续独立存在，不拿能力说明冒充收件列表。列表显示账本快照，不因 GET 隐式清理；到期处理继续由既有生命周期流程负责。

### 提案、复核与正式条目

- `propose_change(payload: dict, *, actor: Actor, idempotency_key: str, trace_id=None, workflow_task_id: str | None = None) -> dict`
- `get_change(change_id: str, *, actor: Actor) -> dict`
- `record_review(change_id: str, payload: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `apply_change(change_id: str, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `get_entry(entry_id: str, *, actor: Actor, revision: int | None = None) -> dict`
- `list_entries(*, actor: Actor, limit: int = 100, offset: int = 0) -> list[dict]`

propose payload：`entry_id`（创建时可省略）、`base_revision`（新建为 0）、`title`、`body`（完整目标 Markdown）、`entry_type`（默认 concept）、`visibility`（private/public；新常规知识默认 public 意图，更新保留原意图）、`evidence_refs`（至少一个；source_id 或 learning_outcome_id + 可选 content_hash）、`risk_flags`（列表）、`operation`（create/update/delete，默认按 base 推断）、`metadata`（对象）。不能自报批准。`publication_intent` 与 `publication_eligible` 分离：默认公开意图不会自动公开私有原件、个人学习证据或权利不明内容。

四类重大门禁：`core_conclusion`、`important_merge_delete`、`personal_position`、`privacy_exposure`。程序从操作/可见性/个人表达与变更幅度作保守推断，不仅信任模型的 risk_flags。程序硬门禁验证身份、来源归属、base、patch hash、policy 版本及所有证据。一般新建有可靠来源可自动；普通修改要求独立 AI 复核；重大变更要求 user 复核。删除为墓碑 revision，不丢历史。审核绑定 `base_revision, patch_hash, policy_version, evidence_hash`；任一变化旧批准不再可用。

record_review payload：`decision=approve|reject|needs_user|needs_evidence`、`reason`、必需 `expected_patch_hash`。risk_flags 只增不减，requires_ai_review 进入普通独立 AI 审核。复核身份由 actor.kind 决定，不能把 AI 字段伪装为 user。AI actor_id 不得与提案生成者相同。用户批准还须 `create_decision_challenge(change_id, *, actor)` 返回的单次 `decision_nonce` 和 `base_revision`；挑战绑定 patch/base/policy/evidence，10 分钟过期。用户批准原子排入 knowledge.apply，AI 不得批准四大风险。apply 只允许 service/user 的 knowledge:apply；agent 永远不能直接 Git 写入。

Entry 含 `entry_id, owner_id, revision, title, body, entry_type, visibility, evidence_refs, metadata, deleted, path, commit_sha, content_hash, created_at, updated_at`。Git/SQLite 断点由 journal 恢复，后继 HEAD 不阻止确认祖先提交。`knowledge.committed.v1` 和条目 SQL 状态在同一事务保存；投影失败不回滚正文。

正式写入同时核验全局 Git HEAD CAS 和目录所指不可变 Markdown 的逐文件摘要。若外部编辑已经改变该文件但目录尚未协调，或新增 ID 的 Git 路径已有未入目录文件，返回 BASE_STALE 并保留两版本；不能把新 HEAD 与旧目录正文拼接成有效覆盖授权。

### 学习成果

- `submit_learning_outcome(payload: dict, *, actor: Actor, idempotency_key: str, trace_id=None) -> dict`
- `get_learning_outcome(outcome_id: str, *, actor: Actor) -> dict`

payload：`device_id, session_id, version`（正整数）、`entry_id`（可空）、`user_text`（保留原话）、`result`（对象）、`metadata`。以 owner+device+session+version 语义幂等，内容变更冲突。产生 `learning.outcome.submitted.v1`，不自动捏造个人理解或掌握分。D 可随后创建任务调用 C 提案。

## 装配与安全边界

`create_core_routes(domain: DomainService) -> list[starlette.routing.Route]` 提供 task/source/change/review/entries 核心端点，包括 GET entries 和 GET entries/{entry_id}。B intake 和 D search/growth/learning/radar 由协调者装配。认证中间件 `DomainAuthenticationMiddleware(app, authenticator=TokenAuthenticator(token_to_actor))` 由协调者添加，无默认 token。`request.state.actor` 是唯一 HTTP Actor 来源。generator scopes=knowledge:read+knowledge:write；独立 reviewer=knowledge:read+knowledge:review；worker 还需 tasks:work；writer service=knowledge:read+knowledge:apply；user 可读写/keep，审批需 knowledge:review。

新增端点：GET changes/{id}/decision-challenge、POST changes/{id}/decisions、POST changes/{id}/reviews、POST changes/{id}/apply、GET reviews、POST tasks/{id}/resume。完整前缀均 /api/knowledge/v1。用户审批响应含 task_id，后台消费 knowledge.apply。retain_source 事务内创建唯一 knowledge.generate 任务，payload.evidence_refs 含 source_id，返回 source.task_id；B 不再另建任务。

对象 raw_path 限定 domain.cache_path（默认 db.parent/cache/intake），拒绝越界与符号链接；keep 必需 descriptor.preview_revision。preview 重新生成需 patch.expected_preview_revision CAS，普通 patch 可用 expected_revision。未 keep 到期/丢弃清理临时正文，不生成来源。正式 Entry 的 SQLite catalog 无 body，get/list 每次从指定 Git commit 读取并核对 hash。

## 补充的执行与恢复契约

- 独立 propose 默认同事务创建 knowledge.review，返回 change.task_id。已在持久 knowledge.generate 内的 C 调用显式传 Python keyword workflow_task_id；A 核验同 owner、kind、running 后关联原任务，不再排第二次审核。HTTP JSON 提供该字段被拒绝。no_change 不排处理任务。
- L1 仅接受可在保留证据原文逐行核对的摘录增补；非原文改写自动升级 needs_ai_review，不凭“有 source_id”推定蕴含。已有正文语义替换保守标记 core_conclusion；用户批准仍不可由 AI 替代。
- `publication_intent` 可单独传入或在 metadata 中声明，和实际 visibility / publication_eligible 分离；模型路由上下文 personal 不等于来源永久私有，实际 source 分类为 personal/third_party_private/employer/secret 时不可公开。未知来源权利不获得公开资格。
- `list_expired_intakes(*, actor, limit=100) -> list[dict]` 包括没有缓存文件、从未领取的 received；只枚举未保留到期记录。
- `expire_intakes(*, actor, limit=100) -> list[dict]` 在独占事务内校验当前状态/到期时间后清理原文/preview及历史幂等响应副本，取消未完成 intake.preview。仅 user 或 service+tasks:work；service 可清理 secret/unknown 但只返回 lifecycle 控制回执，不获得任何原文读取权限。已收藏 Source/Object 不受影响。此处是逻辑去引用，不声称 SQLite 底层空闲页或历史备份被取证级擦除。
- `claim_events(consumer, *, actor, limit=100, lease_seconds=60)` 与 `acknowledge_event(event_id, consumer, lease_token, *, actor, receipt=None)` 提供独立消费者 outbox 租约，requires tasks:work。
- `recover_writer(*, actor)` 校验 Change-Id、树摘要与祖先关系后恢复，requires knowledge:apply 且 actor 不能是 agent。
- `backup(destination, *, actor) -> dict` 仅 service+knowledge:backup，目标必须不存在且不与线上存储重叠；在写锁下保存一致 SQLite backup、bare Git 与引用对象以及完整 manifest。恢复用 DomainService(snapshot/platform.db, snapshot/vault.git, object_path=snapshot/objects)，索引再重建。该方法不是公开 owner 导出端点。

投影可用 `list_events` 按 sequence 重放；频道游标单独存储但仍通过同一领域服务。私有原件不随 `list_entries` 或公开投影输出。代码尚未验证时不宣称真实 E2E 通过。

## 原创知识摘要的窄范围公开资格（2026-10-19 补充）

RAW 转载权与具体原创摘要资格分开。下述机制不修改 RAW 的 `rights=unknown`、private 可见性或私人附言，不是法律安全港。仅新建普通事实性原创摘要、已有合格 public 条目的追加内容适用；已有 private、不合格旧条目、个人观点、学习原话、雇主/第三方私密/secret/unknown 输入及显式 private 意图不自动升级。

以下证明参数**只接受可信 Python 调用**，HTTP、模型 payload 与嵌套 metadata 自报同名字段均 403：

- `retain_source(..., external_document_manifest: dict|None=None)` 原子登记匿名抓取正文。必填 `body_text/content_hash/canonical_url/final_url/captured_at/fetcher_version="anonymous-safe-fetch-v1"/authenticated=false/publicly_accessible=true/context_separated=true/content_kind=external_article|external_post/privacy_assessment="not_assessed"/observed_restrictions=[]`。正文 UTF-8 SHA-256 与实际保留 bundle.document.body_text 相等；URL 必须 HTTPS、无账号/查询/fragment/明显能力路径；来源分类与显式private意图仍限制候选。RAW 内存有manifest本身不授予权限。内容去重同时绑定分类、候选身份和private意图，不混用同URL不同敏感级的生成任务。
- `read_external_document(source_id, *, actor) -> dict|None` 只给分离正文 `text/document_id/content_hash/acquisition_hash/canonical_url/evidence_ref`，不含私人传输 envelope。匿名可访问不等于语义无隐私，故仍标 `personal`，独立审核不可省略。`read_evidence({external_document_id,content_hash},actor=...)` 可读取同一净化证据。
- `propose_change(..., derivative_input_manifest={artifact_kind:"original_factual_synthesis", documents:[{document_id,content_hash,acquisition_hash}], public_catalogue:[{entry_id,revision,content_hash}], model_input_hash:<实际模型输入指纹>})`。可信生成代码只消费这些净化正文、已验证公开目录及 base_revision 绑定的公开旧正文。目录排除当前 entry_id，防提交后自引用失效。普通HTTP使用external ref只可形成未授证修改，包括私有撤回，不自动获得公开资格。
- `publication_review_context(change_id, *, actor) -> dict|None` 普通提案为 None；派生提案返回 `policy_id="original-factual-synthesis-v1"/policy_version/candidate_hash/source_manifest_hash/input_manifest_hash/artifact/source_manifest`。artifact 仅 title/body/entry_type、tags/aliases/categories 白名单和安全 citations，不带来源对象路径/学习原话/用户路由。程序扫描只是保守报警，逐项语义复核仍必要。
- `record_review(..., publication_review={policy_id,policy_version,candidate_hash,source_manifest_hash,input_manifest_hash,review_task_id,checks})`。checks 必须完整覆盖 `facts_supported/expression_independent/privacy_clear/provenance_complete/assets_clear/attribution_complete/restrictions_clear`，每项 `{passed:true,reason:<非空>}`；必须独立 agent、approve、实际关联且租约未过期的 running knowledge.generate/review。普通approve不签证。证书持久绑定 review、patch、输入、来源正文和policy。不得把派生artifact审核冒充内部metadata/关系审核。

Writer 不篡改所审 patch/title/body 或 proposal.publication_eligible，而按另存可信证书计算正式 Entry 的实际 `publication_eligible=true`，写入 `public_artifact` 与证书摘要。证书摘要键为 `certificate_id/policy_id/policy_version/candidate_hash/source_manifest_hash/input_manifest_hash/patch_hash/review_id`。公开投影只能使用净化artifact，不发布内部证书/来源ID/RAW引用。

`verify_publication_entry(entry, *, actor) -> public_artifact` 是发布Adapter可信resolver。对指定 Git 文档逐字段比对ledger记录的不可变历史commit，重新核验独立review、完整patch、当前policy、实际RAW hash、来源清单与模型输入绑定；无证书的既有schema6授权路径也重新检查真实来源rights与分类。owner_id不存Git时由已认证actor定位，显式错owner仍拒。当前条目若已private、删除或撤回eligibility，即使旧Git证书曾有效也拒绝。构建前后均需重新resolver与manifest比较，不能信自报eligibility。

`DomainService.open_readonly(database_path, vault_path=None, *, object_path=None, policy_version="1")` 只打开已有SQLite `mode=ro/query_only` 与只读Git facade，不迁移、创建、recover或调用Writer。发布器的policy_version必须来自可信部署配置，不能取自待发布artifact；此版没有第二个独立policy账本。资源使用完调用close。
## 关系只读投影（2026-10-19 补充）

`from ahadiff.knowledge_platform.relations import RelationService`

`RelationService(domain)` 不另建账本或持久主库；每次读取从当前 owner 可读的正式 Markdown 与审核审计重建可丢弃内存投影。重建期间 Git HEAD 改变返回 `PROJECTION_STALE`，不会混用修订或自动写回正式库。

路由工厂 `create_relation_routes(service, *, prefix="/api/knowledge/v1")` 返回只读 `GET /relations/{entry_id}` 与 `GET /relations/{entry_id}/traverse`；父应用沿用身份中间件装配，未鉴权仍 401，绝非匿名公开图接口。query 用重复 `kind` 参数选择种类，`direction/visibility/limit/max_depth/max_nodes` 对应下列方法；非法整数/越界为 422。统一 envelope 保持 data/task_id(null)/trace_id/data_classification/classification_labels。

- `rebuild(*, actor, visibility="all") -> dict`
- `neighbors(entry_id, *, actor, kinds=None, direction="both", visibility="all", limit=100) -> dict`
- `traverse(entry_id, *, actor, max_depth=2, max_nodes=100, kinds=None, direction="both", visibility="all") -> dict`

`visibility` 为 `all|public`；`kinds` 为 `navigation|confirmed|candidate` 数组或 None；`direction` 为 `in|out|both`。有限多跳最多 3 层、200 个节点、1000 条边，不计算掌握分或传递事实。返回 `nodes/edges/projection_revision/classification_labels/data_classification/inference="none"`；邻接/多跳还含 `root_entry_id/truncated`，多跳含 `distances`。隐藏、删除、其他 owner 条目不成为悬挂节点。public 模式两端必须为明确公开合格条目，证据也必须明确 public 且允许摘要/授权；不会输出原文 quote、对象路径或私人来源。

导航仅由 Markdown 正文中的真实 `[[稳定ID]]`、唯一标题或 alias 解析；代码与媒体嵌入不产生关系。结构关系只读取版本化 `metadata.relations`：

```json
[{"kind":"confirmed","target_entry_id":"ent_example","target_revision":1,"relation_type":"supports","statement":"明确的关系陈述","evidence_refs":[{"source_id":"src_example","content_hash":"sha256","locator":{"quote":"证据逐字原话"}}]}]
```

结构边 kind 仅 `confirmed|candidate`；relation_type 为 1–64 字符小写 snake_case；statement 非空且最多 2000 字符；每条最多 50 个 evidence refs；每页最多 200 个声明。证据仅来源或真实学习成果，每个 ref 必须属于正式条目的 evidence_refs。confirmed 必须逐条 exact hash/quote 通过、正整数 target_revision 等于当前目标修订，且当前正式 revision 对应 committed ChangeSet 具有覆盖整个关系数组的可信审批。缺少目标修订、目标后来改写或其他条件不能验证则降为 `candidate/is_fact=false`；跨 owner、不可读、无绑定或坏引用直接忽略，不泄露缺失目标信息。

`record_review` 新增两个**仅 Python keyword**：`reviewed_fields: tuple[str,...]=()`、`reviewed_relations_hash: str|None=None`。HTTP/payload 同名字段拒绝。关系 hash 必须为 canonical `fingerprint(change.metadata.relations)`，仅 approve 且 reviewed_fields 包含 metadata 时允许。它由可信 reviewer 程序在逐项复核关系、目标与证据后计算，不接受模型自报 hash 或仅 generic approve/boolean。事件同时绑定 patch_hash 与此覆盖 hash；旧审批、仅审核正文和普通 approve 不确认关系。未给 keyword 的旧调用保持兼容。
