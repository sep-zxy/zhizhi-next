# 真实 owner 浏览器与条目学习上下文：受限后续合同

本文件是实现建议，不是已实现功能，也不授权生成/激活真实身份、配置新凭据、外部 OAuth 或个人服务器账号。

## A. 真实 owner 浏览器会话

### 当前缺口

候选只有隔离测试身份的 LocalTestBrowserSessions；正常 identities 配置可以服务 bearer 客户端，但并不因此建立浏览器会话。私有网页不能借学习服务 token、把 bearer 放浏览器存储，或依靠 owner 请求头获得身份。测试模式禁止 worker/model/渠道是正确边界，不能删除限制来获得业务 E2E。

### 建议的最小能力

1. 新增独立 `OwnerBrowserSessions` 受信任工厂，默认关闭，仅准确 loopback origin；使用显式配置、已获准的现有 user identity。与 local-test 会话互斥，不复用 TEST_OWNER，不把 fixture:false 当客户端申请权限。
2. 另设 `/api/knowledge/v1/auth/owner/*` 与独立 cookie 名。浏览器显式申请短期码并持有内存 proof；本机批准工具用已经配置的 owner 身份批准准确的一次性码，不创建 bearer、不输出 token、不经 URL/剪贴板/日志传递秘密。
3. 复用已经审查的 cookie + 所有请求独立内存 proof + 写入 CSRF 的结构，但策略独立：Host/Origin/重复头拒绝、120 秒一次性配对、15 分钟会话、批准轮换、重启/重绑定撤销、显式坏 bearer 不回退。cookie 单独不能换取 proof/CSRF；不把浏览器同主机不同端口视为天然安全。
4. 会话 scope 仅既有 user 身份与网页所需 read/write/review 的交集；无 apply、worker、服务管理或通配扩大。后台服务仍由原 deployment policy 的分离 actor 执行，不由浏览器申请 worker 开关。
5. health 分别报告 service/identity/browser mode/status；无配置持续 waiting_auth。私有 UI 单独接 owner 模式，不能简单放宽当前测试协议的 fixture:true 校验。禁 trace/HAR、身份内容截图、自动配对/续期。

以上能力可在云端写代码、用合成身份验证，但真实配置或批准会话必须另有明确 owner 身份/目录/权限授权。当前“Windows 专用测试令牌和设备身份”许可不能推定覆盖它。无法防御同机已掌握账户权限的恶意原生进程，不宣称它是完整生产 SSO。

### 必要测试

缺配置/不同 owner/服务 actor/测试与 owner 混用拒绝；过期、重放、撤销、配置重绑定；跨 Origin/端口 cookie 无 proof 拒绝；写 CSRF；坏 bearer 不回退；最小 scopes；秘密不进日志/URL/存储；已登录普通业务与重大决定分开。真实 Windows 会话与真实 owner 操作仍单列，不能用合成测试替代。

## B. 指定正式条目版本到学习上下文

### 当前缺口与最小可行边界

当前学习以既有 concept/card 为中心，P05 的 entry_id/revision 深链没有受信任接收合同。不能把 entry_id 当 card_id，不能自动创建一张伪学习卡，更不能把历史 revision 静默换成最新版本。现有候选成卡依赖真实机会/项目证据，直接拿正式正文假装该证据会绕过既有模型/来源边界。

建议分两步，仍只使用统一正式知识为正文主库：

1. 只读 `GET /api/growth/local/knowledge/entry-context?entry_id=…&revision=…`，要求既有学习服务身份和 unified 配置。远端按精确 revision 校验读取，回传 title/body/hash/revision、最小授权引用与可匹配的现有 card 列表。404/权限不足/历史撤回明确拒绝，不回退 latest；打开深链没有任何写入。
2. 用户明确选择后 `POST /entry-contexts`，绑定 entry_id/revision/expected_content_hash + 目标现有 card_id/version 与稳定请求键。事务内保存“正式版本学习引用”，只增加学习上下文关联，不复制为第二份可写正式条目、不完成练习、不调用模型。跨 owner/卡片失效/内容摘要不符拒绝，重放返回同一关联。上下文给本人原话页面引用，不把已发布旧正文当成本次本人表达。

若用户没有现有 card，须另补真正的“从正式条目建立学习上下文”成卡路径：记录明确来源版本，沿既有模型许可生成必要材料并由用户确认目标，而非编造项目机会。当前源码没有可直接套用的无副作用成卡函数，因此这一部分在实现与测试前仍为 V1 缺口。

需要的小型追加迁移/读模型应保存稳定 entry/revision/hash 引用；历史内容只由正式服务授权读取。本文不先创建表或替原接口扩大权限。应在本人理解桥合并后与原学习 UI、Wiki UI 工人冻结字段再实施。
