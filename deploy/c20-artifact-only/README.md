# C20 真 Quartz、合成领域数据的隔离验收

新增根 workflow `c20-artifact-only.yml` 只接受 `expected_source`，限定私有
`sep-zxy/zhizhi-platform` 的 `main`，必须等于该次 `github.sha`。发布本改动后，
应使用包含本改动的**新完整提交 SHA**，不能继续传旧基础 SHA。
本目录不提供自动 dispatch、推送、部署、Windows 同步或联网模型入口。

## 阶段与证据

1. GitHub 托管 runner checkout 精确源码；只读 contents 权限，不保留 Git 凭据。
2. `--phase prepare` 联网准备冻结 Python/npm 依赖、vendored 插件编译产物、既有
   清单中精确哈希字体，并匿名拉取下述精确 Docker 镜像。依赖准备不等于验收成功。
3. `--phase execute` 使用 GitHub 托管 runner 已有 Docker daemon，不安装新 daemon、
   不修改宿主 AppArmor/seccomp/userns 策略。复制精简源码/依赖，不复制 checkout
   `.git` 或用户 home；容器以非 root 身份、`network=none`、只读根文件系统和源码、
   `cap-drop=ALL`、`no-new-privileges`、Docker 默认 seccomp 与 `docker-default`
   AppArmor profile 运行。只有新工作目录、临时目录和两处 Quartz cache 可写；
   不挂载私有 Vault、host home、Docker socket，不注入 Git/model credentials。
   Python/Node 为该 runner 选定版本，仅传入真实 hosted runner 标记而非伪造 CI。
4. 检查实际容器配置与隔离内的网络 namespace、loopback/路由、UID/capabilities/
   安全策略等证据；任何不符即停止，**不回退 bwrap 或普通宿主执行**。
   这不是 `fetch` stub，也不靠环境变量宣称隔离。
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

## Docker 控制面与明确的新边界

本方案使用固定 `ubuntu` Linux amd64 镜像：

- manifest digest：`sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d`
- image ID：`sha256:d9b8c34d61a9b9a3c44cab883d7d2f2619d674966ab520186f799cf32e3e0377`

不采用浮动 tag 替换，不向容器暴露 Docker socket。匿名拉取不要求 Docker 登录或新凭据。
但**宿主 Docker daemon 仍是具备宿主 root 控制能力的控制面**；容器非 root 不代表
daemon 非 root。用户已另行批准此 Docker 方案；不能把此前 bwrap 的权限模型原样套用。
此方案不启用 user namespace remap，容器非 root UID 仍按宿主普通 UID 解释；
它不是 rootless Docker，也不是独立 userns 的 root 映射隔离。mount/network/PID、
只读文件系统、capabilities、NNP、seccomp/AppArmor 和精确允许的挂载共同限制执行。
Docker daemon 或宿主管理员仍能影响容器，不据此宣称可以抵御被控制的宿主。

## 独立日志摘要与 artifact 失败

上传 artifact 后始终运行独立 `summary.py`，只读取固定的 `result/scope.json`、
`result/isolation.json`、`host-evidence/docker-host-inspect.json`、
`result/acceptance/acceptance-receipt.json`。容器自报不能单独通过：还须宿主 inspect
确认精确镜像、配置及容器退出成功（`host_execution_verified=true`）。宿主证据目录不挂进
容器。source/run 由 workflow 的真实 `GITHUB_SHA/GITHUB_RUN_ID` 显式传入，不能由
payload 的 scope 自选；还必须 execution step outcome 为 success，全部证据精确绑定。
它仅向
`GITHUB_STEP_SUMMARY` 和日志输出白名单 source/run 标识、隔离/验收核验结果与上传
step outcome；不输出 JSON 原文、mountinfo、文件名、fixture 正文或任意错误文本。
读取逐级使用 `O_NOFOLLOW` 的目录描述符，最终文件要求 regular、单硬链接及大小上限；
不追踪 symlink、硬链接或 FIFO，不允许 payload 指向宿主文件。

上传前另有两个始终执行的步骤：精确 CID 与 source/run labels 匹配的 cleanup（不使用
prune，不处理其他容器），然后 `export_evidence.py` 以目录描述符和 `O_NOFOLLOW`
导出全新 `evidence-export/`。导出拒绝链接、硬链接和特殊文件，限制 512 MiB / 10,000
文件；只有确认 cleanup 或尚未执行容器时才复制 payload result，否则仅保留宿主证据。
上传器只读取成功导出的 `evidence-export/`，**不直接遍历原 result/host-evidence**；
export 失败时不上传任何不完整目录。摘要成功还要求 cleanup/export step 都成功，
host-only 导出或仅存在文件不是验收成功。
缺件、无效字段、证据不匹配或仅存在文件都标为 `NOT VERIFIED`，并返回非零。
artifact quota/权限/上传失败不会 `continue-on-error`，不会因摘要写成而变成绿色。
摘要不能替代完整 artifact；artifact 上传失败须保留失败状态及该事实，不能宣称证据已保存。

## 验收口径

成功要求 workflow job 成功、精确 source/run provenance、namespace 证据、真实
`site_build_executed=true`、两份字节树相同、泄漏/篡改/撤权门禁全部通过，且
`deploy_executed=false`。CI success 仍不等于真实资料、模型、Windows 或公开部署 E2E。
准备或隔离失败时可能只有 scope.json，**不能把存在 artifact 视为通过**。

## 当前实现验证（云端源文件审查）

本次 Docker 改造基础来自 GitHub `8d530d773a3ccfb95946475399886bb42c6d432a` 精确源码。
本地工作副本是必要文件的部分快照，不是完整 Git checkout，不可从这里推旧历史。
新脚本可运行 stdlib harness/configuration 单元测试及语法校验；这些不执行构建。
本次本地只做 stdlib harness/configuration/summary 测试与语法校验，不运行 Docker、
Quartz 或联网模型，不伪造 CI。此前 bwrap 执行失败不等于 Quartz 通过；本次 Docker
改造是否可运行、真实 Quartz 验收和完整 artifact 是否成功保存，均须以针对新精确 SHA
的 GitHub 实跑证据为准。禁止为获得绿灯移除隔离或吞掉上传失败。
