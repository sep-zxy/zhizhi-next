# 私有 Wiki V1 浏览器旅程与执行边界

## 本批实际结果

本云端只执行 TypeScript 检查、格式检查、175 项单元/合成 DOM 测试，以及 Playwright `--list --project=chromium-private`。28 项浏览器旅程已被发现，未在本环境启动浏览器。此前本机浏览器限制没有重试、绕过或换通路。浏览器真实执行交给 GitHub，列举不是通过。

对集成根 2fac3e9 的后端只读导入，另实际复跑：local-test ASGI 17 项、Owner ASGI 21 项、23 份领域响应捕获及12项前端解析合同。临时 SQLite/Git/缓存及合成内存身份只在本工作树的测试隔离环境使用，不创建/启用真实身份。这些也不等于 Windows、真实模型/渠道/Writer 全链路验收。

## 必须保留的浏览器最低覆盖

唯一项目 `chromium-private`，固定一个 Chromium 项目，不增加矩阵稀释计数：

- `wiki.spec.ts`：7 项既有首页/目录/正式条目/历史/任务、搜索、401、迟到导航、重要确认、保留决定、响应式与第三方媒体边界。
- `local-test-pairing.spec.ts`：2 项既有本机显式配对/空账本/注销及默认关闭。更新为显式 health 模式，不再向 disabled 服务尝试配对。
- `v1-actions.spec.ts`：13 项。P03 正文/附言分离与同键重试；真实 FileReader 文件字节选择、取消、BOM/CRLF上传、点击瞬间过期和旧修订拒绝；P05 绑定证据读取/关闭、错绑定拒绝、精确历史引用链接、修改/删除提案及迟到回执不恢复编辑；P11 resume/cancel 确认、CAS/同键重试及运行/终态拒绝。
- `owner-access.spec.ts`：6 项。只读 scope 门禁、取消待批准、待批准过期、已认证过期清正文/搜索、跨模式响应拒绝、不确定注销不伪成功。

合计 28 项，旧 9 项完整保留。所有新 API 拦截按 URL pathname 精确前缀，不拦截 Vite 源模块。未知 API 硬失败；写请求核对 Owner proof/CSRF、无 bearer、方法、版本、正文/附言隔离与幂等键，不能靠通用成功 mock 混过。

夹具都是明确的合成文本/文件/身份。Trace 可记录合成值，但绝不能把此配置用于真实用户、真实配对、真实令牌或真实材料。新旅程只验证网页与受控 API 合同交互，不代替真实后端/模型执行。

## GitHub 门禁

CI 工人拥有单独 JSON 结果门禁，按上述四个文件及唯一项目统计实际通过，禁止 skip/xfail/flaky/重复执行/错误；`--forbid-only --retries=0`，不以学习端旅程替代私有 Wiki。GitHub 不复用已有 webServer，清除本地浏览器可执行路径覆盖，使用锁定 Playwright 的官方 Chromium。任何失败都应阻止生产装配，不通过削减旅程、放宽断言或把列举报告当运行报告修复。

真实 Windows 使用新包、真实授权身份与实际模型/Writer 的完整流程仍待执行；merge、非文本附件等明确缺口不因这批测试而宣称完成。
