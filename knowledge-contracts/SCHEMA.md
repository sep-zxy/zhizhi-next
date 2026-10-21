# Vault Schema

本文件是 Vault 唯一规范源，定义各类 Markdown 文件的字段、顺序、标签、日期、归档、内容清理、链接、来源和发布校验。`AGENTS.md` 只负责执行顺序，`glossary.md` 只负责术语与标签别名。

下面的 `vault-schema-registry` JSON 块同时供人工、Vault Manager 和发布脚本读取。修改字段、顺序、默认值或枚举时必须先更新本块，再同步修改本文件说明和 `templates/` 下的对应模板；解析失败应视为硬错误，不得回退到代码内的另一套默认规则。

```json vault-schema-registry
{
  "version": 5,
  "dateFormat": "YYYY-MM-DD",
  "tagOrder": ["ai", "knowledge-management", "reading", "writing", "product", "research", "tooling", "workflow", "learning", "life", "daily", "app-note", "index"],
  "semanticTags": ["ai", "knowledge-management", "reading", "writing", "product", "research", "tooling", "workflow", "learning", "life"],
  "structuralTags": {
    "daily": ["daily"],
    "app-note": ["app-note"],
    "index": ["index"]
  },
  "tagAliases": {
    "AI": "ai",
    "人工智能": "ai",
    "pkm": "knowledge-management",
    "knowledge_management": "knowledge-management",
    "知识管理": "knowledge-management",
    "researching": "research",
    "研究": "research",
    "tools": "tooling",
    "tool": "tooling",
    "software": "tooling",
    "工具": "tooling",
    "workflows": "workflow",
    "工作流": "workflow",
    "learn": "learning",
    "学习": "learning",
    "write": "writing",
    "写作": "writing",
    "read": "reading",
    "阅读": "reading"
  },
  "updatedFields": ["title", "source", "author", "published", "summary", "tags", "category", "status", "pricing", "platforms", "open_source", "official_url", "download_url", "publish", "sources", "related"],
  "maintenanceOnly": ["field-order", "whitespace", "heading-format", "archive-path", "index", "wikilink", "spelling"],
  "placeholderValues": {
    "source": "https://placeholder.invalid/source",
    "official_url": "https://placeholder.invalid/official",
    "download_url": "https://placeholder.invalid/download"
  },
  "types": {
    "raw": {
      "paths": ["raw/**/*.md"],
      "exclude": ["raw/index.md"],
      "order": ["title", "source", "author", "published", "created", "updated", "tags", "type", "summary", "publish", "section"],
      "required": ["title", "source", "author", "published", "created", "updated", "tags", "type", "summary", "publish", "section"],
      "nonEmptyWhenPublished": ["title", "source", "created", "updated", "summary"],
      "defaults": {"author": [], "published": null, "tags": [], "type": "literature", "publish": true, "section": "article"},
      "enums": {"type": ["literature"], "section": ["article"]},
      "strict": true,
      "archive": "raw/YYYY-MM",
      "archiveBy": "created"
    },
    "daily": {
      "paths": ["daily/YYYY-MM-DD.md"],
      "order": ["title", "tags", "created", "type", "summary"],
      "required": ["title", "tags", "created", "type", "summary"],
      "defaults": {"tags": ["daily"], "type": "fleeting"},
      "enums": {"type": ["fleeting"]}
    },
    "note": {
      "paths": ["notes/**/*.md"],
      "exclude": ["notes/blog/**/*.md", "notes/software/**/*.md", "notes/apps/**/*.md", "notes/**/index.md"],
      "order": ["title", "tags", "created", "type", "summary"],
      "required": ["title", "tags", "created", "type", "summary"],
      "defaults": {"tags": [], "type": "permanent"},
      "enums": {"type": ["fleeting", "literature", "permanent"]}
    },
    "blog": {
      "paths": ["notes/blog/**/*.md"],
      "exclude": ["notes/blog/index.md"],
      "order": ["title", "publish", "section", "type", "summary", "tags", "category", "status", "created", "updated"],
      "required": ["title", "publish", "section", "type", "summary", "tags", "category", "status", "created", "updated"],
      "nonEmptyWhenPublished": ["title", "summary", "category", "created", "updated"],
      "defaults": {"publish": false, "section": "blog", "type": "permanent", "tags": [], "category": "未分类", "status": "draft"},
      "enums": {"section": ["blog"], "type": ["permanent"], "status": ["draft", "published", "archived"]}
    },
    "software": {
      "paths": ["notes/software/*.md"],
      "exclude": ["notes/software/index.md"],
      "order": ["title", "publish", "section", "software_id", "status", "category", "tags", "type", "platforms", "pricing", "open_source", "official_url", "download_url", "summary", "related", "sources", "created", "updated"],
      "required": ["title", "publish", "section", "software_id", "status", "category", "tags", "type", "platforms", "pricing", "open_source", "official_url", "download_url", "summary", "related", "sources", "created", "updated"],
      "nonEmptyWhenPublished": ["title", "software_id", "category", "summary", "created", "updated"],
      "defaults": {"publish": false, "section": "software", "status": "wishlist", "tags": ["tooling"], "type": "permanent", "platforms": [], "pricing": "unknown", "open_source": "unknown"},
      "enums": {"section": ["software"], "status": ["used", "testing", "archived", "wishlist"], "type": ["permanent"], "pricing": ["free", "freemium", "paid", "unknown"]}
    },
    "app-note": {
      "paths": ["notes/apps/**/*.md"],
      "exclude": ["notes/apps/index.md"],
      "order": ["title", "publish", "section", "type", "related_software", "summary", "tags", "created", "updated"],
      "required": ["title", "publish", "section", "type", "related_software", "summary", "tags", "created", "updated"],
      "nonEmptyWhenPublished": ["title", "related_software", "summary", "created", "updated"],
      "defaults": {"publish": false, "section": "app-note", "type": "permanent", "tags": ["app-note"]},
      "enums": {"section": ["app-note"], "type": ["permanent"]}
    },
    "wiki": {
      "paths": ["wiki/concepts/*.md", "wiki/entities/*.md", "wiki/topics/*.md"],
      "order": ["title", "publish", "section", "type", "tags", "sources", "created", "updated", "summary", "status"],
      "required": ["title", "publish", "section", "type", "tags", "sources", "created", "updated", "summary", "status"],
      "nonEmptyWhenPublished": ["title", "type", "sources", "created", "updated", "summary"],
      "defaults": {"publish": true, "section": "wiki", "tags": [], "sources": [], "status": "stub"},
      "enums": {"section": ["wiki"], "type": ["concept", "entity", "topic"], "status": ["stub", "mature"]}
    },
    "continuity-snapshot": {
      "paths": ["output/continuity.md"],
      "order": ["title", "tags", "created", "updated", "type", "publish", "summary", "generated_revision"],
      "required": ["title", "tags", "created", "updated", "type", "publish", "summary", "generated_revision"],
      "defaults": {"tags": ["knowledge-management", "workflow"], "type": "permanent", "publish": false},
      "enums": {"type": ["permanent"]},
      "strict": true
    }
  }
}
```

