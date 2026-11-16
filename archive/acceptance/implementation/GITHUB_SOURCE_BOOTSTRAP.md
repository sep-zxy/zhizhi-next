# GitHub 完整源码引导与验收边界

2026-10-20 最新授权：开发在隔离云端，最终打包在 GitHub，Windows 仅通过 Git 获取、校验和运行成品。此前云端便携包只作为历史基线，不能标记为新 GitHub 构建结果。

## 一次性源码导入

`deploy/git-sync/source_bootstrap.py prepare` 从明确的完整 40 位 Git commit 读取源文件，不读取工作树未提交内容，也不包含 Git 历史。沿用精确 fixture/二进制资产审查；不明二进制、私有路径、凭据模式和链接失败即停止。仅明确排除非产品 `design-output/`，在清单中逐路径记录，字体、emoji、测试 fixture、锁文件和许可证不删减。

文件按 SHA-256、Git blob、mode 和整体 Git tree 双重校验，压缩后分为最多 96,000 字节的小块。每个 Git Data API 请求含 base64/JSON 后不超过 131,072 字节。`plan.json`、`expected-tree.json` 和每个请求的预期 blob SHA 为连接器上传及复核依据。

准备示例（Python 经 uv）：

```
uv run --no-project python deploy/git-sync/source_bootstrap.py prepare --repo . --ref <完整源码commit> --output <全新目录>
```

只把输出 `checkout/` 的明确文件集发布到同一私有仓库的源码传输分支；不上传本地 `.git`、原 bundle、未提交草稿或开发缓存。上传和移动任何远端引用均由唯一协调者进行，并先核验完整 tree。

## 控制器与最小权限

1. 通过已批准的 GitHub 连接器在 `main` 预置源快照中的全部 `.github/workflows/` 文件，以及 `deploy/git-sync/source_bootstrap.py`。必须与待恢复源码中的 workflow 字节完全相同。
2. 记录该 seed 提交 SHA、传输分支固定 commit、`transport.json` 的外部预期 SHA-256。
3. 手动运行 `Audited source bootstrap`，填入上述三个值。只在本仓库默认 `main` 运行；单一 Ubuntu runner、15 分钟上限、串行写入，不扩展权限或创建长期凭据。
4. Runner 在新目录恢复并核验原文件、权限 mode 和完整 tree。任何校验失败不创建发布提交。恢复前后的 workflow blob 必须与 seed 提交完全相同。
5. 用仅 job 存活期的 `GITHUB_TOKEN` 和 `contents: write` 创建以 seed 为唯一 parent 的提交；提交前复查远端 main。推送使用明确 SHA 的 lease 作为原子 CAS，并独立校验新提交必须是该 SHA 的直接子提交。因此不允许历史重写，任何并发移动（包括回退到祖先）均失败，不触碰交付分支。
6. 读取远端确认新 SHA。结果不确定时停止并检查远端，不盲目重试。父提交自然保留源码回退点。

`workflow_dispatch` 入口必须已经存在于默认分支。不得尝试给 GITHUB_TOKEN 添加并不存在的 workflow 写权限；workflow 文件变更仍由已经授权的连接器处理。GitHub 文档说明 GITHUB_TOKEN 发出的普通 push 不递归触发工作流，因此完整源码恢复后必须另行手动启动构建，而不是等待一次不会发生的 CI。

参考：[手动触发与递归触发限制](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)、[GITHUB_TOKEN 权限](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)、[GitHub 关于 workflow 写权限的说明与复现](https://github.com/orgs/community/discussions/27224)。

## 验收记录

- 16 项离线测试覆盖精确树、CRLF/可执行 mode/空文件、请求大小、篡改、路径和链接、重复 JSON、大小界限、工作树隔离、私有文件拒绝、workflow 差异和 parent/CAS 门槛。
- `cf220876b47a70e0919095bb65392b9a74a87139` 基线真实文件回环：2,664 文件，65,033,902 字节；压缩 22,262,800 字节；234 个传输文件；最大请求 128,044 字节。恢复 tree 与原 tree 同为 `8648a02fd40f9deb18bf77f4bb8df0b1c31dd2e4`。
- 上述是本地静态审计与字节回环，不是 GitHub 执行成功、Windows 运行、真实模型或全平台 E2E 成功。后续提交必须重新产生对应清单与校验值。
- 源码导入 receipt 明确 `build_status: not_run`、`windows_e2e: not_run`。CI 通过和成品分支推进均不自动意味着最终用户验收通过。
