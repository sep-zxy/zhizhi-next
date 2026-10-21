# Windows 便携验收包

这是云端装配源目录，不是给 Windows 执行的安装工程。Windows 只运行完整发布 ZIP 中的启动器。

## 云端构建

Python 经已有 uv 环境执行。`build_bundle.py plan` 从项目锁文件生成 Windows CPython 3.13 x64 的纯 wheel 闭包，`fetch-wheels` 仅下载精确 hash 的官方 PyPI 文件，`prepare-runtimes` 校验官方运行时并在云端展开、交叉目标安装 wheels。遇 HTTP 拒绝或 hash 不符立即停止，不换代理或未批准镜像。

运行时固定为 CPython 3.13.16、Node 22.23.3、MinGit 2.56.0.2。原始包的来源与校验列在代码和产物 provenance 中。MinGit 2.56.0.2 修复 `GIT_CONFIG_GLOBAL=NUL` 回归，不使用存在该回归的 2.56.0。Python 原装 SQLite 静态版本为3.50.4，属于项目允许回补，仍必须实机验证，未替换原装 DLL。

模块输入见 ASSEMBLY.md。依赖、源码和原生文件清单与运行状态必须分别报告。

## 最终用户运行

1. 解压完整包到 `C:\Code\zhizhi-e2e-20261020`。
2. 双击 `diagnostics.cmd` 检查真实 Windows 导入、SQLite、安全路径、Node/Git。
3. 双击 `start.cmd`。首次没有已授权部署配置时只启动原应用页面，并明确显示统一平台和Pi为waiting_auth。这不是统一链路验收通过。
4. `stop.cmd` 只通知这个包的启动进程停止，不修改系统服务，不根据任意保存PID杀进程。

无账户身份时不创建测试token，不复制任何个人登录态。后续由明确授权的部署配置接入统一平台。`config/launch.json` 可引用包内已批准身份、模型、桌面与Pi环境配置；这些文件不是默认交付内容，也不由启动器创建。

已批准Pi环境JSON只允许 ZHIZHI_MODEL_PROVIDER、ZHIZHI_MODEL_ID、ZHIZHI_AGENT_TOKEN、ZHIZHI_USER_TOKEN、ZHIZHI_CHINA_TERMS_VERIFIED、ZHIZHI_MAX_OUTPUT_TOKENS。token只传子进程环境，不放CLI参数或报告。真实登录和身份配置仍需单独批准。

## 限制

- 只支持 Windows x64；云端检查不等于 Windows 运行成功。
- 未装Edge/Chrome时不自动安装浏览器；使用现有浏览器也只建立包内独立profile。
- 主应用、统一服务、Pi、动态Wiki、真实账号、ASR各有独立状态，不能互相替代。
- ASR独立组件和模型有自己的许可证与运行时检查；缺VC++运行时明确报阻塞，不静默系统安装。
- 所有应用管理的状态在包内，但操作系统自己的日志、Prefetch和安全产品记录不属于应用可控制边界。

## 本地动态发布

`publish.cmd` 只消费已审核的 `config/launch.json` 中 `publication_config` 所指向的包内配置，不创建授权。配置需要 repository、revision、domain_database、owner_id、policy、template_assets、staging、output、report、public_base_url。staging/output 必须是 data/publication 下的新目录，report 必须在 data/reports 内。运行预编译渲染器，完成 Git 投影和只读安全门后生成新 HTML；不安装依赖、不转译代码、不部署到公网。
