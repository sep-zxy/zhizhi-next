# Codex 个人知识库操作手册

本 Vault 以 Obsidian 为前端、Markdown 为底层。Codex 的职责是维护一个准确、低打扰、可追溯的个人知识库，不替用户重写原始笔记。

## 1. 启动顺序

每次处理本 Vault，按顺序读取：

1. `AGENTS.md`：命令路由、执行流程和权限边界
2. `SCHEMA.md`：文件结构、字段、链接、来源和校验规则
3. `glossary.md`：固定术语、标签和命名偏好

三份文件发生冲突时：数据保护和公开权限规则优先，其次是 `SCHEMA.md` 的结构约束，最后才是临时推断。

## 2. 数据边界

### 用户源数据

- `daily/`：每日记录，文件名为 `YYYY-MM-DD.md`
- `raw/`：原始输入和待编译材料
- `notes/`：用户人工维护的笔记
  - `notes/software/`：软件库条目
  - `notes/apps/`：软件配套应用笔记
  - `notes/blog/`：用户明确创建或要求整理的博客内容

对源数据只允许：

- 补齐或修复必要 frontmatter
- 增加有意义的 `[[wikilinks]]`
- 修正明显的标题、大小写和术语问题
- 保留正文原意、表达风格和非空用户内容

不得删除、重写或擅自扩写用户原始内容。

### Codex 产物

- `wiki/`：结构化知识层
- `output/`：日报、周报、连续提醒和整理报告
- 各目录的 `index.md`

Codex 可以新建、更新和重组这些文件，但必须保证来源可追溯、链接可解析、内容不凭空生成。

## 3. 通用执行契约

### 3.1 修改前预检

任何会修改 Vault 的命令都必须：

1. 执行 `git status --short`
2. 将已有改动视为用户或前序任务的现状
3. 不回滚、不覆盖、不删除无关改动
4. 读取本次会涉及的现有文件后再修改
5. 修改 Schema 字段、顺序、默认值或枚举时，同步更新 `templates/` 下的对应模板

如果发现已删除源文件仍被索引或 Wiki `sources` 引用，修正引用并在报告中说明，不擅自恢复源文件。

### 3.2 内容处理原则

- 只处理 Markdown；PDF、截图、网页和聊天记录应先转为 Markdown
- 目录层级不超过三层，分类优先使用 `tags` 和 `[[wikilinks]]`
- 已有页面优先增量更新，不重复创建同义页
- 无法确定语义时保留原文，并进入“待确认问题”
- 可安全机械修复的问题直接修复
- 修改必须幂等：同一天重复执行且输入没有变化时，不产生无意义 diff

### 3.3 事实与来源

版本、价格、协议、平台、下载地址、政策、官方能力等易变事实必须先查证。

来源优先级：

1. 官网、官方文档、官方博客和官方下载页
2. GitHub、GitLab 等官方仓库及 Release、License、README
3. 官方应用商店
4. 官方论文、标准和白皮书
5. 维护者公开账号中的发布声明
6. 权威数据库和媒体，仅用于辅助校验

SEO 聚合站、搬运下载站、无署名教程、论坛评论和 AI 摘要不能作为关键事实来源。

### 3.4 公开权限

- 新增 raw 和 Wiki 默认 `publish: true`；raw 允许单篇改为 `false`，缺少可确认的 `source` 时写入 Schema 安全默认值，不阻止公开
- raw 和 Wiki 之外的新增内容默认 `publish: false`
- 文件位于 `notes/apps/` 或 `notes/software/` 不代表自动公开
- Wiki 按用户长期授权默认公开；其他类型只有用户明确授权时，才可把 `publish` 从缺失或 `false` 改为 `true`
- 已公开文件默认保留原值，除非用户要求撤回或发现误发布
- `output/continuity.md` 固定为 `publish: false`
- `updated` 在正文、摘要、标签、分类、状态、价格、公开状态等有效内容或属性变化时更新
- 字段排序、索引、纯格式、拼写、双链和 raw 月份移动不更新 `updated`

公开文件的字段与发布校验统一遵守 `SCHEMA.md`。

### 3.5 内容沉淀边界

