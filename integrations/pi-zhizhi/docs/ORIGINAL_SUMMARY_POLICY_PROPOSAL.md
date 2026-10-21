# 原创技术摘要的独立公开资格：待决策提案

日期：2026-10-19。初版为只读设计，随后获准实施本轮最小必要修复。**当前仅窄范围的干净正文→独立资格复核→领域证书→公开制品解析已实现并用合成材料验证；没有真实账号/渠道或实际公开部署验收。** 实际冻结协议见 `../../../docs/implementation/MODEL_API.md`。本文件保留设计依据，不应把后续政策监控等全部建议视为已经实现。

## 当前缺口与建议

当前真实 URL 摄取的 RAW 权利为 unknown，且私聊摄取分类继承 personal；领域因此阻断其派生条目自动公开。已有 public intent 和阻塞原因展示，不等于已实现用户要求的“默认公开”。匿名可获取的事实也不等于转载许可。

建议采用“默认公开意图 + 独立派生版本资格”，而不是把 source.rights 改为 summary_allowed。先限定为公开技术资料中事实、机制、架构边界与取舍的独立说明；不开放全文改写、翻译、图片、表格复刻或代码搬运。普通合格条目通过独立复核后自动发布，不逐篇要求用户确认；不满足条件的条目保留私有和明确阻塞原因，不假装获得授权。用户显式 private 意图优先，任何自动资格都不能覆盖它。

部署必须先确定适用地区、来源范围及一项有版本的内容政策。以下是工程风险控制建议，不是法院认定或对所有国家适用的法律结论。模型不负责批准法律政策。

## 四类信息必须分开

1. **接收上下文**：私聊文本、身份、附件、学习原话、雇主资料、私人目录等。保持原分类，不能因其附带 URL 而公开。
2. **实际取得的外部文档**：由可信获取器单独保存正文及获取证据，与用户附言分离。anonymous/publicly_accessible 只是访问事实，不是公开个人资料的授权，也不是版权许可。
3. **RAW 复制权利**：原始快照继续私有，rights=unknown 不变。不得从派生资格反向提升源资料权限。
4. **特定派生公开版本**：与内部知识正文可以不同。只含通过资格检查的原创事实说明及必要署名链接，不含内部引用原文、私聊注释、用户立场、访问凭据或私人关联图。

公开派生生成应使用独立、干净的输入清单，仅包含可进入该路径的外部文档与已公开条目。不先用私人上下文生成，再依赖模型声称“已脱敏”。现有 private 条目不能因此被自动改为 public；可先生成隔离的公开视图，保留原条目与原权限。

## 最小数据契约（拟议，尚不存在）

### 获取证据：由获取器写入，不由客户端或模型自报

- document_id、content_hash、canonical_url、最终 URL、取得时间、获取器版本。
- authenticated=false、publicly_accessible=true 的实际取证；不存 cookie/token。
- transport_context_id 和正文来源身份分离；正文与私聊附言不能拼接后假装都是网页。
- observed_rights/observed_restrictions 及其原始证据引用；缺失保持 unknown。发现登录墙、泄漏资料、私人信息、禁止使用等信号时排除自动公开路径。
- source_classification 与 context_classification 分列；后续分类不是篡改既有 source，而是记录独立文档及派生视图的资格。

### 派生候选：由服务生成

- artifact_kind=original_factual_synthesis，publication_intent=public。
- candidate_hash：覆盖最终可公开 title/body/metadata/citations/relations，而非只覆盖正文。
- source_manifest：每一项 document_id/content_hash/acquisition_hash；input_manifest：实际模型输入及分类指纹。
- policy_id/policy_version、generator_actor_id、生成任务及模型审计关联。
- citations：安全 canonical URL、标题、已知作者等必要署名；不暴露 RAW 存储地址或内部证据摘录。
- 初版不发布逐字引文、媒体、代码片段和仿制表格。内部证据 quote 仍供独立复核使用，不进入公开产物。

### 独立资格复核：结构化检查 + 可信代码证明

复核员与生成者身份独立，读取该候选完整语义和原始证据；使用新会话。对每一项分别作出通过/不通过/不确定及依据，不接受一个泛化 safe=true：

- facts_supported：技术事实与取舍有证据，未编造系统设计原因。
- expression_independent：不是近似逐句替换、翻译、独特编排或表达的再现，也不是原文替代品。
- privacy_clear：最终公开内容不含用户/第三方私人信息、雇主秘密、可识别学习原话、敏感属性或个人立场。
- provenance_complete：全部实质性输入可追溯到合格文档，来源未被 URL 或外层 public 标签洗白。
- assets_clear / attribution_complete / restrictions_clear：无未获准媒体代码、署名缺失或已知来源限制冲突。

