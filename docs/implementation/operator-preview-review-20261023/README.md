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
- 没有新造 per-entry eligibility 或 preview/build 命令占位。原发布模块仍是独立入口；完整 C20 shell family 并未宣称完成。
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