- `daily/`、`raw/` 和普通 `notes/` 的长期内容默认只沉淀到 `wiki/`
- 不从 `daily/` 或 `raw/` 自动创建博客、软件条目或应用笔记
- `blog` 只在用户明确要求或源文件已位于 `notes/blog/` 时维护
- `software` 和 `app-note` 只在用户明确要求或源文件已位于对应目录时维护
- 明显适合其他栏目的材料，只在日报或最终反馈中建议

## 4. 命令路由

| 用户表达 | 执行流程 |
|---|---|
| `每日整理`、`执行每日整理`、`执行今日整理` | 第 5 节 |
| `执行全库整理` | 第 6 节 |
| `执行工程沉淀`、`执行工程沉淀：YYYY-MM-DD` | 第 7 节 |
| `生成主题汇总：<主题>` | 第 8 节 |
| `同步`、`同步到 selfweb`、`执行站点同步` | 第 9 节 |
| `发布`、`发布网站`、`部署网站`、`把网站发上去` | 第 10 节 |
| selfweb 上下文中的 `启动` | 第 11 节 |

命令可以使用近义表达，但不得因为模糊上下文自动触发发布、部署或工程沉淀。

## 5. 每日整理

目标：处理本次新增或变化的源材料，更新结构化知识、索引、日报、周报，并通过 Continuity CLI 更新 SQLite 中的连续项目和今日行动，最后导出只读快照。

### 阶段 A：确定范围

1. 完成通用修改前预检
2. 读取 `output/daily-digest/` 中最新日报及其修改时间
3. 合并以下候选：
   - Git 工作区中 `daily/`、`raw/`、`notes/` 的新增或修改文件
   - 上次日报之后更新的源文件
   - 今天的 `daily/YYYY-MM-DD.md`
   - 用户明确指定的文件
4. 无法准确判断时，增加最近三天修改的 `raw/` 和 `notes/` 作为兜底
5. 排除 `index.md`、`wiki/`、`output/` 和模板，避免把 AI 产物当作新增源材料

### 阶段 B：规范化源文件

1. 按目录和现有 frontmatter 识别文件类型
2. 按 `SCHEMA.md` 的固定字段顺序、类型、枚举和白名单规范 frontmatter
3. 按标签注册表执行别名映射、去重、排序和精简；普通内容只保留 1–4 个语义标签
4. 保留正文原意和非空信息；确定冗余属性按 Schema 清理
5. 统一 `glossary.md` 中已有术语
6. 只有有效内容或属性变化才更新 `updated`；纯维护动作不得更新时间

对本次范围内的 raw 额外执行：

1. 固定为 `title/source/author/published/created/updated/tags/type/summary/publish/section` 十一个字段
2. 新 raw 默认公开；缺少可确认的 `source` 时写入 Schema 安全默认值，允许先公开并保留后续替换空间
3. 清除网页导航、登录提示、分享控件、推荐阅读、重复标题和确定无效图片
4. 完全重复图片只保留一份；403、超时等不确定远程错误只报告，不删除
5. 以 `created` 归档到 `raw/YYYY-MM/`，`raw/index.md` 不移动
6. 移动后同步修正索引、`sources` 和显式相对路径；冲突不得覆盖

当范围内包含 `notes/apps/`：

1. 优先读取 `related_software`，缺失时从 `<software_id>-<主题>.md` 推断
2. `software_id` 必须小写、稳定、URL 友好
3. 对应软件页存在时，更新 `related.app_notes` 并去重
4. 软件页不存在时不自动创建，在日报中列为待确认
5. 应用笔记可以链接回对应软件页

### 阶段 C：建立知识连接

1. 识别概念、工具、软件、项目、模型、插件、工作流和主题
2. 查找现有 Wiki 页面和同义条目
3. 已有页面：首次出现时增加有意义的 `[[wikilink]]`
4. 中心对象是稳定命名的工具、软件、开源项目或完整工作流：优先创建或更新实体页
5. 反复出现或明显具有长期价值的方法、术语：创建或更新概念页
6. 需要多个概念和实体共同解释的问题域：创建或更新主题页
7. 信息不足时创建 `status: stub`，或暂不沉淀并说明原因

人物默认不进入自动沉淀流程：

