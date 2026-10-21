现状补注：下文 modules/schema 为历史设计建议；实际目录以根 README 为准。AI Writer 已分层到 optional，不加入主平台安装。

# 知枝统一知识平台：云端四 Worktree 实施设计

版本：implementation-design v1；日期：2026-10-20（UTC+08:00）。
依据：分板块执行方案 v0.3 的 00–07、README、历史输入与接口核验。本文只形成设计，不代表代码已导入、四个工作树已创建、账号已连接或 E2E 已通过。

## 1. 本轮明确的交付契约

用户已将“仅概念方案”升级为实施授权：把现有代码安全打包到云电脑，在一个仓库建立四个开发 worktree 并行实现；先交设计图，再开始实际编码；每小时汇报；只有端到端测试完全通过才交付。

这不等于授权公开源代码、生产发布、扩大数据共享、创建长期账号权限、购买服务或绕过仓库构建限制。实施在隔离测试资产中进行。真实账号建立、真实 Wiki 自动写入和公开站点切换各有独立门槛。

最终交付不是四份“各自成功”的分支，而是一个整合提交，在约定环境下跑通全部适用验收场景的证据包。blocked、skipped、mock-only 均不计为真实通过；存在必需项阻塞时只能报告阶段结果，不报最终完成。

## 2. 架构：一个领域内核，两条知识增长链

### 用户主路径

- Pi CLI：自然语言操作、查阅知识、查看任务、提交少量重大决定。
- 知枝桌面：保留学习界面、首次作答、实践与个人表达，接入统一服务。
- 手机分享：微信官方通道优先，Telegram 备用，共用 HTTP Inbox。
- Wiki 分类门户：次要只读出口，由程序确定性生成，不需要 AI 整理发布。

### 数据流

手机分享 → 临时预览 → 用户 keep → 私有来源 → 知识增量提案。
知枝学习 → 有身份的 LearningOutcome → 知识增量提案。
提案 → 程序硬门禁 → 常规自动 / 独立 AI 复核 / 重大事项人工 → 唯一 Writer → Markdown/Git。
提交后 → 可重建检索 / 真实增长事件 / 学习雷达；公开合格子集 → 确定性 Adapter → Quartz 分类门户。

### 事实源

Markdown/Git 保存唯一正式正文；SQLite 保存任务、审查、幂等与过程账本；私有对象目录保存已收藏原件；FTS/关系和公开站点均为可重建投影。Pi 会话不成为任务账本，网站不成为第二份正文库。

### 信任边界

Pi 不直接访问 Vault 写权限、SQLite、Git、部署凭证或 OpenBao 管理权限。抓取器不接触模型凭证；发布器只能读经过过滤的 public staging。普通知识默认公开意图不等于原始素材、用户原话、附件或秘密默认公开。

## 3. 源码导入与基线门槛 G0

由已有本机任务完成打包，不重新开重复扫描或迁移任务。

1. 导出选定的 growth、Vault Manager、selfweb、AI Writer、continuity、SCHEMA 及必要规则。保留原始相对目录、许可证、AGENTS、依赖清单和来源提交标识。
2. 包含用户未提交源码时逐项列明，不清理或 reset 本机工作树。源快照清单记录路径、大小、SHA-256、源项目和提交号；不复制整台电脑。
3. 排除 auth.json、.env 实值、密钥、Cookie、会话、运行数据库、私人素材、大型构建产物与 node_modules。模板保留占位符；扫描不输出秘密值。
4. 对归档和清单分别计算摘要；云端解包前拒绝绝对路径、..、越界符号链接及重名覆盖，核对清单。
5. 云端建立一个 Git 仓库；导入子项目不保留会形成嵌套仓库的 .git。原始 provenance 写入 sources.json，必要历史另存安全 bundle，不把私密历史不加审查地上传。
6. 逐级读取 AGENTS 和用户 open-source-first skill；确认适用于构建/安装/测试的规则。既有材料提到应用构建只能用 GitHub 托管 Actions runner，不能因为换云电脑就绕过。
7. 建立只读导入基线提交 baseline/import，并记录每个子项目的实际启动/测试入口、现有失败和缺失依赖。基线失败需单列，但本轮改动引入失败不能归为旧问题。

