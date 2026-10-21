# 私有 Git 源码与 Windows 成品同步

## 目标与边界

2026-10-20 最新授权：源码编写在获准隔离云端，最终打包与编译在 GitHub 托管
Actions 完成。Windows 只通过 Git 取成品、校验并运行。早期云端包是历史基线，
不能改名为新 GitHub 构建；新链路见 `GITHUB_SOURCE_BOOTSTRAP.md` 与
`GITHUB_WINDOWS_DELIVERY.md`。
不再以聊天附件作为日常同步通道；约 205 MB 的既有运行时不逐版重复提交。
本说明和本地准备程序不授权创建远端、外传数据、配置持久凭据或执行真实模型。

推荐同一私有仓库两个分支：

- `main`：完整审计源码、必要源资产、依赖锁文件、测试与许可证。
- `windows-delivery`：历史补丁保留；新候选放在 `releases/<source>-<run>-<attempt>/`，
  包含 GitHub 生成的可运行文件、来源/哈希与只读校验器。`current.json` 只在门禁
  通过后随一次 fast-forward 推进，历史版本保留。候选通过完整性校验不等于最终交付。

迁移采用新干净历史，不推送原快照历史或全量 bundle。原历史只保留云端恢复备份。
新的源代码提交继续小步开发；交付分支只在成品验证后推进，并固定 Git commit 运行。
许可证、版权、字体许可与第三方清单不得为减小体积而删除。

## 已完成本地审计

恢复基线为 `a04361e`，完整 bundle 验证和 Git fsck 通过；
文档增量 `af9cc6c` 已合并。原导入阶段已排除私人知识、RAW、Wiki content、
身份文件、运行数据库、缓存与依赖目录，并保留排除记录。
本轮对恢复 HEAD 和全部 2,880 个历史 blob 做了高置信凭据模式检查；
命中均为已核查的确定性测试 fixture，不是账号凭据。启发式检查不能证明
绝对无秘密，因此不凭此自动允许上传任意历史或新增数据。

`deploy/git-sync/reviewed-fixtures.json` 只豁免已复核测试的精确文件 SHA-256
和精确命中类别。文件字节改变、路径改变或新增命中一律失败，须重新审查；
不能整目录白名单，也不能为过扫描器删除测试。

源树预备从已提交的 Git blob 读取，不复制目录中的未跟踪文件。
禁止私有路径、身份文件、运行数据、归档和不明二进制；原项目必要字体与图标保留。
两个源文件 `deploy/windows-portable/runtime/{launcher,windows_job}.py`
是明确列出的启动器源码例外，不是运行时发行包。

## 本地生成

通过 `uv run --no-project python` 调用
`deploy/git-sync/prepare_git_delivery.py source --repo <源码仓库> --output <新目录>`。
工作树必须干净。输出清单、Git blob 预期 SHA、批量 tree JSON 及大文件 blob JSON。

`release` 子命令另接收 `--patch` 和 `--smoke`。只接受已核实的两个归档
SHA-256；解包路径、重复项、软链接、补丁 before/after 和 smoke manifest 均检查。
输出 `checkout/` 可直接运行 `verify_release.py` 做只读字节验证。
打包工具不访问网络，不创建仓库、不提交远端、不读取凭据。

## GitHub 连接器导入约束

只有用户批准确切 GitHub 账号、仓库和 private 可见性且目的地经验证后才导入。
现有云端 gh 未登录；不从 Windows 复制 token，不新建凭据，不提升权限。
连接器可用 Git Data API，先创建大/二进制 blob，校验响应 SHA，
再按 tree 批次顺序生成树。首批不继承旧 tree；后续批次使用前一响应的 tree。
最终通过读取递归 tree 与本地 expected-tree 逐路径、mode、blob SHA 对比后，
才创建提交并移动目标分支。操作不确定时先查状态，不能盲目重试。

当前 create_commit 接口需要 parent SHA；空远端应由正常仓库初始化产生一个
干净 README 提交，再用其作为 parent，替换整树。不能伪造 parent 或引入旧私有历史。
每个分支独立核验。只有 Git fetch 后再核对同一 commit 和校验清单，
才能报告 Git 交付完成。没有远端批准时，只保留本地准备成果。

## 验收的真实含义

本轮包装测试不调用真实模型，不证明 Windows 原生运行或正式渠道 E2E。
既有补丁 rollback/apply、云端 fixture、Windows smoke 与全平台 E2E 分别记录；
blocked、skipped、fixture 不等于真实成功。