## 1. 通用约束

- Markdown 使用 UTF-8
- 日期格式为 `YYYY-MM-DD`
- 周报格式为 `YYYY-Www`
- Vault 内路径使用相对路径和 `/`
- frontmatter 使用 YAML，必须位于文件开头
- 字段按注册表顺序写入；仅字段重排不更新 `updated`
- 标签少而准，标准顶级标签见 `glossary.md`
- 除 raw 外新增内容默认不公开；raw 默认公开但允许单篇关闭

## 2. 文件类型注册表

### 2.1 Daily note

路径：`daily/YYYY-MM-DD.md`

必需字段：

```yaml
title: 标题
tags: [daily]
created: YYYY-MM-DD
type: fleeting
summary: 一句话摘要
```

文件名日期、`created` 和目标日期必须一致。工程沉淀 marker 如存在，必须保留。

### 2.2 Raw

路径：`raw/YYYY-MM/*.md`；采集器临时写入 `raw/*.md` 的文件在每日整理时归档。`raw/index.md` 例外。

必需字段：

```yaml
title: 标题
source: 原始来源 URL
author: []
published:
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: []
type: literature
summary: 一句话摘要
publish: true
section: article
```

- 字段集合和顺序固定，不允许保留 `description` 或采集器私有属性
- 缺少作者时写 `author: []`；无法确认原始发布日期时保留空 `published:`
- 新文件默认 `publish: true`；缺少可确认的 `source` 时写入注册表安全默认值，允许先公开，后续可按需替换
- `summary` 必须是一句话且不超过 120 个字符；已有合格 `summary` 时直接删除 `description`，只有 `summary` 为空时才从 `description` 提取一句
- 每篇保留 1–4 个语义标签，不使用 `raw` 结构标签重复表达目录与 `section`

