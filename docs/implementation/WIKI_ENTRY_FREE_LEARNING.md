# 正式知识条目的独立自由学习

## 语义与权限

用户明确选择已授权正式条目的精确 `entry_id + revision + content_hash` 后，创建独立 `wiki_entry_learning_context`。不依赖工程机会、不创建 `knowledge_cards/growth_tasks/opportunities`，不改旧练习的两题/完成规则。阅读状态恒为 `reading`，不产生掌握分或完成时间。本人原话始终与只读正式正文分开。

每次读取、保存、提交和恢复提交记录，都重新向统一后端读取该精确正式版本并核验所属身份与内容摘要。无配置、无认证、权限撤回或历史版本不可读时失败关闭；不能把较新版本或本机正文缓存当替代。设备/服务重新绑定后，旧上下文不可跨绑定读取。

本机 SQLite 只存来源标识/摘要、版本化本人原话与幂等收据，不复制正式正文或凭据。升级至 schema 33，保留所有旧表。该存储不是加密沙箱；必须位于既有受保护本机应用状态范围，不得提交源码仓库。

## API

前缀 `/api/growth/local/knowledge/entry-learning-contexts`，沿用学习服务 token/Origin 边界，不借用私有 Wiki 浏览器会话，也不新增身份。

- `POST /`：`{request_id: UUID, entry_id: opaque128, revision: 正整数, expected_content_hash: 64hex}`。同授权身份/服务/设备中的同一来源精确修订只创建一个 context，网络响应丢失后可 GET 列表恢复。同一 request_id 改载荷拒绝；来源哈希不符先拒绝，不建本机记录。
- `GET /?limit=20&offset=0`：`{items:[Summary],has_more}`，limit 1–100，未知/重复查询参数拒绝。Summary 为 `context_id,source_kind:"wiki_entry",context_version,entry_id,revision,content_hash,title,note_sha256,created_at,updated_at,state:"reading"`，不含原话/正文。分页多读的一项也先授权，不泄露不可读项的数量信号。
- `GET /{context_id}`：Summary 加 `entry`（既有精确正式版本安全投影）和 `note_text`。额外 owner/path/原始 metadata 不返回。
- `POST /{context_id}/note`：`{request_id:UUID,expected_context_version:正整数,note_text:最多20000字}`。在 SQLite `BEGIN IMMEDIATE` 内核对 CAS、更新和保存收据。改变文本才将 context_version 加一，同文本不增版。重放同请求返回原收据，重新 GET 可获得当前版本；同 key 不同载荷为409。
- `POST /{context_id}/understanding/submissions`：`{context_version,note_sha256}`，202 仅表示已持久接收或领域受理。服务端从保存的原话取精确 UTF-8 快照，不接请求正文/分类/模型/完成字段。原话 CAS 与 outbox 持久快照在同一原话写锁内核验，远端请求不占用 SQLite 锁。
- `GET /{context_id}/understanding/submissions` 和 `GET /{context_id}/understanding/submissions/{64hex}`：复用开放理解 Submission，身份字段为 `context_id/context_version` 而非 card_id。GET 只读取、从不发送 outbox。已有不可变 pending 快照可由用户显式以旧版本/旧摘要重递；不能用新原话替换旧提交。

创建和保存返回真实 context 明细；原话初始为空，context_version 初始为1。POST 不应自动认证重放。页面在授权读取成功前不得展示本机缓存原话；401/403或无法确认权限的错误须隐藏正文、本人原话及候选，保留未保存输入不等于允许未授权渲染。

## 候选与正式写入

成果使用 `submission_kind=wiki_entry_understanding`，绑定 context 版本、原话摘要、原来源版本/摘要与独立 UUID5 理解目标。本人原话最低 personal，更严格的来源/部署分类全部保留。`result` 仅 `{practice_kind:"open_response",graded:false}`。

通用生成器对此类型强制独立唯一目标，禁止来源 ID 等于理解目标，也拒绝显式传入其他目标。模型的标题/别名匹配不得触发自动合并回来源或其他条目。生成模型仅获得此次本人原话及该理解目标已有的历史证据，不自动获得来源正文或无关私有目录摘录。

其余仍复用真实 outcome → 持久任务 → 生成 → 独立复核 → Writer 策略。候选是只读差异，普通策略可能自动提交，重大立场/隐私等变化仍需本人批准；不是“仅预览”。只有可核验的 committed/indexed 状态和正整数 committed_revision 才称正式写入，私有写入不等于公开。没有模型或授权时显示真实 blocked/waiting，不造成功，不启用收费回退。

## 验证与未完成边界

受限本机验收可显式复用纯 `context_authority_key(base_url,device_id,owner_id)` 与 `verify_saved_learning_outcome(path,facts,*,backend_url,device_id,owner_id)` 回调。后者只读已存在 schema33 本机库，缺库/重解析路径/不符一律 False，不初始化或迁移；用真实 context 行及保存原话的幂等收据核对当前/历史版本。它不是独立授权机制，调用工厂必须将 path 固定在已批准隔离 desktop 状态目录，并独立验证 owner、固定材料 manifest、来源正式版本的生成/复核/Writer 同链收据。严禁从 HTTP 接收路径或以任意 UUID/摘要替代真实保存事实。SQLite 只读连接使用现有 WAL/SHM 协调机制，不改业务数据，不是操作系统沙箱或防恶意本机用户的承诺。

`test_wiki_free_learning.py` 使用合成原话、真实 ASGI/SQLite/Git 和显式 fixture 模型，覆盖无工程卡创建、精确历史版本、双击/并发CAS/重放、离线不可变快照、无认证/撤权/缺服务、分类不降低、源标题碰撞不覆盖、独立复核/Writer差异与禁用模型。旧理解桥、生成器和迁移回归必须共同运行。

这些不是浏览器、真实模型或 Windows E2E。真实启动必须使用另外经批准的 operational 身份/配置；local-test 安全模式仍禁模型/worker。受限 owner acceptance 激活由独立模块实现，启动标志不代表用户授权，本提交不生成或激活任何真实凭据。