- 作者、维护者、帖子发布者和社区用户名只保留为 `author`、普通文本或来源说明
- 材料同时出现人物和作品、项目、工具时，优先沉淀作品、项目或工具
- 即使人物在三份以上独立来源中出现，也只可列为待确认候选
- 只有用户明确要求，或人物本身是经用户确认的长期研究对象时，才创建人物页
- 已存在且合格的人物页可以正常链接和更新

### 阶段 D：编译 Wiki

将具备长期价值的内容沉淀到：

- `wiki/concepts/`
- `wiki/entities/`
- `wiki/topics/`

要求：

- 从源材料抽取，不空想
- 先查重，再创建
- 所有页面写明 `sources`
- 工具、软件、项目和工作流实体优先补官方来源
- 页面结构、命名、标签、状态和发布字段遵守 `SCHEMA.md`

### 阶段 E：形成一次性运行结果

在写报告前，先汇总本轮事实：

- 处理和修改的源文件
- 新建或更新的 Wiki 页面
- 新增的关键双链
- 待确认问题
- 可继续方向
- 暂不沉淀内容及原因
- Continuity SQLite 项目、事项、候选和工程证据
- 本轮可直接执行的今日动作及其来源

日报、周报、SQLite 写入和只读快照都从这份结果生成，避免不同产物相互覆盖。

### 阶段 F：更新 Continuity SQLite

1. 使用 `C:\Code\rust\continuity-widget\bin\continuity-cli.exe database status` 读取当前 revision、项目数、事项数和候选数；`output/continuity.md` 只是只读快照，不作为写入源
2. 根据数据库中的项目 UUID、`legacy_id`、事项 fingerprint、`project_path` 和来源引用定位已有数据
3. 只读取与项目明确关联的主会话、工程状态、测试、构建和项目文档；排除 subagents、无项目归属会话和顺带提及
4. 新项目或无法可靠匹配的旧项目通过 `project upsert` 创建为 `candidate`，不得直接进入主组件
5. 项目摘要和多条事项优先使用 `project summarize` 一次写入；单条增量使用 `item upsert`；所有自动输入必须带稳定 `batch.id`、`kind: automatic` 和来源
6. 事项必须明确写入 `kind=question|action`、`horizon=daily|weekly|later`、稳定 fingerprint、优先级和结构化 evidence，不再通过 Markdown 标题推断
7. 项目进度只由已登记里程碑权重派生；没有里程碑时为 `unplanned`，不得用活跃度或 open/done 事项比例伪造百分比
8. 本轮直接可执行的今日动作写为 `horizon=daily` 的 action；不把待确认问题、项目大方向或模糊建议伪装成今日行动
9. 自动整理不得完成、归档或删除项目，不得把 open 事项改为 done；人工锁定字段发生冲突时由 CLI 写入 suggestion
10. 只有完成已登记工程事项且目标可由 `itemId` 或 `projectId + fingerprint` 精确定位时，才可通过 `$continuity-update-progress` / `event apply` 提交幂等完成事件；失败、取消、模糊匹配和 question 只能追加证据或进入 suggestion
11. 同一项目上轮自动 daily、本轮未继续出现的事项由 `project summarize` 主动降为 weekly；人工 daily、pinned 和 manual_override 不参与自动降级
12. 候选确认、项目完成/归档、成熟度 `mature` 和人工冲突决策只在用户明确要求时执行；批次异常时可使用 `batch undo-last`

### 阶段 G：写入产物

1. 按影响更新索引：
   - 源目录变化才更新对应 `index.md`
   - Wiki 变化才更新 `wiki/index.md`
   - 报告变化后更新 `output/index.md`
2. 生成或更新 `output/daily-digest/YYYY-MM-DD.md`
3. 按本周全部日报增量更新 `output/weekly/YYYY-Www.md`
4. 完成所有 SQLite upsert 后执行 `continuity-cli.exe snapshot export`，由数据库稳定导出 `output/continuity.md` 只读快照
5. 快照相同 revision 时不得重写；同一天重复执行且输入没有变化时，不产生新的业务 revision 或无意义 diff

日报固定包含：

1. 今日新增内容
2. 今日沉淀出的知识点
3. 新建或更新的 Wiki 页面
4. 新增的关键双链
5. 待确认问题
6. 明天可继续整理的方向
7. 暂不沉淀的内容及原因

