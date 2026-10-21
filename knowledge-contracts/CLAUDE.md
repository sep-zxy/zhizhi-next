# Claude Code 个人知识库操作手册

你正在维护的是一个以 Obsidian 为前端、以 Markdown 文件为底层、由 Claude Code 协助整理的个人知识库。
本文件是 `AGENTS.md` 的 Claude Code 兼容版本；若两者有差异，以 `AGENTS.md`、`SCHEMA.md`、`glossary.md` 的当前规则为准。

## 启动必读顺序

每次开始处理本 Vault 时，先按顺序读取：

1. `AGENTS.md`
2. `CLAUDE.md`
3. `SCHEMA.md`
4. `glossary.md`

如果需要维护 `wiki/`，必须遵守 `SCHEMA.md`。
如果需要统一术语，必须遵守 `glossary.md`。

## 核心职责

- 维护 `daily/`、`raw/`、`notes/` 到 `wiki/`、`output/` 的知识整理流程
- 对用户原始笔记只做最小编辑
- 对 AI 产物可以主动整理，但必须可追溯
- 不凭空扩写，不删除原始内容，不回滚用户已有改动

## 目录约定

- `daily/`：每日记录，文件名格式 `YYYY-MM-DD.md`
- `raw/`：原始输入、网页摘录、视频总结、资料转写
- `notes/`：用户人工整理的长期笔记
  - `notes/software/`：主动维护的软件库条目
  - `notes/apps/`：软件配套应用笔记，默认命名为 `<software_id>-<主题>.md`
- `wiki/`：结构化知识层
  - `wiki/concepts/`
  - `wiki/entities/`
  - `wiki/topics/`
  - `wiki/index.md`：wiki 总索引，必须使用小写文件名
- `output/`：日报、周报、全库整理、主题汇总等 AI 产物

## 原始资料处理原则

适用于 `daily/`、`raw/`、`notes/`：

- 可以补 frontmatter
- 可以补少量必要 `[[wikilinks]]`
- 可以修正明显术语、标题和大小写问题
- 不重写正文原意
- 不删除原始内容
- 信息量不足时，只在报告中说明，不强行沉淀 wiki

## AI 产物处理原则

适用于 `wiki/`、`output/`、各目录 `index.md`：

- 可以新建、更新、重组
- 可以覆盖旧版 AI 产物
- 必须保持来源可追溯
- 索引链接必须指向真实存在的文件或 wiki 页面
- `output/index.md` 不得引用不存在的日报、周报或报告

## frontmatter 规范

非 wiki 文件尽量使用：

```yaml
---
title: 笔记标题
tags: []
created: YYYY-MM-DD
type: fleeting | literature | permanent
summary: 一句话摘要
---
```

wiki 页面必须遵守 `SCHEMA.md`。
`wiki/index.md` 是导航页例外，不要求使用 concept/entity/topic 类型。

## 公开网站发布规范

准备发布到公开网站的 Markdown 必须优先在源文件阶段写成可同步形态；详细规则以 `AGENTS.md` 的“公开网站发布规范”为准。

必备字段：

