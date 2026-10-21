# Continuity Widget

Windows 本地 SQLite 项目控制台。EXE 默认打开完整项目工作区，集中管理项目目标、Todo、待确认事项、阻塞项、自动建议、里程碑进度和项目成熟度；托盘另提供只聚焦当前项目的紧凑组件，并包含幂等工程事件回写与无服务 JSON CLI。

## 稳定入口

```text
C:\Code\rust\continuity-widget\bin\continuity-widget.exe
C:\Code\rust\continuity-widget\bin\continuity-cli.exe
```

数据库固定为：

```text
%LOCALAPPDATA%\Zxy\ContinuityWidget\continuity.db
```

首次启动且数据库不存在时，会一次性导入 `C:\Code\notes\zxy\output\todo*.md` 与 `output\continuity.md`。正常运行时 SQLite 是唯一实时数据源；Vault Markdown 同时保留为可重建本地索引的恢复来源。

## 界面

- `项目工作区`：EXE 默认入口。以 Plane v1.3.1 Project Workspace 为唯一信息架构参考，采用 48px rail、240px 项目导航、List/Board、固定视图、里程碑表、活动流和 420px Side Peek；使用本项目自己的中文文案、数据、Lucide 图标和 clean-room 代码。
- `项目概览`：维护目标、成功标准、状态和成熟度；进度只按未取消里程碑的权重派生，没有里程碑时显示“未规划”。
- `工作项`：明确分流 Todo、待确认、阻塞、自动建议和已完成；类型、里程碑、优先级筛选真实作用于 List/Board，Side Peek 是统一编辑入口。
- `草稿保护`：外部 revision 变化时保留当前草稿并显示内联冲突条，不静默覆盖编辑内容。
- `紧凑组件`：由托盘按需打开，只显示当前项目的“现在做 / 待确认 / Todo、待确认、里程碑计数”。
- `伴随预览`：优先出现在主组件左侧，空间不足时转到右侧；内容只分页，不滚动。

## CLI

CLI 不启动服务或 HTTP 端口。输入使用 `--input <json文件>` 或 `--input -`，stdout 仅输出 JSON。

```powershell
$ErrorActionPreference = 'Stop'
& '.\bin\continuity-cli.exe' database status
& '.\bin\continuity-cli.exe' database rebuild-from-markdown --input '.\rebuild.json'
& '.\bin\continuity-cli.exe' project upsert --input '.\project.json'
& '.\bin\continuity-cli.exe' project confirm --input '.\confirm.json'
& '.\bin\continuity-cli.exe' item upsert --input '.\item.json'
& '.\bin\continuity-cli.exe' item complete --input '.\complete.json'
& '.\bin\continuity-cli.exe' item list --input '.\project-items.json'
& '.\bin\continuity-cli.exe' project summarize --input '.\summary.json'
& '.\bin\continuity-cli.exe' project progress --input '.\project-progress.json'
& '.\bin\continuity-cli.exe' milestone upsert --input '.\milestone.json'
& '.\bin\continuity-cli.exe' milestone save --input '.\milestone-patch.json'
& '.\bin\continuity-cli.exe' event apply --input '.\event.json'
& '.\bin\continuity-cli.exe' event status --input '.\event-key.json'
& '.\bin\continuity-cli.exe' event revert --input '.\event-key.json'
& '.\bin\continuity-cli.exe' event retry-pending
& '.\bin\continuity-cli.exe' suggestion apply --input '.\suggestion.json'
& '.\bin\continuity-cli.exe' suggestion reject --input '.\suggestion.json'
& '.\bin\continuity-cli.exe' database repair-legacy-encoding --input '.\repair.json'
& '.\bin\continuity-cli.exe' batch undo-last
& '.\bin\continuity-cli.exe' snapshot export --input '.\snapshot.json'
```

自动批次写入前通过 SQLite backup API 备份数据库，默认只保留最近 7 份：

```text
%LOCALAPPDATA%\Zxy\ContinuityWidget\backups\
```

### 从 Markdown 重建本地索引

重建命令默认只在临时数据库中预演并返回项目/事项计数。只有 `apply=true` 才会先备份旧库，再用临时库替换现有数据库。

```json
{
  "vaultPath": "C:/Code/notes/zxy",
  "apply": false
}
```

确认预演计数后再把 `apply` 改为 `true`，并先退出正在运行的 Continuity 窗口。旧库会保存在 `backups/` 和 `rebuild-backups/`；临时库构建失败不会触碰当前数据库。

恢复解析器读取 `output/continuity.md`、`output/todo.md` 和 `output/todo-candidates.md`。Markdown 中不存在的完成历史、receipt 和人工编辑无法恢复，因此这是灾备/重新索引操作，不是日常同步命令。

## 工程事件

Codex Skill 位于 `C:\Users\Dell\.codex\skills\continuity-update-progress`，推荐通过 `C:\Code\codex\dev-tools\continuity-event.ps1` 提交。事件以 `source + eventId` 幂等：同一 payload 重放返回既有结果，不同 payload 复用同一事件 ID 返回 `event_conflict`。

- 精确的 `itemId`，或精确的 `projectId + fingerprint`，才允许自动定位事项。
- `success + work_item.completed` 只会自动完成 open、action 且未人工锁定的事项。
- 模糊目标、question、人工锁定或其他冲突进入 suggestion，由用户应用或拒绝。
- `failure` 和 `cancelled` 只追加证据，不关闭事项。
- 普通事项事件不会自动把项目设为 completed、archived 或 mature。

Claude Code、Git post-commit 和 GitHub Actions 的默认关闭样例见 `docs/integrations.md`。没有精确 project/item UUID 时只审计，不自动关单。

## 构建

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Code\rust\continuity-widget'
rtk cargo fmt --check
rtk cargo check --all-targets
rtk npm run build
rtk cargo build --release --bin continuity-cli
rtk npm run tauri -- build --no-bundle
```

生产版前端内嵌，不访问 `127.0.0.1:1420`。项目使用 rusqlite bundled SQLite，不需要单独安装 SQLite。
