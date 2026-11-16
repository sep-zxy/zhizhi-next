# 私有 Wiki Owner 配对与精确修订交接

本批为已批准 V1 的默认关闭能力接线，不创建/启用真实身份，不修改后端会话或权限，不将局部测试算作完整交付。

## 独立认证协议

页面先读取真实 `/health.browser_authentication_mode`。`disabled`/缺字段保持待配置，不调用配对；未知模式拒绝。`local_test` 只调用 `/auth/local-test/*` 且严格要求 fixture:true；`owner_loopback` 只调用 `/auth/owner/*` 且严格要求 fixture:false、authentication_mode:owner_loopback。每次新申请重新核对服务模式，不自动批准、不轮询、不展示批准成功直到服务确认。

Owner 已认证响应必须给出只含 knowledge:read/write/review 的唯一 scopes 集合并包含 read；缺失、重复、通配或额外权限拒绝。pending/waiting 不得提前携带 scopes。只读 Owner 不能发补料、附件、投递、任务控制或提案；本人确认另需 review。后端仍是最终授权边界，前端不发送 worker/apply/伪审批字段。

proof/CSRF 仅保存在 WikiApi 私有内存，不进入 React 快照、URL、storage、日志。cookie 请求保持 same-origin；领域读发 proof，写加 CSRF；health 从不带这些头。刷新须重配，过期/401/CSRF/来源拒绝会清私有内容。模式切换或非法模式清会话；旧响应/旧 JSON 解码不得覆盖新会话。注销不确定时清本地但不声称服务端已撤销。

local-test 原严格 fixture 检查和禁止真实链接、补料、附件、知识修改均保留。Owner 只是复用既有 TTL/proof/CSRF 原语，不以放宽 fixture 检查冒充接入。正文/来源/候选修改仍走原版本、独立复核、重大决定与 Writer。

## P05 精确修订交接

`health.learning_viewer_url` 是受信任启动器给出的唯一跨端口入口，严格接受 `http://127.0.0.1:<1024..65535>/`；拒绝前导零、其他 host/scheme、userinfo、路径、query、fragment。null 明确未装配，不猜端口。旧宿主显式 window 配置仍只接受原有根相对路径，不泛化为任意 URL；新 health 字段出现时优先且 null 不回退。

点击链接仅附 `#/growth?view=entry-context&entry_id=...&revision=...`，ID 按正式 opaque 128 字符规则、修订为正安全整数。旧 query/hash 不沿用，正文、token/proof/CSRF、card 不传递。历史阅读链接绑定当前实际读到的历史修订，不回退 latest。删除条目不提供学习交接。入口配置不代表 viewer 已认证，接收端仍独立授权读取并要求明确选择既有 card 后确认引用；缺 card 不自动造卡。

## 测试证据与未完成部分

- 私有 Wiki 153 项单元/合成 DOM/传输通过，类型与格式通过；新增跨模式、scope、TTL、401、迟到 health、内存清理、可信 URL 和精确修订边界。
- `check_owner_auth.py` 用真实临时 ASGI 与合成内存身份验证 21 项，包含准确 owner 协议、只读 scope、拒写、cookie 单独无效、注销与可信学习入口。不落盘任何配对秘密。
- 未创建真实 Owner、未执行真实 Windows/模型/浏览器全链路，未本地生产打包或远端写入。Playwright 浏览器旅程另补并交 GitHub 实际执行；列举或 DOM 通过不算浏览器执行通过。
- true merge 仍未实现，按 TWO_ENTRY_MERGE_DESIGN.md 记录风险与验收缺口。

后续严格边界与精确历史校验补丁完成后，单元/DOM合计175项通过；浏览器28项仅列举，执行要求见 PRIVATE_WIKI_BROWSER_COVERAGE.md。