目录以到达的源码为准，禁止为了文档重排成熟代码；建议包容式布局：

- modules/growth/：知枝原项目，保持原内部结构。
- modules/vault-manager/、modules/selfweb/、modules/ai-writer/、modules/continuity/：原工具快照。
- schema/：既有规范和版本注册表，不含私人正文。
- docs/implementation/：本设计、接口、ADR、验收矩阵。
- tests/e2e/：跨模块整合测试和脱敏 fixture。
- deploy/knowledge-platform/：隔离部署定义与运行手册。

实际路径到达后写 path-map.json，以下所有权以逻辑路径映射，不能让两个分支各猜一次。

## 4. 先设计图，后冻结契约，再创建四个开发 Worktree

G1 的完成证据：设计图及本文已交给用户；已说明是目标架构，不是已运行截图。用户要求先出图，不必凭空再增加一轮视觉审批，但用户随后提出的修改必须先处理。

G2 冻结提交 freeze/contracts-v1 由协调者在主仓库准备；只定义接口、schema、fixture、所有权、装配点与测试规格，不先写完整业务实现。所有四个开发分支从同一 freeze SHA 创建。

四个开发 worktree：

| 分支 / 路径 | 职责 | 独占源码 | 对外输出 |
|---|---|---|---|
| impl/domain / ../wt-domain | A 领域内核与可信写入 | knowledge_platform/contracts、store、tasks、auth、sources、changes、reviews、writer、migrations；schema/ 的兼容扩展 | 领域 API、持久任务、审查状态机、CAS、稳定 ID、migration dry-run |
| impl/intake / ../wt-intake | B 统一收件与内容适配 | knowledge_platform/intake、channels、extractors、media；本 lane 测试 | HTTP/微信/Telegram、解析能力报告、临时预览、keep/discard、SSRF 防护 |
| impl/agent / ../wt-agent | C Pi 与模型知识生产 | integrations/pi-zhizhi；knowledge_platform/model、generation、ai_review；本 lane 测试 | 受限工具、正式 provider adapter、ModelRunner、提案生成与独立复核 |
| impl/zhizhi / ../wt-zhizhi | D 既有知枝与知识读出口 | growth 的既有学习/Wiki/模型调用适配触点；knowledge_platform/learning、search、radar、publish；selfweb 发布适配；本 lane 测试 | RemoteKnowledgeBackend、旧模型旁路封堵、检索/学习回填、无 AI 发布 |

四路指四条代码并行开发线，不是四套数据库、四个生产服务或四份知识主库。主仓库仅用于基线、冻结、串行整合与验收，不再开第五条开发线。业务 Writer 的临时知识库事务隔离区与四个源码开发 worktree 是不同概念；测试使用独立 fixture 知识仓库，不复用源码 worktree 存正文。

### 协调者独占共享文件

根依赖/lockfile、根测试配置、主入口路由装配、CI 工作流、跨 lane E2E、导入清单、根部署文件、顶层 AGENTS 由协调者串行修改。各 lane 提交 dependency-request.json、route-registration.json、migration-request.json 或补丁建议，不争抢这些文件。

每路必须写自己模块的单元、契约和链路测试，不能把测试全部甩给 D。跨模块 E2E 由协调者编排，缺陷归责任 lane 修复。D 工作较多，先知枝学习桥接和旧模型调用收口，再检索/投影，最后雷达和分类门户，不让首页阻塞 P0。

### 原始模块的额外所有权

Vault Manager 的生产实现默认不改，A 通过边界封装复用；如必须修原工具，A 独占对应补丁。AI Writer/continuity 不盲目同时接管写入，先视为被复用能力，C 负责模型相关接口，A 负责最终写入接口，任何原文件改动先在 ownership.json 指定单一负责人。禁止两个 lane 同时重写原模块入口。

