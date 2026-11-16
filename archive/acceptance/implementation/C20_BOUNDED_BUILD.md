# C20 固定 manifest 构建入口（不部署）

对应已批准 CLI 功能地图 C20 / 骨架 F16。`operations_preview` 现提供
`publish-build`，原 `publish-build-plan` 仍只读，C21 inspect / restore-plan 不变。

仅在项目既有规则要求的 **GitHub 托管 runner** 中显式调用：

```text
uv run --no-sync python -m ahadiff.knowledge_platform.operations_preview publish-build
  --manifest <approved-publish-manifest.json> --staging <sanitized-directory>
  --output <new-isolated-output> --template-assets <trusted-template-assets.json>
  --public-base-url https://example.com --repository <canonical-bare-git>
  --database <trusted-domain-ledger> --owner-id <owner> --policy <trusted-policy.json>
```

以上为参数说明，不是本轮真实构建命令执行记录。此入口不自动下载、安装或编译依赖，
不修改 CI / lockfile；所选源码版本的 Quartz、锁定依赖、插件离线编译产物、已核验字体
及独立可信模板资产清单须由获准 GitHub 流程预备。不得从待验证输出反推资产白名单。

## 边界

- manifest 必须与净化 staging 完整一致；记录选定 manifest 与模板资产清单 SHA-256。
- 复用真实领域资格复验与 staging hash 校验；复制到临时净化目录后再检查。
- 固定 argv 直接调用已选源码中的 Quartz bootstrap（无 shell、npm sync、模型）。
  Quartz 可写 site 下编译缓存，不是仅写新 output；构建前检查两处缓存树的
  链接/特殊文件/文件硬链接及受保护输入重叠。回执注明缓存可能改变。
- 子进程只收到基础运行环境与离线 Quartz 配置，不传 Git/数据库参数或凭证变量，
  输出不回显原始进程 stdout/stderr。超时或非零退出阻断。
- 在构建后、复制到新目标后均再次复验当前资格；扫描最终 HTML、检索、引用与资产，
  新目标排除源仓库、数据库目录、staging、模板等路径重叠及链接。失败不替换旧站。
- 成功回执包含产物 hash 和 `deploy_executed=false`，部署前仍须重新核验并单独授权。
- `GITHUB_ACTIONS` / `RUNNER_ENVIRONMENT` 仅是防误执行的环境检查，不是可信证明。
  无网络、无私有 Vault 挂载、无凭证和 OS 隔离必须由实际执行器提供；本入口无法
  用环境变量证明这一点，因此回执固定 `isolation_attested=false`。输入需保持稳定；
  进程级路径校验不能防御具有并发修改目录权限的恶意同机进程。

## 本轮证据与未验项

测试使用合成 Git/SQLite、假子进程和合成静态文件；不设置假 CI 变量，不运行真实
Quartz，不算构建或 E2E 成功。覆盖资格撤回、篡改、泄漏、链接、目标保护、超时及 CLI
脱敏。下一阶段仍需 GitHub 上实际锁定 Quartz 双构建 hash/完整泄漏扫描及隔离证据，
随后通过 Git 将对应版本送至 Windows 验证。C21 没有恢复操作，也没有恢复已证明结论。

当前实现验证：20 项新增 fake-process 测试及 137 项既有发布/资格/预览回归，共
157 passed。独立只读审查指出的缓存写入链接与嵌套同名 manifest 哈希遗漏已补门禁；
这些测试不证明真实 Quartz 产物、真实托管执行器隔离、Windows 或最终交付已通过。
