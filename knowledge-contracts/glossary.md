# 术语表

本文件用于统一知识库中的常用术语，避免同义词混用。

---

## 固定术语

- `Claude Code`
  - 不使用：CC / claude-code / Anthropic 编程工具

- `Obsidian`
  - 不使用：黑曜石笔记 / obsidian app

- `frontmatter`
  - 不使用：元数据头 / YAML头 / 文件头信息

- `AI Agent`
  - 中文可写：AI 代理 / 智能体
  - 不使用：agent / Agent 智能体（正文首次出现可写 `AI Agent`）

- `Prompt Engineering`
  - 中文可写：提示词工程
  - 不使用：提示工程 / prompt工程

- `Context Engineering`
  - 中文可写：上下文工程
  - 不使用：context工程

- `Harness Engineering`
  - 中文可写：驾驭工程
  - 不使用：Harness工程（标题和 wiki title 可保留英文）

- `Model Context Protocol`
  - 简写：MCP
  - 首次出现优先写：Model Context Protocol（MCP）

- `Trellis`
  - 不使用：trellis / Trellis workflow harness（正文可解释其职责）

- `Color System`
  - 中文可写：色彩系统
  - 不使用：颜色系统

- `daily note`
  - 中文可写：日记 / 每日笔记
  - 文件结构里统一指向：`daily/`

- `raw`
  - 含义：原始输入、待编译素材
  - 不使用：收集箱 / inbox / 待整理

- `wiki`
  - 含义：结构化知识层
  - 不使用：知识库正文 / 主知识区

- `output`
  - 含义：AI 生成的日报、周报、主题汇总、查询产物
  - 不使用：结果区 / 导出区

---

## 标签标准写法

标签统一使用小写 kebab-case，并按以下顺序排列：

- `ai`
- `knowledge-management`
- `reading`
- `writing`
- `product`
- `research`
- `workflow`
- `tooling`
- `learning`
- `life`

结构标签仅用于标识文件角色：

- `daily`
- `app-note`
- `index`

`raw` 不再作为标签使用；目录和 `section: article` 已经表达文件角色。

### 标签别名

- `AI`、`人工智能` → `ai`
- `pkm`、`knowledge_management`、`知识管理` → `knowledge-management`
- `tools`、`tool`、`software`、`工具` → `tooling`
- `workflows`、`工作流` → `workflow`
- `researching`、`研究` → `research`
- `learn`、`学习` → `learning`
- `write`、`写作` → `writing`
- `read`、`阅读` → `reading`

来源网站名、一次性描述以及与 `section/type/status/category` 重复的词不作为标签。普通内容保留 1–4 个语义标签；新增标准标签或别名前，必须同步更新 `SCHEMA.md` 的机器注册表。

---

## 命名偏好

- 用户原始笔记标题：允许中文
- wiki 文件名：优先英文小写连字符
- 日期格式：统一 `YYYY-MM-DD`
- 周报格式：统一 `YYYY-Www`

---

## 链接偏好

- 在正文里优先使用 `[[wikilinks]]`
- 不用普通 Markdown 链接替代 vault 内部链接
