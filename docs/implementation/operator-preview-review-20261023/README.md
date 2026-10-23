# C20 / C21 本地操作员只读检查入口

日期：2026-10-23（UTC+08）。这是已批准功能地图的最小安全子集，不是公网部署、实际恢复或全量端到端交付证明。

## 入口与身份边界

模块：`ahadiff.knowledge_platform.operations_preview`。仅供用户本人在明确指定路径上运行，不注册为 Pi / 模型工具，不给模型 service scope，不读取或配置登录凭据。命令输出 JSON 摘要，不输出正文、来源原文、对象路径或底层异常详情。

```sh
uv run --project projects/growth python -m ahadiff.knowledge_platform.operations_preview backup-inspect /explicit/snapshot
uv run --project projects/growth python -m ahadiff.knowledge_platform.operations_preview restore-plan /explicit/snapshot --dry-run
uv run --project projects/growth python -m ahadiff.knowledge_platform.operations_preview publish-verify --manifest /explicit/public-manifest.json --repository /explicit/vault.git --database /explicit/platform.db --owner-id OWNER --policy /explicit/policy.json
```

Windows 只运行 GitHub 产物中的等价模块，不在 Windows 安装开发依赖或编译。以上命令是云端开发调用示例，不代表已进行 Windows 验收。

退出码：0 代表本次检查或预演计划通过；2 表示参数错误或验证失败。成功不代表已恢复、已构建站点、已发布或真实 E2E 通过。

## C20：现有发布清单资格复验

- 输入必须是已有 public manifest、明确 policy、可信领域数据库与对应 bare Git。
- 直接复用 `bind_domain_publication` / `revalidate_public_projection`，在临时目录用既有净化投影器重建同一 revision/policy/template，逐项比较完整 manifest。来源授权撤回、证书失效、策略或投影变动会失败。
- 成功仅返回 revision、build_hash 与 `site_build_executed: false`、`deploy_executed: false`。
- 不接收发布地址、部署 token 或输出站点目录；不启动 Quartz / Node build，不切换网站。`build_hash` 是既有确定性投影标识，不是本站构建成功证明。
- 本模块另提供真实 eligibility、publish-preview 与只读 publish-build-plan，详见后文；实际 build/deploy 命令仍未实现，不使用成功占位。
- 可信领域数据库沿用现有 SQLite `mode=ro` 与 `query_only` 读取，可能使用 SQLite 常规读侧 WAL/SHM；不迁移、恢复或更新领域记录。Git/数据库由本机可信操作员控制，检查期间应保持稳定。

## C21：现有备份检查与恢复计划

直接读取现有 `DomainService.backup()` 的 manifest schema v1，不调用该写操作。

检查范围：

1. manifest 完整标志、版本和重复 JSON key；DB SHA256；所有路径拒绝父目录/对象 symlink、junction、特殊文件及越目录。
2. 原备份若有 WAL、SHM、journal sidecar 一律失败，不用 `immutable=1` 隐藏 WAL，不 checkpoint。将已校验 DB 复制到自动清理的临时检查目录，以 `mode=ro`、`query_only` 执行 integrity_check；避免 SQLite 读操作在原备份旁创建 sidecars。
3. Git HEAD 与清单 revision、每个历史 revision 可达性和正式 blob；拒绝外部 alternate、replace refs、shallow/graft、include 配置及额外 worktree 配置。
4. 所有清单对象的实际字节数与 SHA256，清单/对象文件集合/DB source 引用一致。内容寻址导致重复同内容对象允许，冲突拒绝。
5. history SQL 列与 metadata JSON identity 一致；metadata 加正式正文重建规范 Git 文档，核对完整 blob 哈希和 owner 命名空间。不能用重新计算 DB hash 掩盖历史 metadata 变化。
6. 当前 catalog 完整内容必须等于对应 owner/entry 的最新历史 metadata，SQL revision 同步；不存在待恢复 writer journal。结束再次检查 DB hash 和 HEAD。

通过后输出对象数、历史版本数、DB hash、Git revision。`restore-plan --dry-run` 仅列：新隔离目标→复制后复验→重建可丢弃索引→应用验收→明确切换批准。没有目标写入参数，不复制完整快照、不初始化服务、不替换库、不切换线上。

## 明确不保证的事项

- 检查不是备份真实性签名、攻击性并发变更下的安全沙箱、完整应用恢复演练或所有 Git 无关对象的 fsck。
- 读取期间备份必须保持稳定；不允许用此输出代替隔离恢复后的真实查询/流程测试。
- 仅校验本模块列出的 authoritative 一致性约束，不证明所有领域业务记录语义正确。
- `recovery_proven` 始终 false。没有模型、外网渠道、公网站点和 Windows 测试被本轮声称通过。

## 合成测试证据

新增 `tests/knowledge_platform/domain/test_operations_preview.py`，真实隔离 SQLite/Git fixture、通过领域 backup 生成测试快照。覆盖原文件零改写、缺失组件、DB/对象破坏、sidecars、路径逃逸、链接、Git 外部配置、历史对象缺失、清单重复 key、发布清单篡改、当前来源授权撤回及重算 DB hash 后 current/history 篡改。

