# 确定性公开投影与离线插件构建

发布输入是指定 Git revision 的公开投影，不是整个私有 vault。`sync-site.ps1` 仅生成全新 staging；`build-public-site.ps1` 仅构建、验证全新输出，不部署、不覆盖现站、不调用模型。

## 插件准备与信任边界

- 原独立清单包含35插件/1190文件，完整保存于 `pruning-v1/original-vendored-source-manifest.json`。当前manifest为31插件/1042文件的删除型派生，保留hash与原件精确一致，唯聚合index仅允许确定性删除四包导出；原锁完整保存。见 `pruning-v1/README.md`。
- 原始清单 SHA256：`e6f15d51db077c9bff09d3dc23c9a26a1ab73a9f8740e572b7dfa789c87a9dc6`；原始 `quartz.lock.json` SHA256：`dc9229d63ba3a6117cca7b718038836390edcf3a8d00b7de7705fa83fe3c8e48`。
- Git 的文本换行规范化只接受经原始字节验证后计算的 CRLF→LF 版本。新增、缺失、替换插件或源码均明确失败。
- 插件编译产物没有独立发行摘要，必须在准备阶段从这些已验证源码与各自锁文件构建。不得声称 dist 具有上游签名。依赖安装与脚本审阅是独立的准备步骤，构建 wrapper 不安装依赖。
- wrapper 始终设置 `QUARTZ_OFFLINE_PLUGINS=1`：不 clone、install、update 或删除插件，不伪造 `.git`；缺依赖/入口/组件明确失败。此开关只限定插件装配，不假称所有模板资源都无网络需求。模板字体缓存等应在独立准备阶段完成。

## 显式公开 origin

必须传 `-PublicBaseUrl https://公开域名`。运行时覆盖，不更改原 YAML 的 localhost 开发设置。只允许无凭据、端口、路径、查询和片段的 HTTPS 公共域名。隔离验收使用 `https://example.org` 作为合成 canonical origin，并不访问或部署该域名。

## 成品门禁

先用无用户知识的合成 index、同版本原站配置及插件，独立构建模板基线；从该可信基线审核 CSS/JS/favicon/static 资源摘要，写入 `TemplateAssetsPath` 的 `assets` 字典。不能从待发布输出生成该白名单。配置或依赖改变后必须重建并审核基线。

`publish.verify --staging ...` 会重验 staging 的 build hash，根据公开页面与标签推导目录、标签、RSS、站点地图、搜索索引和 OG 文件的精确许可路径。扫描所有成品字节中的私有指纹、RAW 标识、凭据/私有路径，校验静态资产摘要及 HTML/CSS 引用。任何失败阻止发布。报告写在 public 目录外。

生成首页与虚拟目录/标签页日期来自公开来源日期，不使用构建时钟；同一 revision、policy、模板与锁定依赖可比较最终 artifact hash。Markdown 投影不调用模型，正文与来源映射不由构建模型重写。

## 调用

在已准备 Python/Node 依赖的 PowerShell 7 环境运行 `scripts/build-public-site.ps1`，必填 StagingPath、OutputPath、PolicyPath、TemplateAssetsPath、ReportPath、PublicBaseUrl。OutputPath 必须不存在，且不能与 staging 重叠。Python 通过 uv，环境必须已同步，构建阶段使用 `--no-sync`。

规范条目额外要求 DomainDatabase、OwnerId、RepositoryPath。`sync-site.ps1` 的 DomainDatabase/OwnerId 及最终验证 CLI 都使用领域 `open_readonly` 入口：只读现有账本，不初始化、迁移或恢复数据库，不触发 Writer。每个正式条目须经 SQLite 中可信审核/证书、当前公开意图、来源摘要和采集 manifest 重验；旧 Git revision 不能绕过当前私有化/删除/撤销。旧授权摘要也不能只靠 frontmatter 自报 eligible。

最终 gate 重建指定 revision 的投影并比较完整 manifest，拒绝投影后变化的策略或资格。公开 manifest 仅携带完整候选/输入/证书摘要，不携带内部 review/source ID、原文引文或私聊身份。成品报告绑定输入快照、公开 manifest、policy 与成品整体 hash。Quartz 子进程只收到净化 staging 与显式公开 origin，环境采用最小白名单，不继承领域路径、模型/渠道令牌或登录配置；数据库参数只交给构建后的可信 Python 验证器。这不是操作系统级沙箱，仍要求锁定且核验过的模板与依赖。

测试中的 SQLite/Git 和合成知识是隔离集成夹具，不代表真实账号、付费模型或生产发布验收。实际浏览器、订阅模型和渠道端到端结果必须分别记录，不能以单元测试替代。

领域资格策略版本由可信部署配置指定：PowerShell 的 DomainPolicyVersion / CLI 的 --domain-policy-version（默认 1）。它不能从待发布条目或旧 manifest 推断；部署升级领域策略时须同步此配置，旧证书会拒绝。当前无持久 active-policy 注册表，不声称自动发现策略升级。
