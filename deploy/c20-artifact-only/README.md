# C20 真 Quartz、合成领域数据的隔离验收

新增根 workflow `c20-artifact-only.yml` 只接受 `expected_source`，限定私有
`sep-zxy/zhizhi-platform` 的 `main`，必须等于该次 `github.sha`。发布本改动后，
应使用包含本改动的**新完整提交 SHA**，不能继续传旧基础 SHA。
本目录不提供自动 dispatch、推送、部署、Windows 同步或联网模型入口。

## 阶段与证据

1. GitHub 托管 runner checkout 精确源码；只读 contents 权限，不保留 Git 凭据。
2. 联网准备冻结 Python/npm 依赖、vendored 插件编译产物以及既有清单中精确哈希字体。
3. 复制精简源码/依赖，不复制 checkout `.git` 或用户 home；bubblewrap 建立独立
   user/mount/pid/network 等命名空间、清空环境。只有新工作目录和两处 Quartz cache
   可写；不挂载私有 Vault、host home、Docker socket。Python/Node 为该 runner 选定版本。
4. 子命名空间实查网络 namespace 与 host 不同、仅 loopback、无外部路由；失败即停止，
   **不回退普通宿主执行**。这不是 `fetch` stub，也不靠伪造 CI 变量建立隔离。
5. 在隔离内，从两个独立合成模板构建稳定资产白名单；之后真实 DomainService 创建
   canonical Git 与 SQLite fixture，真实 publish-preview/publish-build 双构建。
   对全部产物文件及已有 artifact hash 比较；在真实输出副本注入泄漏 fingerprint，
   篡改 staging、撤销现时来源 rights，检查失败门禁。没有 mock Quartz 或 guard。
   A/B 以及恢复正对照构建前，两处缓存都重置到同一已核验准备快照（保留字体），
   不继承模板或前次构建新增缓存；只删除隔离缓存挂载点的子项，链接/硬链接先拒绝。
   该结论是同 runner、同锁定依赖/准备快照的重复性，不宣称跨平台或跨依赖环境复现。
   负向门禁严格匹配异常类型与预期错误码/文本，普通构建错误不能当作通过；
   泄漏扫描先验证未改副本，篡改和撤权恢复后另作真实构建正对照。

artifact 名为 `c20-real-quartz-synthetic-evidence-<SHA>`，仅私有 Actions artifact，保留
7 天。内容包括 scope、namespace/mount/env-name 证据、合成 SQLite/Git/staging、
模板构建结果、A/B 成品和 acceptance receipt。所有 fixture 均此 run 新建，**禁止将此
入口改成接收用户 Vault/数据库/凭证**；artifact 中的合成资料不是用户公开授权。
产品回执 `isolation_attested=false` 保持不变，执行器证据是另外的文件。

## 验收口径

成功要求 workflow job 成功、精确 source/run provenance、namespace 证据、真实
`site_build_executed=true`、两份字节树相同、泄漏/篡改/撤权门禁全部通过，且
`deploy_executed=false`。CI success 仍不等于真实资料、模型、Windows 或公开部署 E2E。
准备或隔离失败时可能只有 scope.json，**不能把存在 artifact 视为通过**。

## 当前实现验证（云端源文件审查）

本次基础来自 GitHub `4be0ab83b4a24a36c5d68c05cf36b2e952774675` 精确 tree。
本地工作副本是必要文件的部分快照，不是完整 Git checkout，不可从这里推旧历史。
新脚本可运行 stdlib harness/configuration 单元测试及语法校验；这些不执行构建。
当前环境没有 markdown-it-py，DomainService 导入阻断，因此本地未跑领域集成。
未在本地构建、未伪造 CI、未运行 GitHub workflow，真实 bwrap/Quartz 仍待 GitHub 验证。
Ubuntu runner 的 userns/AppArmor 若不支持此 sandbox，流程会失败关闭，需要审核失败
日志后修正隔离执行器；禁止为获得绿灯移除隔离。