## 5. 必须冻结的接口与事件

所有新 API 在 /api/knowledge/v1；旧 loopback API 保留。写请求带 Idempotency-Key；身份从认证上下文派生，禁止请求体自报 owner 生效。

冻结对象：Entry、EvidenceRef、SourceDescriptor、Intake、Task、ChangeSet、Review、LearningOutcome、GrowthEvent、ModelTask、PublishManifest、RadarSuggestion。冻结完整 JSON Schema、枚举、字段可空性、修订语义、错误码、事件版本及示例。前端 TS 类型由同一 schema 生成，不手工分叉。

成功体统一 {data,task_id,trace_id}。例如 intake_id/state 放 data 内，避免 v0.3 示例中顶层/包裹写法混用。同步查询 task_id 可空；错误体 {error:{code,retryable,detail},trace_id}。错误码补齐 IDEMPOTENCY_CONFLICT，409 的 BASE_STALE 与 REVIEW_REQUIRED 语义分别明确。

| 调用方 → 提供方 | 契约 | 边界 |
|---|---|---|
| B → A | create_intake / update_intake / schedule_task / retain_source | B 不直接写 SQLite，不越过 keep |
| C → A | propose_change / submit_ai_review / read_evidence / task_events | AI actor 不可伪造 user，不能 apply Git |
| D → A | submit_learning_outcome / entry_catalog / commit_events | 学习幂等含设备、session、成果版本 |
| D → C | ModelRunner.run(ModelTask) | 统一模式所有旧模型调用都走此接口，无按量旁路 |
| C → D | search / read_entry / radar / learning | 限定 owner、scope、revision |
| A → D | knowledge.committed.v1 | 派生更新失败不撤销正式提交 |
| B → C | preview/generate ModelTask | 纯解析可无模型；未 keep 禁止正式知识生成 |

每个事件携带 event_id、version、owner_id、trace_id、aggregate_id、aggregate_revision、occurred_at、payload；消费者以 event_id 幂等。事件与状态写同一事务 outbox，只有领域进程写数据库。

ModelRunner 返回成功、auth、quota、policy、unsupported、invalid_output、transient 等可区分结果，A 统一映射 Task 状态。模型“已受理”不等于执行完成；Pi RPC 等 agent_settled。

必要服务端端点冻结：intakes、intakes/{id}/decision、tasks/{id}、tasks/{id}/events、sources/{id}、changes、reviews、reviews/{id}/decisions、learning/outcomes、entries/{id}、search、growth、radar/runs、radar/{id}/decision。路径、方法、scope 和幂等策略逐项写 OpenAPI。

业务服务可通过内部 protocol 接口组合，并不是为了表格把模块全部变成网络微服务。HTTP 是客户端/隔离进程边界，纯域调用采用受控接口。

## 6. 实施依赖与合并次序

### 第 0 波：安全快照、设计、契约

完成 G0/G1/G2；核对真实源码、AGENTS、依赖许可和可复用能力。把事实差异写 ADR，不以旧审计行号强改文件。

### 第 1 波：四路同时推进

- A：持久任务、身份、稳定 ID、来源和 ChangeSet，SQLite/Git 隔离测试。
- B：按冻结 fake domain protocol 完成 HTTP 收件、解析、预览/keep 幂等和故障回退。
- C：按冻结 fake domain/model fixture 完成 Pi 工具、结构化生成/复核和额度/auth 分支。
- D：按冻结客户端完成知枝 RemoteBackend/outbox、检索投影和发布 Adapter，保留 LocalBackend 的存储兼容。

Mock 只是并行开发手段，不满足真实 E2E 最终标准。

### 第 2 波：集成第一条闭环

先合并 A 最小可用内核，再 D 学习桥接，再 C 真实知识提案接口；验证知枝学习 → 独立复核 → CAS → 同一条目可检索。随后合并 B，验证链接 → 预览 → keep → 提案 → 提交。每次整合在主仓库跑跨 lane 契约测试，失败停后续合并而非互相改他人文件。

