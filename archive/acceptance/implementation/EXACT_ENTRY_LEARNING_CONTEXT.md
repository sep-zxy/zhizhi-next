# 指定正式修订到既有学习卡

这是一条显式引用关联，不创建卡、不调用模型、不提交学习完成，不将正式正文写成本人原话。无既有卡时仍有 V1 成卡缺口，不能冒称 P05 全流程完成。

## 合同

学习服务自身认证与统一 backend 配置均必需，不借私有 Wiki 的浏览器 token。

- GET /api/growth/local/knowledge/entry-context?entry_id=…&revision=…：仅接受准确两个参数，读取正式服务指定历史版本，校验 ID/revision/UTF-8 body 哈希，返回 entry 与现有可选 cards。不得回退 latest。entry 仅 ID、版本、标题、正文、哈希、最小引用、原分类标签；不暴露 owner、Git 路径、commit 或内部 metadata。打开深链不建立关联。
- POST /api/growth/local/knowledge/entry-contexts：request_id(UUID)、entry_id、revision、expected_content_hash、target_card_id(UUID)、expected_card_version。未知字段/身份/正文/布尔版本拒绝。重新读取正式服务验证精确版本和 hash 后，事务内校验真实卡存在、主题 active、未 dismissed、卡版本一致。成功200仅返回 context_id、entry_id、revision、content_hash、card_id、card_version、created_at、state=linked。
- GET /api/growth/local/knowledge/cards/{card_id}/entry-contexts：返回 items、has_more=false；每项关联加 entry。重新授权读取每个精确版本，不返回缓存正式正文。远端拒绝/缺失/服务不可用时明确报错；读取到另一个 owner 的同名条目不会暴露原关联。历史卡版本关联保留原版本，不随卡修订静默替换。

同 request_id 同载荷重放返回原关联，异载荷409；重复点击或不同请求键指向相同 entry/revision/card/version 不生成第二条关联。已成功的旧请求即使卡后来修订仍可恢复原结果，但新请求必须匹配新卡版本。POST 无客户端自动身份刷新重发，由用户查询或明确重试。

## 存储与边界

growth.sqlite 追加 v32 迁移：正式引用和幂等收据两表。仅保存引用、hash、卡版本与时间，按 backend/device/服务返回的已授权 owner 摘要隔离；不保存第二份正式正文、token、用户原话或模型答复。事务 BEGIN IMMEDIATE 序列化 CAS/收据。现有 migration 文件锁保持，旧数据无覆盖。

原独立复核、Writer、完成练习与公开资格规则未修改。此引用不会自动进入模型上下文，也不会把关联动作当作学习结论或掌握证明。通过正式服务重新读取时保留原数据分类，不能因它出现在学习页就升级为 public。

## 验证

合成 ASGI/真实 SQLite/Git 测试验证只读无关联、空卡不成卡、精确历史版本在 head 前进后保持、double-submit、重启恢复、请求冲突、版本 CAS、失效主题、摘要不符、权限丢失、配置重绑定、无身份/缺服务、安全投影及无原话/完成状态修改。另保留并更新 migration 并发与旧 schema 演进测试到 v32。没有真实浏览器、真实模型或 Windows E2E 通过宣称。