这些是云端合成集成测试，不是用户真实资料或最终 E2E。

本轮执行记录（2026-10-23 03:29 UTC+08）：

- 新增 operator 检查文件：38 passed。
- domain 全目录 + 既有 publish / publish_artifacts：667 passed，30.55 秒。
- 执行使用 uv、隔离已有测试解释器与 PYTHONPATH；没有安装或修改项目依赖，没有调用模型、Windows、部署或实际恢复。
- `git diff --check` 通过。没有把这些合成结果当成最终端到端全部通过。

## C20 后续已落地：当前资格、固定版本预览、构建计划

现新增真正可执行的 `eligibility`、`publish-preview` 和只读 `publish-build-plan`；仍未创建实际 build/deploy 命令。

```sh
uv run --project projects/growth python -m ahadiff.knowledge_platform.operations_preview eligibility ENTRY_ID --repository /library/vault.git --database /library/platform.db --owner-id OWNER
uv run --project projects/growth python -m ahadiff.knowledge_platform.operations_preview publish-preview --repository /library/vault.git --database /library/platform.db --owner-id OWNER --revision EXACT_COMMIT_SHA --policy /policy/public.json --staging /isolated-output/new-staging
uv run --project projects/growth python -m ahadiff.knowledge_platform.operations_preview publish-build-plan --repository /library/vault.git --database /library/platform.db --owner-id OWNER --policy /policy/public.json --staging /isolated-output/new-staging
```

- eligibility 读取当前 owned entry，经原 get_entry 和 verify_publication_entry 核验，仅返回布尔、受控原因码和版本号；不会输出标题、正文、来源或异常细节。
- publish-preview 固定完整 Git SHA；输出父目录必须已存在，目标必须不存在，拒绝既有空目录、链接以及与库目录/源仓库/policy 重叠。先在新临时目录调用原投影器，再现时复验完整 manifest 和原 Quartz staging 哈希校验，最后 copytree 到全新目标，绝不合并覆盖。内部排除清单不落入公开 staging。
- 老 SHA 不等于旧授权：当前来源权利撤回后 eligibility 拒绝，旧版本预览排除对应内容，旧 staging 的 build-plan 复验失败。
- publish-build-plan 调用现有复验与 quartz_generated_files，输出计划而非构建结果。现有 Quartz 引擎是 projects/selfweb/site/quartz/bootstrap-cli.mjs；GitHub runner 在锁定依赖及模板后调用该入口的 build 命令，显式 directory 指向仅净化 staging、output 指向全新目录。禁止直接 npm run build，因为它先执行 sync-site，可能重新混入非本次 staging 内容。
- GitHub 实际构建后必须运行既有 ahadiff.knowledge_platform.publish.verify，绑定原 manifest、当前资格、policy 和可信 template assets 校验输出。构建 runner 只接收净化 staging，不能挂载私有库；可信资格复验在隔离读侧完成。部署依然单独授权，本命令不启动 runner、不调用 Node、不传凭据。
- 最终复制先独占创建新目录并记录 inode/device；IO 错误时仅清理本次创建且身份未变化的目录，可重新预览。抢先存在或被替换的目录绝不删除；若清理本身失败，报 blocked 且不宣称无文件变化。检查与产物均不是永久发布许可，构建/部署前必须再次复验当前授权。

新增 publication operator 合成测试 10 项通过，涵盖确定性、路径隔离、来源撤回与旧 staging 阻断、固定 SHA、篡改 staging、实际 CLI 命令输出；未运行 Quartz 构建或发布。

C20 新编排加入后的复验（2026-10-23 03:41 UTC+08）：operator 两个测试文件 48 passed；domain 全目录 + publish / publish_artifacts 677 passed（30.06 秒）。以上依然只是合成集成回归，不是最终 E2E。

## 独立审查阻塞修复：领域白名单与失败清理

通用 legacy Markdown exporter 允许 `publish: true` 页面，这不适合本人领域 operator。现在新增领域专用读取适配层：复用受限 ReadOnlyGitWriter（清理 GIT_DIR 等继承环境、有界 Git 子进程），要求每个 Markdown 为 schema v6 canonical entry，按本人 namespace 筛选，再交原 build_projection 净化与可信 resolver 现时资格复验。普通 legacy Markdown 无论正确或错误 owner 都拒绝；不会将通用 exporter 的兼容性当作领域授权。

publish-preview、publish-verify、publish-build-plan 全部使用同一严格复验，旧 legacy staging 不能通过 build-plan 或 verify。通用 publish 模块本身不被修改。

新增反例涵盖真实 Git 插入有日期/title/publish 标志的无所属条目、错误 owner、旧 legacy manifest、GIT_DIR 重定向，以及真实 copy_function IO 失败、输出目录被外部替换不能误删。新 publication 文件 16 项通过。

审查修复后复验（2026-10-23 03:45 UTC+08）：domain 全目录 + publish / publish_artifacts 683 passed，33.44 秒。其中 operator 共 54 项。均为隔离合成集成测试，不是最终真实端到端交付。