周报至少包含：

- 本周主要主题
- 反复出现的话题
- 值得长期保留的洞察
- 本周新增的重要页面
- 后续可以继续推进的方向

### 阶段 H：校验与反馈

按 `SCHEMA.md` 执行：

- frontmatter 和枚举校验
- Wiki 路径、结构、状态、来源和查重校验
- Wikilink、索引、图片和本地来源校验
- app-note 与 software 关联校验
- 日报、周报和 continuity 只读快照结构校验
- Continuity CLI JSON 响应、revision、候选项目、事项 fingerprint、人工覆盖和快照 revision 校验
- 公开权限和占位符校验
- 幂等性检查

优先调用：

```powershell
$ErrorActionPreference = 'Stop'
& 'C:\Code\rust\vault-manager\bin\vault-manager-cli.exe' validate --vault 'C:\Code\notes\zxy' --scope changed --json
```

校验器不可用时才人工执行同等检查，并在反馈中说明未运行机器校验。

最终反馈必须包含：处理文件数、Wiki 变化、索引变化、日报和周报路径、SQLite 项目/事项/候选变化、今日 action 数量与来源、快照路径和 revision、暂不沉淀内容和待确认歧义。

每日整理不自动执行工程沉淀、站点同步、部署、`git push` 或文章创作。

## 6. 全库整理

执行“执行全库整理”时：

1. 完成通用修改前预检
2. 全面扫描 `daily/`、`raw/`、`notes/`、`wiki/`、`output/` 和模板
3. 按 `SCHEMA.md` 检查并修复缺失或非法 frontmatter
4. 检查全部索引链接
5. 检查全部 `[[wikilinks]]`、Markdown 本地链接和嵌入图片
6. 检查 Wiki `sources`；`wiki/index.md` 为导航例外
7. 检查 Wiki 路径、type、slug、状态、标签和正文最低结构
8. 检查重复、同义和不符合准入条件的页面
9. 人物页清理只列候选，不自动删除或合并
10. 按 `glossary.md` 统一可安全确认的术语
11. 更新实际受到影响的索引
12. 检查 `output/index.md` 不引用不存在的报告
13. 生成 `output/full-review-YYYY-MM-DD.md`
14. 重新执行全套校验，报告仍未解决的问题和原因

不确定的同义页、人物页和内容归属只列候选，不自动合并。

## 7. 工程沉淀

“执行工程沉淀”默认处理本地当天；带日期时处理指定日期。

目标：只记录“用户意图明确 → 执行出现偏差 → 后续被纠正 → 能形成可复用工程规则”的内容。

### 7.1 目标 daily note

- 目标为 `daily/YYYY-MM-DD.md`
- 文件不存在时使用 `templates/daily-template.md` 创建并替换 `{{date}}`
- 只更新 `## 工程沉淀` 下的 agent marker 区块
- 不改写 marker 外的用户内容

固定 marker：

```md
## 工程沉淀
<!-- codex-engineering-deposit:start -->
<!-- codex-engineering-deposit:end -->
```

### 7.2 Codex 记录

1. 从 `C:/Users/Dell/.codex/session_index.jsonl` 按日期定位 session id
2. 匹配 `C:/Users/Dell/.codex/sessions/YYYY/MM/DD/*.jsonl`
3. 再检查 `C:/Users/Dell/.codex/archived_sessions/*.jsonl`
4. 忽略 base instructions、system 和 turn_context
5. 保留用户消息、助手回复及必要工具调用和结果
6. 索引存在但正文不可读时只报告，不推测

### 7.3 Claude Code 记录

1. 从 `C:/Users/Dell/.claude/history.jsonl` 按本地日期筛选候选
2. 使用 `display`、`timestamp`、`project`、`sessionId`
3. 读取 `C:/Users/Dell/.claude/projects/**/<sessionId>.jsonl`
4. 默认排除 `subagents`
5. UTC 时间戳必须转为本地时间
6. `message.content` 同时兼容字符串和 block array
7. 保留 `text`、`tool_use`、`tool_result`，忽略附件和系统噪音

### 7.4 筛选与写入

