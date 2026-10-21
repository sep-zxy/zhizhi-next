# 获准 Windows 测试身份与浏览器配对

仅用于用户于 2026-10-20 明确批准的 `C:\Code\zhizhi-e2e-20261020` 隔离验收范围，不是生产登录、个人服务器身份或新模型 OAuth。默认关闭；无身份启动仍只提供静态壳与健康检查，所有知识 API 保持 401。

## 服务端边界

- 固定测试 owner `portable-e2e-fixture`，固定测试 Actor，仅 `knowledge:read`、`knowledge:write`、`knowledge:review`。没有 `*`、apply、worker 权限。
- 测试配对模式拒绝 worker、模型、workflow、渠道、媒体及外部 domain/intake 服务注入；HTTP 不得改变这些设置。
- 测试身份是 `user`，现有领域策略不会对 user 执行机器身份的 `allowed_classifications` 过滤。因此不能声称该字段强制 public-only。必须使用新建专用 `data/local-test/platform` 测试账本及测试 owner，不能指向生产资料；测试内容只使用明确标记的 public synthetic fixture。跨 owner 读取仍真实拒绝。
- 配对批准与领域 API 必须使用同一个服务端 bearer 映射。应用工厂重新绑定时清除已有 pending/code/session，不能继承另一身份来源已批准的会话。原始 bearer 不给浏览器、不进 URL、环境变量、命令行或日志。
- 仅精确 `http://127.0.0.1:<port>`；校验 loopback 客户端、Host、Origin 与 Fetch Metadata。非 loopback、错端口/Host、跨源、重复敏感头全部拒绝，无 CORS 放行。
- 配对有效期 120 秒、一次使用；会话仅服务端内存，最长 15 分钟，无持久化 session。服务重启失效。
- cookie 使用 HttpOnly、SameSite=Strict，Path 限定 `/api/knowledge/v1`，不设置 Domain 或持久期限。此明确 HTTP loopback 测试模式不能设置 Secure，也不能用于远程 HTTP。
- cookie 不按端口隔离，因此另给浏览器一份独立随机 `pairing_proof`，只在内存持有，所有 cookie 鉴权请求均需 `X-Wiki-Pairing-Proof`。服务端仅保留摘要。仅在其他 loopback 端口截获 cookie，不能调用 session 取得 CSRF 或读取数据。
- 写请求额外要求精确 Origin 和 `X-Wiki-CSRF`；显式错误 bearer 不回退 cookie。浏览器内存丢失后重新配对，不能从 cookie 自动恢复证明。
- 此机制不能防御已控制本机用户/进程内存或浏览器的恶意软件，也不是 OS sandbox。不要据此扩大到真实生产身份。

## 前端接口

以下均在 `/api/knowledge/v1/auth/local-test` 下，响应不是领域的 `{data: ...}` 信封；所有响应 `no-store`。

1. `POST /request`，body `{}`，必须同源。返回 `{status:"waiting_approval", pairing_code, pairing_proof, expires_in_seconds:120, fixture:true}`，同时设置 pending cookie。
2. 页面展示配对码，由获准 Windows 包内命令读取本地测试身份，向 `POST /approve` 提交 bearer 和 `{pairing_code}`。网页不能取得 bearer；此 endpoint 不支持 cookie 代替批准。
3. 页面将 proof 只保留在内存。`GET /session` 带 cookie 与 `X-Wiki-Pairing-Proof`，未批准为 `waiting_approval`；批准后轮换 cookie，返回 `{status:"authenticated", csrf_token, expires_in_seconds, fixture:true}`。无有效 pending/session 返回 `waiting_auth`；有 cookie 但证明不正确返回 401。
4. 之后所有知识请求 `credentials:"same-origin"`，带 `X-Wiki-Pairing-Proof`；所有写请求再带 `X-Wiki-CSRF`。CSRF 同样只在内存，不得记日志、放 URL 或 local/sessionStorage。
5. `POST /logout` 带 proof、CSRF、同源 Origin；撤销服务端 session，删除 cookie，返回 `waiting_auth`。

