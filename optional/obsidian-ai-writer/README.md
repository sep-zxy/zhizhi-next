# Obsidian AI Writer

独立可选插件，不属于主平台安装/启动依赖。开发前进入 optional/obsidian-ai-writer；内部相对布局保持不变。

Obsidian 长文写作插件。它读取用户明确选择的笔记上下文，通过 OpenAI Compatible Chat Completions API，完成提纲、初稿、审校和插图占位图插入。

## 主要能力

- 自定义 Base URL、API Key 和模型。
- 右侧写作面板与编辑器命令。
- 当前选区、当前笔记和显式选择笔记作为上下文。
- 观点叙事长文、应用体验指南、结构化软件档案和自定义四种文章风格。
- 每种风格自带可直接修改的文章提示词，内置风格无需重复填写写作目标。
- 按目标笔记持久化素材引用、文章风格、提示词、阶段补充和生成结果。
- 自动监听当前文件切换与重命名，重新打开笔记时恢复右侧面板状态。
- 每篇笔记保留最近 20 个会话版本，可在“历史”页签中恢复。
- 每一步都可输入补充与修改要求，成稿支持目标字数及本地范围检查。
- 自动检测“待补充”、TODO、TBD、XXX 等未完成标记，并尝试修复一次。
- 流式输出、取消、阶段重试和非流式兼容模式。
- 草稿与审校稿差异预览。
- 支持博客、应用笔记和软件笔记三种类型，可复用 Vault 内模板直接创建文件。
- 插图阶段在正文合适位置生成本地 SVG 占位图和说明，不调用图片 API。
- 确认后直接写回目标文件并保留原 frontmatter，同时刷新 `updated` 日期。

## 默认模板

| 类型 | 模板 | 输出目录 |
|---|---|---|
| 博客 | `templates/blog-note-template.md` | `notes/blog` |
| 应用笔记 | `templates/app-note-template.md` | `notes/apps` |
| 软件笔记 | `templates/software-note-template.md` | `notes/software` |

模板路径可以在插件设置中修改。通过功能区或命令面板打开独立的“按模板新建”页面，选择模板并输入文章名称，即可在当前活动笔记所在文件夹创建文件；重名时自动追加编号。

写作素材、模板类型和文章风格同时生效：素材约束事实，模板类型控制 frontmatter 与模板兼容性，文章风格和可编辑提示词控制观点、节奏与结构。只有选择“其他”风格时，面板才显示“这篇文章想讲什么”。

## 插图占位图

插图阶段会把占位图保存到：

```text
assets/ai-writer/<文章标识>/placeholder-01.svg
```

正文中会直接插入图片和说明。后续生成真实图片时，可以覆盖同一路径的 SVG 文件，无需再次修改文章链接。

## 开发

```powershell
$ErrorActionPreference = 'Stop'
rtk npm install
rtk npm test
rtk npm run build
```

把 `manifest.json`、`main.js`、`styles.css` 复制到测试 Vault 的 `.obsidian/plugins/ai-writer/` 后启用。

## 网络边界

真实流式请求使用原生 `fetch`，因此目标服务必须允许 Obsidian 所在 WebView 的 CORS 请求。设置中可切换为 Obsidian `requestUrl` 兼容模式；它可绕过 CORS，但只能在完整响应返回后显示结果。

API Key 保存在插件 `data.json` 中，属于本地明文配置。插件不会记录 Key，也不会把内容发送到用户配置 Base URL 之外的服务。
