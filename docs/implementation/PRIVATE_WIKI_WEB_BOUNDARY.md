# 个人 Wiki 同源 Web 边界

## 已实现

`ahadiff.knowledge_platform.private_web.create_private_wiki_mount(directory)` 返回固定 `/wiki` 的 Starlette `Mount`，只接受显式绝对编译产物目录，要求真实 `index.html`。装配层应在普通 API 路由检查后追加该 Mount，不把它交给仅适用于 `Route.methods` 的重复路由检查。

前端为 hash 路由，例如 `/wiki/#/home`、`/wiki/#/entry/<id>`。不需要服务器 SPA history fallback，未知路径、缺失 JS/CSS 真实返回 404。静态层仅提供根 `index.html` 与 `assets/` 下受支持的 JS、CSS、图片和字体，不暴露 `.vite/manifest.json`、source map、源码、任意 HTML、配置或状态文件。沿用 Starlette 的 realpath 根目录检查，拒绝符号链接逃逸。

响应带严格同源 CSP、`nosniff`、`no-referrer`、`Cross-Origin-Resource-Policy: same-origin`、`Cache-Control: no-store`。无需 CDN、远程字体、内联脚本或 `unsafe-inline`。`/wiki/` 只包含通用界面代码，不含用户资料、身份或凭据。

该工厂不启动服务、不构建前端、不调用模型、不生成 token/cookie，也不改变 `/api/knowledge/v1` 鉴权。静态页可访问不代表已登录；无受信身份的 API 必须继续 401，由 UI 显示待认证。已用独立 ASGI fixture 验证这一点，不算真实浏览器身份 E2E。

## 已冻结装配与尚未接通部分

- 应用工厂参数：`private_web_root: str | Path | None = None`。仅配置后 mount，默认关闭；已以现有 portable 根约束验证路径，目标为包内 `app/wiki-private`，与学习域 `app/viewer`、公开 Wiki `app/wiki` 分离。
- CLI 参数：`--private-web-root <绝对目录>`。`--identities-file` 与 `--owner` 同时省略时进入 no-identity 模式；已有 identities 模式仍要求 owner，身份文件加载规则保持兼容。
- 当前知识服务身份只有受保护配置提供的 bearer，浏览器没有可信 session 来源。`credentials: same-origin` 不会凭空建立身份，不能把 loopback、浏览器来源、客户端 owner 字段或 growth token 当作本人登录。
- 未配置启动模式已实现：通用静态页与健康检查可用，知识 API 全部 401，连回调路由也不例外；不初始化领域账本、模型、worker、渠道或自动提交。CLI 拒绝在该模式同时传后台工作、model/channel/ASR/provider 等配置；工厂拒绝注入任何 backend service。该模式不等于身份配置完成。
- `/health` 在 no-identity 模式 HTTP 200，但 JSON `status=waiting_auth`、`authentication_status=waiting_auth`；launcher 只能将它记为壳可用/待认证。既有 bearer 模式仍为 `status=ready`、`authentication_status=configured`，后台故障仍 `degraded` 与 HTTP 503，不可当全 ready。两种模式当前均 `browser_authentication_status=waiting_auth`；bearer 已配置不代表浏览器会话已接通。

## 浏览器身份桥最低要求

真正启用身份桥前，必须明确既有受信会话来源与用户已批准的身份映射。配置新的身份、token 或持久授权需要单独批准，不能由开发或测试隐式产生。

建议身份桥由服务端可信 resolver 返回既有 `Actor`，不从请求 body/query、任意身份 header 或前端猜测 owner。会话应为 HttpOnly、作用域限定、SameSite 的 cookie，非 loopback HTTPS 还必须 Secure；原始 bearer 不传给前端、不进入 URL 或 localStorage。请求需严格 Host/Origin 匹配，跨站写请求拒绝，写操作需要同源和 CSRF 防护。若显式 Authorization 不合法，不应静默降级到 cookie 身份。配置缺失、过期、无法校验或权限不足必须保持 401/403，不能伪造成功。

后续新增默认关闭的本机测试配对边界，详见 [LOCAL_TEST_BROWSER_AUTH.md](LOCAL_TEST_BROWSER_AUTH.md)。仅该显式测试模式的健康信息可标记 `browser_authentication_status=configured`，它表示认证机制已装配，不表示任何浏览器已登录。默认壳与普通 bearer 模式仍 `waiting_auth`。交付仍需实际 GitHub 构建产物、Git 同步到获准 Windows 目录，再验证同源浏览器登录和全部真实 E2E。
