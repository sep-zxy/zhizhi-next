# 默认关闭的 owner 浏览器能力

本能力的代码和合成测试已实现；没有生成、写入或激活任何真实 owner 凭据。它不改变隔离 local-test 模式，也不代表 Windows 真实业务验收通过。

## 显式配置边界

既有受保护 identities 文件须额外明确 owner_browser 对象，且只允许 owner_id、actor_id、origin 三键。origin 必须是准确的 http://127.0.0.1:非特权端口，不接受 localhost、远端、路径、查询或用户信息。已有 identities 中必须存在准确的 user actor，不接受测试 owner、service/agent、缺 read 或同名不同权限的模糊配置。

服务须同时显式传 --identities-file、--owner 和 --owner-browser-actor；文件中的 owner/actor/origin 必须与 CLI 和监听端口完全相同。只有文件存在但没有该开关时，浏览器仍 waiting_auth。无身份壳与 local-test 模式禁止此开关。配置或启用真实身份需要独立授权，不能从本次测试身份许可推导。

## 浏览器合同

- POST /api/knowledge/v1/auth/owner/request，空对象；返回 waiting_approval、20 位一次性 pairing_code、只保留在浏览器内存的 pairing_proof、120 秒有效期。
- POST /api/knowledge/v1/auth/owner/approve，仅由本机批准工具用受保护文件中已有 bearer 调用，正文 pairing_code；不会把 bearer 发给网页。
- GET /api/knowledge/v1/auth/owner/session：cookie 加独立内存 proof 才能兑换或读取会话；批准后轮换 cookie。authenticated 返回 csrf_token、剩余秒数和 scopes。
- POST /api/knowledge/v1/auth/owner/logout：cookie、proof、同源 Origin、CSRF 全部验证后撤销。
- 所有响应 fixture:false、authentication_mode:owner_loopback。与 fixture:true 的 local-test 协议不可混用。两种策略复用同一会话原语，但路由/cookie/身份政策独立且装配互斥。
- health 新增 browser_authentication_mode=disabled|local_test|owner_loopback，既有 identity/browser 状态分别保留。configured 表示能力已配置，不代表某浏览器已登录。

所有领域 cookie 请求都需要 X-Wiki-Pairing-Proof，写请求另需 X-Wiki-CSRF 和准确 Origin。scope 仅配置用户已有权限与 read/write/review 的交集，不含 apply、worker 或通配扩权。浏览器无法启动模型/后台/自动提交；这些仍由原部署政策明确开启，重大决定仍走原本人审批。显式坏、空或重复 Authorization 均不能回退 cookie。

## 本机批准工具

包内入口为 python -m ahadiff.knowledge_platform.owner_pairing --identities-file <已批准的受保护文件>。一次性码只从 stdin 读取，不接受码或 URL 命令行参数。工具只读取现有文件，不创建身份；复用 secret 配置加载器，拒绝符号链接和 POSIX 宽松权限，便携模式还要求位于包内获准路径。仅使用配置绑定的 loopback 端口和固定 approve 路径，HTTPConnection 不用环境代理、不跟随重定向、不重试。只输出 pairing_approved 或固定 OWNER_PAIRING_NOT_CONFIRMED，不输出异常、路径、token、码。

批准失败或响应丢失不能宣称未执行，应由浏览器显式查询原配对状态，不自动申请新身份。不同 loopback 端口共享 cookie 的风险由独立内存 proof 缓解；这不是抵御已控制本机账户的恶意原生进程的完整 SSO。

## 证据边界

合成 ASGI 测试覆盖 owner 绑定、权限交集、跨 owner 数据拒绝、cookie 单独拒绝、CSRF、Host/Origin、TTL、重放、重绑定、坏 bearer、默认关闭与 local-test 禁生产选项。批准工具的网络由明确 fixture 替代，检查准确目标、拒绝重定向/错误协议、固定摘要、文件保护。真实浏览器、真实 owner 批准与真实模型/Writer E2E 仍待独立授权和运行。

Windows 限制：当前 JSON secret loader 没有验证 Windows DACL，也没有正常 owner 的 DPAPI provisioning 路径。因此文件型 owner CLI/批准工具在 Windows 明确拒绝激活，等待单独的受保护身份供给实现与批准；不能以文件存在或 local-test DPAPI 替代。程序化工厂是受信任装配边界，不是可由网页申请的权限。user actor 的 allowed_classifications 不构成读取分类沙箱；浏览器权限交集只限制操作 scopes，数据隔离依赖准确 owner 和正式服务授权。
