# 私有 Wiki V1：材料、证据、版本提案与任务控制

本批实现 P03 / P05 / P11 中已有领域能力及经批准的最小补料/任务合同。只使用已认证私有 API，不产生浏览器令牌，不创建真实测试身份，不直接运行模型、不发布公开站。以下结果不等于 Windows / 真实账户 / 模型全链路验收。

## P03 文本补料

POST `/api/knowledge/v1/intakes/{intake_id}/supplement`，需要 `Idempotency-Key`、真实已认证 user、`knowledge:write`。浏览器 cookie 请求仍需原有配对 proof 和 CSRF。唯一允许字段：

- `expected_preview_revision`：非负整数，允许尚无预览的 0，不接受 bool。
- `text`：非空有效 UTF-8 来源正文，最多 100,000 字符及 300,000 字节。
- `private_note`：可省略的私人附言，最多 10,000 字符及 30,000 字节。

响应沿用 envelope，`data` 为安全 intake 投影；首次成功为 `state=partial`、预览修订加一。当前阅读副本是本次补充正文，完整度始终 false、摘要未生成。原始缓存、此前补料、原文本均保留在隔离临时缓存中；前端分栏显示原材料、补充正文和私人附言。

事务中依次核对同键回执、owner/分类、未保留/未丢弃/未到期、预览 CAS、当前解析任务不在运行。补料不会创建或重写 Source / RAW / 正式条目，也不拉取链接、不读取客户端文件路径。尚未运行的旧 intake.preview 任务取消；运行中的租约绝不被夺取。最多 20 次补充，不延长原有效期。至少 personal 分类，与原有更强分类保守合并，不能由请求降级。

后续“私有保留”仍为独立决定，必须使用新预览修订。保留的不可变私有包包含原材料与完整补料历史，正文证据仅取已标识的来源正文，私人附言不进入模型证据。手动补料包不再按最后一段正文语义去重，避免相同正文、不同附言的包丢失。缓存丢弃/过期后的旧补料幂等回执也被现有清理机制清除内容。

### 实际 UTF-8 文本附件

POST `/api/knowledge/v1/intakes/{intake_id}/text-attachment` 接受实际文件字节，不是假上传或文件路径。唯一字段为 `expected_preview_revision`、`filename`、`content_type`、`content_base64`、可选 `private_note`。同样需要 user / knowledge:write / CSRF / Idempotency-Key，使用同一预览 CAS、TTL、20 次补料上限和运行/对账/已保留拒绝规则。

- 仅 `.txt` + `text/plain`，或 `.md` + `text/plain|text/markdown`；文件名最多 120 字符，拒绝路径、隐藏名、控制字符与双点。
- 严格规范 Base64，解码前校验编码长度；实际文件 1–100,000 字节，严格 UTF-8，不猜测/转码。拒绝归档/执行文件/PDF 签名及不允许的控制字节。整个 JSON 请求仍有 512 KiB 流式上限，包含 Base64 膨胀和附言。
- BOM、CRLF、原始字节与文件名/MIME/大小/SHA-256 保存在服务器派生路径的私有临时缓存包中。预览仅返回安全元信息和可阅读文本，绝不回传 Base64。无 Source、RAW、正式条目或自动公开副作用；独立 keep 后才进入原有不可变 RAW 包。
- 原文件、补料历史和私人附言分栏；证据正文不混入附言。丢弃/TTL 清理沿用缓存及幂等回执清理，不延长有效期。
- 前端真实 FileReader 读取，上传前显示来源正文并单独确认。选择新文件、取消、401/注销/卸载后移除内存内容；成功回执后清除本地文件副本。未知发送结果冻结原始 bytes/body/key，只能同键重试，不假装已撤回服务端请求。
- local-test 会话前端禁止真实文件；路由提供 `allow_text_attachments=False` 时在解码/缓存前拒绝的工厂开关。集成装配由后端负责人将该开关绑定 `local_test_browser_sessions is None`，本提交不修改共享 app.py；这项装配需随集成检查，不能把参数测试当作整包已禁用。

仍有明确缺口：截图、图片、音视频、PDF、归档和其他文件没有安全附件合同，页面明确拒绝，不造成功回执。新收件尚须先建立文本/链接预览，再向该预览上传文本文件。无法取得的原文/转录仍是未取得，不补造。

## P05 安全证据读取