### 第 3 波：恢复、安全、真实外部链路

完成崩溃恢复、并发、权限、隐私、备份、无模型发布和雷达回填。合法取得账号配置后跑真实订阅、真实平台和渠道。微信不具资格必须明确 blocked；不能默默以 Telegram 替代微信验收承诺。

### 第 4 波：候选发行与证据

固定 integrated SHA、依赖摘要和测试资产；按仓库规则使用获授权的 GitHub 托管构建路径。如果当前没有可用私有远端或 Actions 运行权限，将应用构建/安装 E2E 标 blocked，不能在云端偷偷 build 规避限制。生产发布另行确认，不在本开发交付中自动切站点。

## 7. 端到端验收合同

### 测试层级

- T0 静态/单元/契约：证明字段、权限、状态机和接口一致。
- T1 隔离集成 E2E：真实服务进程、真实 SQLite/Git/对象目录；模型/外站可控 fixture，必须明确 fake。
- T2 真实外部 E2E：用户授权账号、真实渠道、真实公开素材、正式订阅 provider，逐阶段有可关联 trace。
- T3 安装与运行 E2E：按仓库规则产生的真实桌面产物 + 云服务，在隔离测试库完成学习和回填。

最终“完全通过”要求全部必需 T1/T2/T3 用例 PASS，不能把 T0 或 T1 全绿冒充 T2/T3。条件渠道是否从首发验收范围移除，需要用户明确确认，不由工程师通过改测试筛选器缩小范围。

### v0.3 的 E01–E20 全部保留

| 用例 | 主责 | 必须观察的结果 |
|---|---|---|
| E01 渠道消息重放 | B+A | 身份绑定一致，一次收件，可追踪回执 |
| E02 未 keep | B+A | 永久 Source 和 Wiki 提案均为零 |
| E03 抖音不完整 | B+C | partial/等待可见，不能冒充完整总结 |
| E04 重复 keep | B+A | 同一 source_id、一次知识加工 |
| E05 常规增量 | A+C | 来源与策略可追溯，正式提交一次 |
| E06 四类重大风险 | A+C | 核心结论/重要合并删除/立场/隐私分别拦截；AI 高置信不越权 |
| E07 无个人原话 | C+D+A | 不伪造“我的理解”，保持待证 |
| E08 actor/owner 冒充 | A+C+B | 401/403，无批准和写入副作用 |
| E09 审批后变化 | A | base/patch/policy 任一变化旧批准失效 |
| E10 Git/DB 崩溃 | A | commit 后 kill 恢复只应用一次，含后续 HEAD 场景 |
| E11 索引故障 | D+A | 正文保留，索引可重建，状态不谎报 |
| E12 额度耗尽 | C+A | waiting_quota，普通按量端点请求为零 |
| E13 认证/秘密不可用 | C+A | waiting_auth，无秘密日志与明文降级 |
| E14 SSRF/注入 | B+C+A | URL 各跳拦内网，材料不能执行工具越权 |
| E15 无模型发布 | D | 同版本两次投影 hash 一致 |
| E16 公开泄漏回归 | D+A | HTML/JS/搜索/图谱/sitemap/资源均无私有指纹 |
| E17 离线编辑冲突 | D+A | 两边内容保留，冲突可见，无 force push |
| E18 雷达学习回填 | D+A+C | 重复接受一次学习任务，成果回填不造掌握分 |
| E19 备份恢复 | A+D+B | DB/Git/来源/对象一致，索引重建成功 |
| E20 旧模型路径 | D+C | 所有旧模型入口经新路由，旧收费端点零请求 |

### 追加真实用户旅程