### 2.3 普通 Note

路径：排除 blog、software、app-note 和 index 后的普通 `notes/**/*.md`

```yaml
title: 标题
tags: []
created: YYYY-MM-DD
type: fleeting | literature | permanent
summary: 一句话摘要
```

### 2.4 Blog

路径：`notes/blog/*.md`

必需字段：

```yaml
title: 标题
publish: false
section: blog
type: permanent
summary: 一句话摘要
tags: []
category: 分类
status: draft | published | archived
created: YYYY-MM-DD
updated: YYYY-MM-DD
```

`publish: true` 必须有用户明确授权；含 `{{...}}` 等正文模板占位符的博客只能是 `draft` 且不得公开。Schema 注册表中的 `.invalid` 安全默认值不属于正文模板占位符。

### 2.5 Software

路径：`notes/software/<software_id>.md`

必需字段：

```yaml
title: 软件名称
publish: false
section: software
software_id: 小写稳定 ID
status: used | testing | archived | wishlist
category: 分类
tags: [tooling]
type: permanent
platforms: []
pricing: free | freemium | paid | unknown
open_source: true | false | unknown
official_url: 官方地址或待确认
download_url: 官方下载地址或待确认
summary: 一句话摘要
related:
  wiki: []
  articles: []
  app_notes: []
sources: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
```

规则：

- 文件名必须等于 `software_id`
- 价格、平台、开源状态、官网和下载地址必须有可靠来源
- 下载地址优先官方来源；非官方地址必须明确标注性质，不得伪装成官方下载
- `related.app_notes` 使用 Vault 相对路径，保留并去重
- 软件页记录事实和总体判断，具体操作放入 app-note
- 主动维护的软件原则上应有关联 Wiki 实体；缺失时可在整理报告中列为候选

### 2.6 App note

路径：`notes/apps/<software_id>-<主题>.md`

必需字段：

```yaml
title: 标题
publish: false
section: app-note
type: permanent
related_software: software_id
summary: 一句话摘要
tags: [app-note]
created: YYYY-MM-DD
updated: YYYY-MM-DD
```

规则：

- `related_software` 必须小写、稳定、URL 友好
- 优先与文件名前缀一致
- 对应软件页存在时，双方关联必须一致
- 对应软件页不存在时不自动创建，列入待确认

### 2.7 Wiki

路径：

- `wiki/concepts/<slug>.md`
- `wiki/entities/<slug>.md`
- `wiki/topics/<slug>.md`

必需字段：

```yaml
title: 页面标题
publish: true
section: wiki
type: concept | entity | topic
tags: []
sources: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
summary: 一句话摘要
status: stub | mature
```

