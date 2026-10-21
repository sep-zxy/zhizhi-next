# 本人理解到统一知识候选的最小桥

## 行为与边界

保存 note 与提交成果是两个动作。此桥读取已保存本人原话，不要求旧练习完成，不写 stage/completed_at/分数，不伪造雷达接受记录，不使用 AI 讲解补齐用户表达。

显式提交后进入已有 LearningOutcome → knowledge.generate → 独立复核 → Writer 策略。按钮须说明“提交理解，生成并复核候选”，不能叫“仅预览”：普通变更是否自动写入取决于已配置的部署策略，重大立场/核心变化仍等本人决定。没有模型、认证、允许分类或服务时保留真实等待/失败，不换收费模型。

旧卡片 `/wiki/draft` 流程不修改，本桥不调用它，不同时创建第二条手工 draft 写入链。新生成 ChangeSet 只读；改本人草稿后可提交新事实版本，不覆写旧成果。正式写入和公开是不同状态。

## HTTP 合同

前缀 `/api/growth/local/knowledge`，全部要求学习服务既有 `X-AhaDiff-Token` 与 Origin 守卫。此 token 不给个人 Wiki 浏览器使用。

- `POST /cards/{card_id}/understanding/submissions`：只收 `{card_version:正整数, note_sha256:64位小写十六进制}`；摘要为已保存原话的精确 UTF-8 SHA256，不 trim、不换行归一化。不收正文、owner、device、分类、模型或完成状态。202 仅表示本地持久接收/领域受理，并非生成或写入成功。
- `GET /cards/{card_id}/understanding/submissions?limit=20&offset=0`：`{items:[Submission],has_more:boolean}`；limit 1–100。真实空列表不造样例。
- `GET /cards/{card_id}/understanding/submissions/{submission_id}`：一项 Submission。

Submission 字段：

```
submission_id: 64hex
card_id, card_version, note_sha256, user_text
delivery_state: pending | acknowledged | rejected
outcome_id, task_id: string | null
remote_state: not_submitted | unknown | available
task: 公共任务安全投影 | null
change: {
  change_id, state, entry_id, base_revision, committed_revision,
  title, path, original_markdown, candidate_markdown, evidence_refs
} | null
error: {code, retryable} | null
```

`evidence_refs` 是对象数组，只允许 source_id/learning_outcome_id/external_document_id/revision。task 不含 lease、payload 或原始模型输出。错误不回原始服务错误文本。change 必须实际引用该 outcome；旧正文按 base_revision 精确读取，失败不能拿最新正文替代。新建的 base_revision=0 对应空旧正文。committed_revision 只有明确 committed/indexed 且正整数才可显示正式写入；不据任务 succeeded 推断。索引或公开没有可靠读模型时保持未知。

新提交的 card_version 或原话哈希过期返回 409 `UNDERSTANDING_STALE`，零 outbox/外发。缺统一配置为 `WAITING_CONFIGURATION`，无认证为既有鉴权拒绝。非空与 20,000 字上限在服务端校验。

## 持久性、重试与隐私

- session 为 `understanding:{card_id}:{card_version}`，device 只取既有配置；DurableOutbox 根据完全相同事实复用版本，内容改变才分配新版本。submission_id 来自持久幂等键，不依赖浏览器 UUID。
- 原话与 result `{practice_kind:open_response, graded:false}` 作为不可变事实。部署 public 分类不能降低个人原话，最低 personal，更强分类保留；未配置分类不发送。
- 外发前落 outbox。超时可能已被远端受理，显示 unknown；GET 从不发送或 flush。acknowledged 只回读真实 task/change。
- 用户可显式“重试递送这份原话”，仅 pending/retryable，用该记录原 version/hash 再 POST；不自动认证重放。即使当前 note 已改，已存在的精确旧快照可安全重放，不能换正文。rejected 保留错误，不自动重发。
- 本机 local-test 模式的独立空账本、禁止 worker/model/渠道/真实 URL 等限制完全不变。该桥需要另外明确配置的 operational desktop/backend；不生成身份或读取其他凭据。

## 验证

`tests/knowledge_platform/zhizhi/test_understanding_bridge.py` 的 19 项为合成用户证据 + 真实 ASGI/SQLite/Git + 显式 fixture 模型，不是真实账号或 Windows E2E。覆盖精确原话、版本/哈希冲突、双击/重启、离线/无认证/缺配置、只读 GET、重大决定、no_change、真实 Writer、精确旧版差异、私有分类和路由守卫。

2026-10-21 本地完整知识后端与根 e2e 目录回归：779 passed，2 skipped（缺已核验 ASR 权重/语音），1 既有 Starlette 警告。真实模型、GitHub 学习浏览器旅程和新成品 Windows 业务闭环须分别验收，不继承旧候选的启动/配对结果。