J01：官方微信授权收件 → 小黑盒正文 → 预览 → keep → 正式条目 → Pi 查到正确引用。
J02：Telegram → 抖音短链 → 合法媒体/字幕或 ASR → 预览 → keep → 条目；重复长链不造重复知识。
J03：知枝安装版学习作答 → 保留原话 → RemoteBackend → 提案/复核 → Wiki 回填；断网 outbox 重放不重复。
J04：Pi 正式订阅模型会话 → 工具调用 → 领域任务 → 终态 → CLI 重启仍可查；无付费旁路。
J05：同一正式 revision → 关闭模型网络 → 发布 Adapter → Quartz 可浏览分类、条目、来源、中文搜索；仅隔离测试站点，不默认公网部署。

至少一条完整正向旅程必须实际使用正式订阅 provider，不是测试模型；平台受访问限制时验证失败语义只是负例 PASS，不能替代正向平台能力 PASS。无法合法取得正例需明确阻塞。

### 结果记录

每次运行记录 run_id、integrated commit、依赖锁摘要、环境/系统、真实或 fixture、测试资产清单、执行命令、用例列表、passed/failed/blocked/skipped、退出码、trace 关联、截图/报告、未满足的资格。秘密和原始私人内容不写证据包。

唯一完成判定：必需用例 failed=0、blocked=0、skipped=0；每项预期观察值成立；旧知枝回归无新增失败；安全/隐私用例不能豁免；整合后的同一提交重复运行核心旅程仍通过。不通过则继续修复或请求具体阻塞权限，绝不报“基本完成”。

## 8. 每小时进度汇报

由协调者在原对话每小时一次，按用户时区 UTC+08:00；起点以实施确认时刻为准。每次只报：本小时可验证成果、四路当前状态、整合 SHA/测试计数、阻塞和下一小时计划。阻塞需要用户输入时即时说明，不必等整点。

固定格式：A/B/C/D 各一句；当前 E2E PASS/FAIL/BLOCKED/SKIPPED；最近合并和证据；下个里程碑。不报没有证据的百分比，不把 mock 数当真实通过数。汇报调度由父任务维护，此设计文档本身不代表自动化已建立。

## 9. 仍可能要求用户动作的门槛

产品选择不再重问。只有以下真实阻塞才汇总请求：官方微信账号资格、Telegram Bot 的授权配置、所用订阅账号的正式登录、OpenBao 初始化/解封与工作负载身份创建、允许的真实测试素材、仓库托管构建权限。账号密码/密钥不在聊天里索取；不自动新建授权或把源代码推到未获准远端。

已经明确：微信优先、Telegram 备选；个人服务器；小黑盒/抖音先行；Pi CLI 主要交互；有来源常规知识自动、普通问题 AI 复核、重大问题才交用户；默认公开仅限合格知识产物；分类门户次要；沿用 SCHEMA 基础但更新细则。

## 10. 设计图制作提示

绘制中文横向 16:9 系统实施架构图，标题“知枝统一知识平台 · 四路并行实施”。白底、深灰文字、四种低饱和颜色；以箭头和模块边界表达逻辑，不画营销首页。左侧放“Pi CLI 主交互”“知枝学习”“微信优先 / Telegram 备用”。B 蓝色区域“统一收件 → 临时预览 → keep → 私有来源”；C 紫色区域“Pi / 订阅模型”“生成提案 / 独立 AI 复核”；A 绿色主区域“权限与任务账本”“来源 · 提案 · 分级审核”“唯一 Writer”；A 下接“Markdown + Git / SQLite / 私有对象”。D 橙色右侧“知枝接入 / 检索 / 学习雷达”，小分支“确定性 Adapter → Wiki 分类门户”，注明“发布不调用 AI”。重大审核向“用户确认”分流，回到 Writer 前，不直接写库。顶部或底部用一条独立开发带展示“一个代码仓库 → 契约冻结 → A/B/C/D 四 Worktree → 串行整合 → 真实 E2E 全通过”。强调“Mock 通过 ≠ 交付”“账号/权限不足 = 阻塞”。数据流用实线，工具调用用虚线；不将 Pi 画成可任意写库的超级中心，不将 Wiki 主页画成主入口。