路径和 `type` 必须对应。Wiki 已获得用户长期公开授权，新页面默认 `publish: true`；单篇仍可按用户要求改为 `false`。

### 2.8 Output

日报、周报、主题汇总和全库报告至少包含：

```yaml
title: 标题
tags: [knowledge-management]
created: YYYY-MM-DD
type: permanent
summary: 一句话摘要
```

`output/continuity.md` 是 Continuity SQLite 的只读导出快照，额外必须包含：

```yaml
updated: YYYY-MM-DD
publish: false
generated_revision: 0
```

该文件只能由 `continuity-cli.exe snapshot export` 生成；不得人工维护项目、事项、marker、checkbox 或生命周期状态。SQLite 数据库是唯一实时数据源。

### 2.9 Index

各目录统一使用小写 `index.md`。索引是导航页，可以不遵循 Wiki 的 `concept/entity/topic` 类型，但应包含标题、创建日期、类型和摘要。

索引只引用真实存在的文件或页面，并为条目提供一句简短说明。Git 中不得保留同目录下仅大小写不同的 `INDEX.md`。

### 2.10 模板同步

模板与 Schema 的对应关系固定为：

- `templates/daily-template.md` → `daily`
- `templates/raw-article-template.md` → `raw`
- `templates/note-template.md` → `note`
- `templates/blog-note-template.md` → `blog`
- `templates/software-note-template.md` → `software`
- `templates/app-note-template.md` → `app-note`
- `templates/continuity-template.md` → `output/continuity.md`

维护规则：

- 模板 frontmatter 的字段集合、字段顺序、枚举值和默认值必须与本文件一致
- Schema 注册表发生字段、顺序、默认值或枚举变化时，必须在同一次修改中同步对应模板
- raw、software 中无法预填的事实 URL 使用 `placeholderValues` 注册的安全默认值
- `{{...}}`、`YYYY-MM-DD`、示例标题和示例摘要只允许存在于模板；模板实例进入正式目录后必须替换，否则发布校验应阻止公开
- `templates/` 本身不进入每日整理增量、公开内容扫描或网站输出

## 3. 字段语义

### 3.1 `created`

表示内容首次创建日期。补 frontmatter 时优先使用：

1. 文件名中的可靠日期
2. 原始材料明确日期
3. Git 首次记录日期
4. 文件创建时间

不得因为重新整理而改成当天。

### 3.2 `updated`

以下变化更新为当天：

- 正文结论或关键事实发生变化
- 新增有效案例或重要内容
- 新来源改变结论、可信度或事实判断
- `summary`、`tags`、`category`、`status`、`pricing`、`platforms`、`open_source`、官网、下载地址、`publish`、`sources`、`related` 等有效属性发生变化

字段排序、空格、换行、标题层级、拼写、索引、双链、raw 月份移动和不影响结论的来源补记不更新时间。`updated` 仍用于记录有效修改和“最近更新”视图，但公开网站首页与“最新内容”列表统一按 `created` 降序排列。全库整理或旧内容维护不得改变 `created`，因此不会把旧内容顶到最新发布位置。

### 3.3 `publish`

- 新增 raw 和 Wiki 默认 `true`；其他类型默认 `false`
- raw 允许手动关闭公开；缺少可确认的 `source` 时可使用注册表安全默认值，不阻止公开
- Wiki 按用户长期授权默认公开；其他类型只有明确授权才可改为 `true`
- 撤销误发布可以改回 `false`，但纯撤销不改变 `updated`
- `publish: true` 的文件必须通过第 11 节发布校验

### 3.4 `status`

- Wiki：`stub | mature`
- Blog：`draft | published | archived`
- Software：`used | testing | archived | wishlist`

不得使用“小条目”等非枚举值；中文只用于解释。

### 3.5 标签

顶级标签只使用：

- `ai`
- `knowledge-management`
- `reading`
- `writing`
- `product`
- `research`
- `tooling`
- `workflow`
- `learning`
- `life`