程序另做精确文本重合、连续跨度、引文总量、敏感模式、URL 与元数据扫描。阈值只是保守报警规则，不能以“低于某百分比/字数”当法律安全证明；低重合也可能是翻译或近似改写，必须同时经过表达层复核。不确定不自动通过。

只有可信服务代码能形成 publication_attestation，绑定 candidate_hash、source_manifest_hash、input_manifest_hash、policy_version、review_task_id、reviewer_actor_id 和各项覆盖结果。可复用现有 Python-only 复核能力边界；HTTP JSON 或模型输出里的 attestation/eligible 字段应直接拒绝。模型不能自签资格。

## 提交、发布与失效

- 领域提交时再次核对候选、来源、复核、当前策略版本和分类；发布器再核已提交版本与资格证明。公开资格不从单个 acquisition=true 或普通 AI approve 推导。
- 普通合格派生稿的初次公开不按既有 private→public 权限放宽处理，因为其发布的是独立受限产物；必须先明确此领域模型设计，不能用隐藏开关绕过当前 privacy_exposure 风险。
- 四类重大变更门禁不变。私人立场、核心结论反转、重要合并删除、隐私暴露仍需对应人工处理；secret、泄漏资料、无权使用的大段复制不能靠一个人工批准布尔值自动放行。
- 某次复核任一维度不通过或不确定：private/blocked，记录 DERIVATIVE_PRIVACY_RISK、DERIVATIVE_EXPRESSION_UNCERTAIN、SOURCE_RESTRICTION_CONFLICT 等具体原因。可自动重新生成一次更窄候选，再独立复核；不能无限重试直到模型给通过。
- 候选内容、引用、关系目标版本、来源正文或策略变化使旧证明失效；增量更新重走独立资格。撤稿/限制变化进入持久复核队列，必要时暂停公开投影。不能把源码仓库或 RAW 目录作为公开制品。
- 缺少真实订阅资格或额度时保留 waiting_auth/waiting_quota，绝不将确定性本地扫描当独立语义复核已通过。

## 应先通过的验收用例

1. 私聊仅粘贴公开技术 URL：上下文保留 personal，RAW 权利 unknown；隔离的原创事实派生在已批准政策下可获公开资格，公开制品没有聊天信息。
2. 同一消息附带私人观点/雇主注释：公开生成输入不含这些内容；无法可靠分离时阻断，不以网页可访问洗白。
3. 登录后页面、来源未知、公开泄漏的私人信息、来源限制冲突：不走自动公开路径。
4. 长段照搬、近义替换、翻译、图片/代码/表格复刻：阻断或进入独立权利流程，不因低字符串重合通过。
5. 全文/标题/元数据/引用/关系任一变动、目标版本漂移：旧资格失效；HTTP 伪造 eligible 或 attestations 被拒。
6. 用户学习原话与来源作者问题不被公开成用户个人立场或薄弱项；RAW 和内部 quote 从公开快照排除。
7. 独立 reviewer、正确策略版本、真实完整输入清单、正文来源哈希均有可审计证据；丢响应/重启不重复发布。
8. 真正的普通公开平台 URL 从接收到隔离生成、复核、提交、公开站点可见，且未暴露 RAW；fixture 通过之外仍需真实受权账号与渠道端到端验证。

## 本轮需要的最小决策

建议先批准以上窄范围设计，再由 A/B/C/D 分别修改领域资格、分离获取对象、干净输入与独立复核、公开投影。不要仅放宽 A 的 rights 判断。本轮若不实施，应将“普通公开来源的原创摘要自动公开”明确列为未完成，而不是以 public intent 代替功能。

## 一手依据与限度

- 美国版权局说明版权保护表达而不是思想、过程、方法、系统、原则等；这支持工程上区分事实说明与表达复制，但不保证任意摘要都是合法复用。[What is Copyright?](https://www.copyright.gov/what-is-copyright/)
- 美国版权局强调 fair use 逐案判断，没有预定百分比、字数或数量的通用安全公式。因此代码阈值只能预警，不能生成“合法”证明。[Fair Use Index](https://www.copyright.gov/fair-use/)
- 中国著作权法第二十四条中的适当引用有目的、署名以及不得影响正常使用和不合理损害权利人利益等条件，不是无条件摘要公开许可。此处仅作为政策设计约束，不以 AI 复核代替法律判断。[WIPO Lex：中华人民共和国著作权法，2020 年修正](https://www.wipo.int/wipolex/zh/legislation/details/21065)