```yaml
---
title: 标题
publish: true
section: wiki | software | article | blog | app-note
summary: 一句话摘要
tags: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

固定约定：

- Wiki 像百科条目，一页一个概念、实体或主题，不把多个条目写在一起。
- 软件条目放在 `notes/software/<software_id>.md`。
- 软件配套应用笔记放在 `notes/apps/<software_id>-<主题>.md`。
- 应用笔记必须写 `section: app-note` 和 `related_software: <software_id>`。
- 一个软件可以对应多篇应用笔记；站点生成以应用笔记 frontmatter 的 `related_software` 反向关联为主，`related.app_notes` 只作为可维护辅助字段。
- 不要把多个应用笔记写进软件介绍页正文；软件页只写软件介绍，具体操作沉淀到 `notes/apps/`。
- 软件、博客和文章必须维护 `tags/category/status/pricing/updated` 等已有字段，方便站点筛选、排序和分页。
- 站点列表页采用“筛选前置 + 结果紧随 + 超出 12 条分页”的检索式布局；不要把完整分类清单折叠到页面底部来替代筛选结果。
- 软件、开源项目、协议、价格、平台、下载地址等事实不足时，必须查询可靠来源补齐；无法确认则写“待确认”，不得编造。
- 可靠来源优先使用官网、官方文档、官方仓库、官方应用商店、官方论文/标准/白皮书和维护者公开声明。
- 不把 SEO 聚合站、下载站搬运页、论坛评论或 AI 摘要本身当作事实来源。

内容沉淀边界：

- 默认沉淀目标是 `wiki/`。
- `daily/`、`raw/`、普通 `notes/` 中的长期稳定内容，默认只沉淀到 `wiki/`。
- `blog` 只在用户明确要求时创建或更新；正常补双链、索引、frontmatter 和元数据不算擅自创作博客。
- `software` 和 `app-note` 只在用户明确要求，或源文件已经位于 `notes/software/`、`notes/apps/` 时维护。
- 不从 `daily/` 或 `raw/` 自动生成 `notes/blog/`、`notes/software/` 或 `notes/apps/` 文件；如果材料明显适合这些栏目，只在最终反馈或日报待确认中建议。

## 执行站点同步

当用户输入“同步”“同步到 selfweb”“执行站点同步”或类似表达时，默认同步到 `C:\Code\codex\selfweb\site`。

固定流程：

1. 在 `C:\Code\codex\selfweb\site` 查看 `git status --short`，不回滚已有改动
2. 执行：

```powershell
$ErrorActionPreference = 'Stop'
& 'C:\Code\codex\selfweb\scripts\sync-site.ps1' -VaultPath 'C:\Code\notes\zxy' -SitePath 'C:\Code\codex\selfweb\site' -Build
```

3. 检查 `C:\Code\codex\selfweb\reports\site-sync-report.md`
4. 确认 `是否可以发布：True`，且缺失字段、断链、缺失图片、敏感命中均为 0
5. 如果 Quartz 构建报 `EBUSY ...\public`，只停止明确占用 `C:\Code\codex\selfweb\site\public` 的本地预览进程，例如 `python -m http.server` 子进程链，然后重试同步构建
6. 检查关键新增页面是否出现在 `content/` 和 `public/`
7. 最终说明页面数、软件页数、构建结果、报告路径和是否执行 `git push`

## 发布网站

当用户输入“发布”“发布网站”“部署网站”“把网站发上去”或类似表达时，默认执行 selfweb 完整发布流程，不再重复询问固定配置，除非连接失败或用户明确要求改配置。

固定配置：

- selfweb root: `C:\Code\codex\selfweb`
- deploy script: `C:\Code\codex\selfweb\scripts\deploy-site.ps1`
- public URL: `http://49.233.192.178/`
- OSS bucket: `blog-self`
- OSS endpoint: `oss-cn-beijing.aliyuncs.com`
- server: `root@49.233.192.178`
- SSH key: `C:\Users\Dell\.ssh\id_ed25519`
- remote path: `/var/www/selfweb`

固定命令：

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Code\codex\selfweb'
.\scripts\deploy-site.ps1 `
  -OssBucket 'blog-self' `
  -OssEndpoint 'oss-cn-beijing.aliyuncs.com' `
  -Server 'root@49.233.192.178' `
  -SshKeyPath 'C:\Users\Dell\.ssh\id_ed25519' `
  -RemotePath '/var/www/selfweb'
```

发布规则：

1. 发布脚本会自动同步公开内容、构建 Quartz、上传 `site/public/assets` 到 OSS、上传完整 `site/public` 到 Ubuntu 服务器并 reload Nginx。
2. 只有同步报告 `canPublish=True` 才能继续发布。
3. 服务器保留本地图片，同时上传 OSS；暂不把页面图片 URL 改为 OSS 地址。
4. 不默认使用 `-DeleteOssExtra`，避免误删 Bucket 内其它对象。
5. 服务器 80 端口由 Nginx 服务 `/var/www/selfweb`；`8081` 是原有 `couple/syncday` 服务，不要改动。
6. 发布后至少验证 `http://49.233.192.178/`、`/wiki/`、`/software/` 返回 200。
7. 不在文档、记忆或聊天中记录 OSS AccessKey Secret；凭证只应保存在本机 `C:\Users\Dell\.ossutilconfig`。

当用户输入“启动”且上下文指向 selfweb 时，在 `C:\Code\codex\selfweb\site` 执行：