标签统一为小写 kebab-case，去重后按注册表顺序排列。普通内容只保留 1–4 个语义标签；来源网站名、一次性描述和与 `section/type/status/category` 重复的标签必须删除。

`daily`、`app-note`、`index` 只用于对应文件类型。raw 不使用 `raw` 标签，因为目录与 `section: article` 已表达其角色。新增长期标签或别名映射前先更新 `glossary.md` 和注册表。

## 4. Raw 正文清理与月份归档

### 4.1 正文清理

每日整理只对本次新增或变化的 raw 执行以下清理，不改写作者观点：

- 统一标题层级、列表、代码块、空行和图片语法
- 删除网页导航、登录提示、分享控件、推荐阅读、重复标题等确定无关内容
- 完全相同的图片链接只保留第一次出现
- 本地图片目标必须存在
- 远程图片先尝试 HEAD，必要时以 GET 验证；只有明确返回 404、410 或内容类型不是图片时才自动删除
- 403、429、超时和临时网络错误不得作为删除依据，只记录到日报和校验报告
- 相邻等价图片只保留一张已确认有效且更清晰的图片
- 正文、作者、来源、发布时间、引用、代码和关键截图必须保留

### 4.2 月份归档

- 只有 `raw/` 按月整理，格式固定为 `raw/YYYY-MM/文件名.md`
- 归档月份优先使用 `created`；缺失时依次使用 Git 首次记录日期、文件创建日期
- `raw/index.md` 始终保留在根目录
- 移动文件不改变 `updated`
- 移动后同步更新显式 Vault 相对路径、Wiki `sources` 和索引链接；仅文件名 Wikilink可以保持不变
- 目标已存在时不得覆盖，必须报告冲突
- 同一文件已在正确月份时不得重复移动或重写

## 5. Wiki 分类与准入

### 5.1 Concept

适合方法、模型、术语、原理和框架。应能脱离单篇来源独立解释，并具有复用价值。

### 5.2 Entity

适合稳定命名的组织、工具、软件、产品、书籍、模型、插件、Agent Skill、GitHub/GitLab 项目和完整工作流。

中心对象是稳定工具或项目时，即使只有一篇源材料，也可以创建实体页，但必须确认稳定名称和官方来源。顺带提及或仅用于比较的对象不自动创建。

软件页与 Wiki 实体分工：

- `notes/software/`：价格、平台、下载、使用状态和个人判断
- `wiki/entities/`：稳定定义、核心能力、边界和知识关系

### 5.3 人物实体

人物条目是人工确认的例外，不是每日整理的自动沉淀目标。

- 作者、维护者、分享者和社区用户名默认只保留在 `author`、正文或来源中
- 材料同时包含人物与作品、项目、工具时，优先沉淀后者
- 三份以上独立来源只构成候选条件，不构成自动准入
- 只有用户明确要求，或人物本身是经用户确认的长期研究对象时，才能创建
- 已有人物页不符合规则时，只列清理候选，不自动删除

### 5.4 Topic

适合综合性主题、问题域、专题综述和对比分析。主题页应连接多个概念或实体，而不是把单个对象换一种标题重写。

### 5.5 Stub 与 Mature

`stub` 最低要求：

- 清晰的定义或简介
- 当前已知边界或初步结论
- 可追溯来源

`mature` 还应包含：

- 多个关键维度、能力或贡献
- 适用边界或反例
- 相关概念或实体
- 明确的当前结论

标题名称可以因类型调整，不强制机械使用完全相同的小节名，但语义要完整。

## 6. Wiki 命名

- 文件名使用小写英文、数字和连字符
- 使用稳定官方名称或通行英文名称生成 slug
- 不使用空格、下划线和临时描述性长句
- 创建前同时按文件名、frontmatter `title` 和常见别名查重
- 不创建仅大小写、缩写或中英文翻译不同的同义页

## 7. Sources