沿用 GET `/sources/{source_id}` 元信息与 POST `/evidence/read` 读取。后者只允许一个 `source_id` / `external_document_id` / `learning_outcome_id`，以及现有绑定字段 `source_revision`、`content_hash`、`locator`、`quote`、`claim_ids`。已有领域层检查 owner、分类、版本、不可变哈希/对象完整性。只读 POST 不产生幂等写入回执，但 cookie 浏览器仍必须通过 CSRF。

HTTP 返回只读投影：kind、对应证据 ID、可用的 revision、content_hash、text、title、text_basis、body_complete、分类/时间/locator。外部正文记录没有 revision 时不虚构版本。去除内部 object_path、raw_path、owner、完整 descriptor、学习 payload 与原始模型输出。前端按用户点击懒读取，并再次核对 ID/修订/哈希；关闭或 401 时移除文本，不加载来源中的外部图片/HTML/脚本。不提供整个 RAW/附件下载并不等于缺失文本已补齐。

修改/删除沿用 POST `/changes`。前端固定 `entry_id`、`base_revision`、`operation`、候选 title/body、原 evidence_refs 与 publication_intent，保留基础/候选全文对照及单独确认。只创建提案，不调用 apply，不提交审批证明，不绕过独立复核/Writer。历史版本只读，需返回当前版本才能提案；没有证据时明确不能提案。删除为可追溯 tombstone 提案，不永久清除历史/RAW。BASE_STALE 需要重新读取核对。

明确缺口：领域层不支持 merge，不能用更新正文冒充条目合并。跨应用学习入口须有真实接收页面、exact entry_id/revision 权限回读与显式用户关联，不能把正文塞 URL，不能把条目 ID 冒充 card ID；独立接收合同仍由学习工作流实施。

## P11 恢复与取消

POST `/tasks/{task_id}/resume` 或 `/cancel`，唯一 body 字段 `expected_revision` 为正整数；需要 user、knowledge:write、Idempotency-Key。同键同参成功回执在状态判断前重放，改参同键冲突，新键旧版本拒绝。

- resume：waiting_auth/quota/user/review/input、blocked、partial、retry_wait -> queued。
- cancel：以上状态及 queued -> cancelled。
- running、所有终态及 reconciliation_required 均拒绝；不抢占工作租约、不清除 checkpoint/result、不撤销已完成副作用。
- 成功只表示已排队/已取消，不代表任务完成、认证/额度已恢复或知识已撤回。终态重做和运行中协作取消不在该合同中。

前端所有新写表单第一次发送后冻结输入；未知网络结果重试复用同一 body/key。关闭/路由离开后的迟到结果不导航、不回填；401 全局清除私有内容。失效材料及本机 synthetic fixture 会话不能补充真实材料或提交真实知识修改。

## 实测范围

- Python 新增 38 项真实临时 SQLite/cache/Git/ASGI 安全合同检查，覆盖并发/重启幂等、旧 keep 失效、保留/过期/运行中拒绝、原材料与附言隔离、强分类保留、正文去重不丢附言、非法字段/Unicode/体积、终态/租约/待对账拒绝。
- 本工作树全部 knowledge_platform：767 passed / 2 skipped。两项 skipped 需要另行验证的真实离线 ASR 权重与语音，不能算通过。
- 新增21项组件/传输测试；合计93项单元/DOM通过。22份真实临时ASGI响应经11项前端解析合同检查。
- 既有17项配对ASGI检查、类型/格式均已复跑通过。真实浏览器/Windows/真实Owner/真实模型E2E仍未通过，不因这些局部结果宣称交付完成。
- 本批未安装开发依赖、未构建生产包、未创建或激活凭据、未 push 远端。

### 文本附件增量检查

- 新增 33 项真实临时缓存/SQLite/ASGI 检查，包含原始字节、私有保留、类型/体积/编码、幂等/CAS、TTL、运行/对账拒绝、测试模式上传开关。
- 新增 12 项合成 FileReader/DOM/传输检查；私有 Wiki 合计 105 项通过，typecheck 与 format:check 通过。23 份实际 ASGI 响应通过 12 项解析合同，既有 17 项认证合同通过。
- 本增量仍未运行真实浏览器/Windows/真实 Owner/真实模型 E2E，不改变尚未完整交付状态。

- 附件完成后本工作树 knowledge_platform 全量复跑：800 passed / 2 ASR 资源 skipped；ruff 与 git diff --check 通过。