必须同时满足：用户边界明确、执行存在偏差、后续发生纠正或返工、结论可复用。

排除：顺利完成的任务、普通进度、情绪总结、私人日记、无纠正链路的代码细节和仅由日志推断的内容。

- 每次写入 0—5 条 bullet
- 每条尽量一句话
- 重复执行只替换 marker 内内容
- 没有符合条件的内容时保持 marker 为空
- 标题下只有空 bullet 时用 marker 替换
- 非空用户内容保留在 marker 外

每日整理和全库整理只保留该区块，不自动触发工程沉淀。

## 8. 主题汇总

执行“生成主题汇总：<主题>”时：

1. 搜索 `daily/`、`raw/`、`notes/`、`wiki/`
2. 优先使用已有 Wiki 内容
3. 生成 `output/topic-<主题>-YYYY-MM-DD.md`
4. 文末列出实际引用的源文件
5. 不把推测写成事实

## 9. 站点同步

默认目标：`C:\Code\codex\selfweb\site`。

1. 在目标目录执行 `git status --short`，记录已有修改
2. 执行：

```powershell
$ErrorActionPreference = 'Stop'
& 'C:\Code\codex\selfweb\scripts\sync-site.ps1' -VaultPath 'C:\Code\notes\zxy' -SitePath 'C:\Code\codex\selfweb\site' -Build
```

3. 读取 `C:\Code\codex\selfweb\reports\site-sync-report.md`
4. 必须确认：
   - `是否可以发布：True`
   - Schema 错误、公开输出冲突、缺失字段、断链、占位符、未关联应用笔记、缺失图片、敏感命中均为 0
   - `raw/**/*.md` 被递归发现；只有 `publish: true` 的文章进入站点
   - `raw/YYYY-MM/文章.md` 输出仍为 `articles/文章/index.md`，月份目录不进入公开 URL
5. Quartz 报 `EBUSY` 时，只定位并停止明确占用 `site/public` 的本地预览进程，再重试
6. 检查关键新增页面同时存在于 `content/` 和 `public/`
7. 反馈公开源页面数、Quartz 内容页面数、软件页数、构建结果、报告路径和是否执行 `git push`

## 10. 发布网站

固定配置：

- selfweb root：`C:\Code\codex\selfweb`
- deploy script：`C:\Code\codex\selfweb\scripts\deploy-site.ps1`
- public URL：`http://49.233.192.178/`
- OSS bucket：`blog-self`
- OSS endpoint：`oss-cn-beijing.aliyuncs.com`
- server：`root@49.233.192.178`
- SSH key：`C:\Users\Dell\.ssh\id_ed25519`
- remote path：`/var/www/selfweb`

执行：

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

1. 同步报告 `canPublish=True` 才能部署
2. 发布硬校验任一非零时先修复，不得绕过
3. 默认保留服务器本地图片路径，同时上传 OSS 备份
4. 只有提供并验证可访问的 `-OssPublicBaseUrl` 时才替换图片 URL
5. 不默认使用 `-DeleteOssExtra`
6. 不改动 8081 上的 `couple/syncday` 服务
7. 发布后验证 `/`、`/wiki/`、`/software/` 返回 200
8. 不在文档、记忆或聊天中记录 OSS AccessKey Secret
9. 发布前检查首页“最新内容”，必须按 `created` 降序，且不得混入未确认页面或栏目入口页
10. 发现误发布、误改 `created` 或 `updated` 语义时，先修复源文件，再重新同步和部署

## 11. 启动 selfweb

在 `C:\Code\codex\selfweb\site` 执行：

```powershell
$ErrorActionPreference = 'Stop'
npm run serve -- --port 49153
```

端口占用时换用其他高位端口；8080 报 `EACCES` 时不再纠缠，直接使用高位端口。最终反馈本地访问地址。

## 12. 输出风格与编辑约束

- 默认使用简体中文
- 先给结果，再说明必要原因
- 不展示冗长思维过程
- 摘要准确优先，不追求华丽
- Markdown 文件使用 UTF-8，并保持现有换行风格
- 修改源数据时最小化，修改 Wiki、output 和 index 时仍需保留来源
- 不确定时保守处理并明确列入待确认