- Wiki 页面必须有非空 `sources`
- `sources` 可以包含 Vault 相对路径和可靠外部 URL
- 本地来源路径必须存在；已删除来源应移除或替换
- 从 Vault 源材料沉淀的页面至少保留一个本地来源
- 工具、软件、项目、价格、平台、协议等事实优先补官方 URL
- 正文“来源”小节与 frontmatter 可以同时存在，但不得互相矛盾
- `wiki/index.md` 为导航页例外，不要求 `sources`

## 8. Wikilink 与本地资源

1. 首次提到已有知识点时优先使用 `[[wikilink]]`
2. 同一篇文件中同一目标通常只链接一次
3. 不把普通词语强行链接
4. 目标解析顺序：精确相对路径、文件名、唯一标题或别名
5. 无法唯一解析时视为断链或歧义链接
6. 重要目标不存在时，只有满足准入规则才创建 stub；否则进入待确认
7. Vault 内部导航优先使用 Wikilink，不用普通 Markdown 链接替代
8. `![[图片或附件]]` 的目标必须真实存在
9. 文档里的示例路径放在代码块或行内代码中，不写成虚假 Wikilink

## 9. Index、日报和周报

### 9.1 Index

- 说明目录用途
- 只列真实存在的近期或重要文件
- 每项附一句简短说明
- 保持导航属性，不扩写成长文
- 目录无变化时不重写
- `output/index.md` 不得引用不存在的日报、周报或报告

### 9.2 日报

路径：`output/daily-digest/YYYY-MM-DD.md`

固定小节：

1. 今日新增内容
2. 今日沉淀出的知识点
3. 新建或更新的 Wiki 页面
4. 新增的关键双链
5. 待确认问题
6. 明天可继续整理的方向
7. 暂不沉淀的内容及原因

日报是历史快照。同一天重复执行更新同一文件，不能重复追加同一事项。

### 9.3 周报

路径：`output/weekly/YYYY-Www.md`

至少包含：

- 本周主要主题
- 反复出现的话题
- 值得长期保留的洞察
- 本周新增的重要页面
- 后续可以继续推进的方向

周报只汇总该周已有日报和可追溯证据，不引入无来源的新结论。

### 9.4 Continuity SQLite 与只读快照

- `%LOCALAPPDATA%\Zxy\ContinuityWidget\continuity.db` 是项目、里程碑、事项、证据、事件回执、建议、历史、设置和窗口状态的唯一实时数据源；当前 schema 版本为 v2。
- `output/todo.md`、`output/todo-candidates.md` 和 `output/todo-archive.md` 只属于首次迁移输入，不再注册为 Vault Schema，不得由每日整理或小组件继续写入。
- 自动整理只通过 `continuity-cli.exe project upsert`、`item upsert` 和 `project summarize` 写入；所有写入使用 SQLite `IMMEDIATE` 事务并递增一次全局 revision。
- Project 使用 UUID 主键并可保留 `legacy_id`；新项目默认 `candidate`，确认后才进入 active。
- Item 的 `kind`、`horizon`、`status` 是独立维度；同项目 fingerprint 唯一，语义近似只作为候选，不自动合并。
- Project 的成熟度为 `idea|defined|executing|validating|mature`；里程碑状态为 `planned|in_progress|completed|cancelled`，项目百分比按未取消里程碑权重计算，无里程碑时必须导出 `unplanned`/“未规划”。
- 工程事件以 `source + eventId` 唯一，payload hash 必须稳定；仅精确目标的成功完成事件可自动关闭未锁定 action，冲突、模糊目标和受保护事项进入 suggestion，failure/cancelled 只追加 evidence。
- 人工修改设置 `manual_override`；自动更新不得覆盖人工字段，只能追加 evidence 或写入 suggestion。
- `output/continuity.md` 每日整理结束后通过 `snapshot export` 稳定生成；相同 revision 不重写，正文必须标注“只读快照”。
- 快照必须包含项目状态、目标、成功标准、成熟度、里程碑及加权进度、今日/本周/稍后、待确认/下一步、阻塞标记、待处理建议、来源引用和 `generated_revision`，不得包含完整聊天正文。
- 完整导出示例以 `templates/continuity-template.md` 为准。

