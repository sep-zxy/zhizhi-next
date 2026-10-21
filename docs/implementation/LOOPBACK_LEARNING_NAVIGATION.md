# 私有 Wiki 与学习服务的显式导航

这是导航装配，不是跨应用认证桥。启动器先为学习 viewer 分配端口，将同一个 `http://127.0.0.1:<port>/` 通过平台 CLI `--learning-viewer-url` 传入，然后在该端口启动 viewer 并验证健康。没有配置时保持 `null`，不能猜端口或把平台根地址当学习入口。

- 仅规范 HTTP、127.0.0.1、1024–65535 整数端口、根路径 `/`；拒绝 userinfo、query、fragment、其他 host/协议/路径、前导零、空白和平台自身端口。CLI 限制平台也绑定 127.0.0.1。local-test 模式不能开启此导航。
- `/health` 的 `learning_viewer_url` 仅为上述非秘密配置，不能通过 HTTP 请求修改。其存在不证明目标就绪、已认证、已有学习卡或已完成关联。无身份外壳仍拒绝全部领域 API，不创建账本。
- 私有前端只从该可信健康字段读取跨端口入口，并再次严格验证；保留原根相对配置的限制，不泛化任意绝对 URL。用户明确点击后仅将 `entry_id` 与精确正整数 `revision` 放入已有 `/#/growth?view=entry-context&entry_id=…&revision=…` 路由。
- 不传 bearer、cookie、pairing proof、CSRF、原文或隐私正文，不自动请求另一端口，不增加 CORS 许可。学习服务仍使用自身既有认证与重新读取精确修订的权限检查；没有既有合法学习卡时仍明确未完成，不自动造卡。

代码和合成 ASGI/CLI/包装测试不等于实际 Windows 跨应用浏览器验收。真实导航、重新鉴权、历史版本读取、显式关联及会话丢失仍须在 GitHub 构建的实际候选上验证。