配对码与 proof 不得持久存储或进入审计。页面只显示码与短期有效期，不显示 proof/CSRF。请求频率、pending 数量、session 数量和 body 大小均有限制。初始化、配对与页面成功不等于真实模型或全部 E2E 完成。

## 验证边界

云端测试只使用隔离合成 token/状态，覆盖错误 bearer、错误 owner、伪造 owner 头、跨 owner、一次性批准、cookie 轮换、cookie 无 proof、Origin/Host/CSRF、过期/注销、超限及生产配置拒绝。真实 Windows DPAPI、包内初始化、浏览器配对、模型与端到端验收仍须用 GitHub 产物经 Git 同步后另行验证。

## 包内 Windows 初始化与启动契约

模块为 `ahadiff.knowledge_platform.local_test_identity`，只能在已校验 portable 环境的 Windows 上运行。不从云端生成再传入真实身份；CI 只打包代码，绝不运行初始化或配对。获准 ROOT 只能是：

- `C:\Code\zhizhi-e2e-20261020`
- `C:\Code\zhizhi-e2e-20261020\git-delivery\releases\<12位小写源码hash>-<run>-<attempt>`

根及路径任一级 symlink/junction 都拒绝。沿用 portable runtime 检查，Python、Git、HOME、TEMP、导入和状态均须包内。该验证不声称是 OS sandbox。

初始化命令为包内 Python `-B -s -X utf8 -m ahadiff.knowledge_platform.local_test_identity initialize --approve-local-test-identity [--port 8766]`。入口必须保留显式批准参数，不由通用启动自动追加。工具只生成随机测试 bearer、随机设备标识，写入 `ROOT/data/local-test/identity.json`，使用 Windows CurrentUser DPAPI 加密；不使用 machine-wide 标志，不更改 ACL/系统网络设置，不读取 Codex、浏览器或其他既有凭据。设备标识不是硬件标识，也不是外部账号登录。

DPAPI 调用依据 Microsoft 的 [CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) 与 [CryptUnprotectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata)：使用 `CRYPTPROTECT_UI_FORBIDDEN`，不用 `CRYPTPROTECT_LOCAL_MACHINE`，释放返回缓冲区，并校验解密后的固定 schema、摘要、包根、owner、scope、端口。Python 的内存对象不能保证完美擦除；不声称抵御同账户恶意软件或系统内部 DPAPI 行为。

同时创建空 `data/local-test/platform` 与独立 `data/local-test/launch.json`，后者恰好三个键：

```json
{"mode":"local-test","local_test_identity":"data/local-test/identity.json","platform_port":8766}
```

完整既有测试身份只可只读校验后返回 `already_initialized`，不重置、不轮换、不覆盖；部分文件、冲突端口、已有无效目录或配置直接失败，不能覆盖修复。生产 `config/launch.json` 不读不改。

独立测试 launcher 读取该三键文件，不与生产配置合并，调用服务 CLI：

```text
-m ahadiff.knowledge_platform
--state-directory <ROOT/data/local-test/platform>
--local-test-identity <ROOT/data/local-test/identity.json>
--private-web-root <ROOT/app/wiki-private>
--host 127.0.0.1
--port <绑定端口>
```

`--local-test-identity` 与 `--identities-file` 互斥；测试模式拒绝 owner、模型、provider、ASR、worker、渠道、auto-commit 等生产配置。根、身份位置、状态位置、host、port 都必须与加密内容匹配。默认启动仍不读取这个身份，不隐式启用配对。

配对命令为包内 Python `-B -s -X utf8 -m ahadiff.knowledge_platform.local_test_identity pair --pairing-code <页面显示的20位大写码>`。只读取上述自有测试文件，直连绑定 `127.0.0.1` 端口，禁止代理、DNS 目标、重定向；只向固定 `/approve` 发送 bearer。成功/失败只输出固定状态/错误码，不输出 token、device ID、配对码、路径或原始异常。页面码不能上传、写日志或留入 Git。

目前云端 DPAPI 测试使用显式 synthetic 加解密替身，严格标记为 fixture；它验证文件/路径/配置/拒绝/CLI 约束，不证明真实 Windows DPAPI 或浏览器 E2E 已成功。