## 11. 发布校验

任何 `publish: true` 文件必须包含：

```yaml
title:
publish: true
section: wiki | software | article | blog | app-note
summary:
tags: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
```

还必须满足：

- 不含 `{{...}}`、`YYYY-MM-DD`、`应用笔记标题`、`软件名称`、`一句话说明...` 等模板占位符
- Wikilink 和本地图片全部可解析
- app-note 已关联有效软件页
- 不含敏感信息或凭证
- 首页最新内容不会因为纯格式或误改 `updated` 被错误顶置
- 首页和 `latest.md` 按 `created` 降序排列；同日内容按 `updated`、标题稳定排序
- `wiki/index.md`、各栏目 `index.md` 等导航页不得混入内容型最新列表

### 11.1 Vault Manager 缺失属性补全

- 编辑器必须从本文件注册表读取 `required`、`defaults`、`enums` 和 `nonEmptyWhenPublished`，不得在前端维护另一套字段规范
- 空输入框显示界面提示；点击“一键补齐可公开属性”后，对无法推导的 URL 字段写入注册表中的 `.invalid` 安全默认值
- “一键补齐可公开属性”优先写入 Schema 默认值或可从文件路径、正文、文件日期可靠推导的值，事实 URL 才使用统一安全默认值
- `summary` 可从正文首个有效段落生成；`created` 可按第 3.1 节推导；Wiki `type` 可从目录推导
- `.invalid` 安全默认值用于明确表示“该事实暂未记录”，不冒充真实来源；它满足发布字段完整性要求，不阻止 `publish: true`
- 界面必须明确提示哪些字段使用了安全默认值，但真实属性替换是可选维护动作，不是发布前置条件
- raw 只有在 `source` 既为空又无法写入安全默认值时才阻止公开
- 补全导致有效属性变化时更新 `updated`；重复执行且无变化时不得重写文件

全站发布仍以 selfweb 同步报告中的 `canPublish=True` 为最终硬门禁。

## 12. 校验层级与失败处理

### 12.1 文件级

- frontmatter 存在且 YAML 结构完整
- 必需字段齐全
- 日期、枚举和路径类型合法
- 文件名与日期、slug 或 software_id 一致

### 12.2 内容级

- Wiki 达到对应状态的最低结构
- 事实有可靠来源
- 不存在明显模板占位符
- 不把猜测写成结论

### 12.3 交叉引用级

- Wikilink、索引、sources、图片和关联路径可解析
- app-note 与 software 双方关联一致
- 没有同义重复页或仅大小写不同的文件

### 12.4 产物级

- 日报、周报和 continuity 只读快照结构完整
- 报告只引用真实文件
- 快照 `generated_revision`、项目 UUID、事项 fingerprint、来源和状态合法

### 12.5 发布级

- 除 raw 与 Wiki 的默认公开规则外，未经授权不提升 `publish`
- 公开文件通过第 10 节全部校验
- 任一硬校验失败时不得同步发布

### 12.6 处理策略

- 可确定、可逆的机械问题：直接修复
- 涉及语义、归属、公开权限、同义合并和人物准入：只列候选
- 无法解析的链接：能唯一确定目标则修复，否则报告
- 校验结束后再次运行同一校验；仍失败的项目必须写入全库报告

## 13. 质量底线

- 不从空气中创造知识
- 不把猜测写成事实
- 不丢失来源
- 不制造同义重复页
- 不为了完整而扩写无来源内容
- 不自动创建人物资料库
- 不让索引和报告引用不存在的文件
- 不因纯维护操作改变公开内容排序
- 输入无变化时，不制造无意义文件修改