```powershell
$ErrorActionPreference = 'Stop'
npm run serve -- --port 49153
```

如果端口被占用，换其他高位端口；如果 8080 报 `EACCES`，直接避开 8080。

## 执行今日整理

当用户输入“执行今日整理”时：

1. 读取最新日报，确定增量范围
2. 扫描 `daily/ raw/ notes/` 的新增或最近更新文件
3. 补齐 frontmatter
4. 建立必要双链
5. 将长期价值内容沉淀进 `wiki/`
   - 不从 `daily/` 或 `raw/` 自动创建博客、软件条目或应用笔记
   - 已经位于 `notes/software/`、`notes/apps/` 的文件可以按对应规范维护
6. 更新 `daily/index.md`、`raw/index.md`、`notes/index.md`、`wiki/index.md`、`output/index.md`
7. 生成 `output/daily-digest/YYYY-MM-DD.md`
8. 更新 `output/weekly/YYYY-Www.md`
9. 最终说明处理文件、新建页面、更新索引、待确认问题、暂不沉淀内容

## 执行全库整理

当用户输入“执行全库整理”时：

1. 读取 `AGENTS.md`、`CLAUDE.md`、`SCHEMA.md`、`glossary.md`
2. 查看 `git status --short`，记录已有改动，不回滚
3. 扫描 `daily/ raw/ notes/ wiki/ output/`
4. 补齐缺失 frontmatter
5. 检查所有 wikilinks 和 index 链接
6. 检查 wiki sources 是否存在
7. 谨慎识别重复或同义 wiki 页面，不确定则只列候选
8. 更新全部 index
9. 补齐缺失周报或移除无效引用
10. 生成 `output/full-review-YYYY-MM-DD.md`

## 执行工程沉淀

当用户输入“执行工程沉淀”时，默认处理当前本地日期。
当用户输入“执行工程沉淀：YYYY-MM-DD”时，处理指定日期。

目标是在 `daily/YYYY-MM-DD.md` 的 `## 工程沉淀` 区块中，写入从 Codex 和 Claude Code 对话里提炼的工程实践教训。
只记录“用户意图明确 -> 执行出现偏差 -> 后续被纠正 -> 可复用工程教训”的内容。

执行规则：

1. 如果当天 daily 已存在，只更新 `## 工程沉淀` 下的 marker 区块，不改用户手写内容
2. 如果当天 daily 不存在，用 `templates/daily-template.md` 创建，并替换 `{{date}}`
3. Codex 记录从 `C:/Users/Dell/.codex/session_index.jsonl` 定位，再读取 `C:/Users/Dell/.codex/sessions/YYYY/MM/DD/*.jsonl` 和 `C:/Users/Dell/.codex/archived_sessions/*.jsonl`
4. Claude Code 记录从 `C:/Users/Dell/.claude/history.jsonl` 定位，再读取 `C:/Users/Dell/.claude/projects/**/<sessionId>.jsonl`
5. Claude Code JSONL 的 `timestamp` 是 UTC，筛选当天时必须转成本地时间；不要依赖默认 JSON 日期转换
6. 解析时保留用户消息、助手回复、必要的 tool 调用和 tool 结果；忽略 attachment、system、file-history-snapshot、turn_context 等噪音
7. 每次生成 0-5 条 bullet，尽量一句话一条，写成工程实践教训，不写流水账

写入格式固定为：

```md
## 工程沉淀
<!-- codex-engineering-deposit:start -->
<!-- codex-engineering-deposit:end -->
```

必须排除：

- 顺利完成、直接落地的任务
- 普通进度记录
- 泛泛心得或情绪总结
- 用户私人日记内容
- 没有纠正链路的代码修改细节
- 仅由工具日志推断、缺少对话证据的内容

若没有符合条件的偏差纠正链路，marker 保持为空，最终反馈说明“今日无可沉淀工程偏差”。
“执行今日整理”和“执行全库整理”只负责保留工程沉淀区块，不默认触发工程沉淀。

## 固定质量要求

- 默认中文输出
- 摘要准确优先，不追求华丽
- 不确定时保守处理，并写入“待确认问题”
- 今日整理和全库整理都要说明“暂不沉淀内容及原因”
- 保持 UTF-8 编码和现有换行风格
