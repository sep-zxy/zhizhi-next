# SQLite Continuity Widget 全量改造实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**目标：** 将 Continuity Widget 全量改造成以 SQLite 为唯一实时数据源的 Windows Todo 小组件，提供清晰的“现在做 / 每日确认 / 本周方向”、独立编辑器、候选项目确认、主动退化、无滚动分页、伴随预览和无服务 CLI 扩展点。

**架构：** 根 Rust crate 改为共享 `continuity_core`，集中负责 SQLite、业务规则、历史、备份、快照和 JSON 命令；Tauri 与 `continuity-cli.exe` 都只调用该核心。主组件只呈现派生视图，编辑器维护项目与事项，伴随预览独立成窗；三个窗口通过同进程事件刷新，外部 CLI 写入后通过 `meta.revision` 每秒检查刷新。

**技术栈：** Rust 2024、rusqlite（bundled SQLite）、serde、uuid、chrono、Tauri 2、TypeScript 7、Vite 8、Lucide。

---

## 1. 审核结论摘要

本计划采用以下已确认规则，不再保留旧 Markdown 运行时兼容层：

- SQLite 是唯一实时数据源，路径固定为 `%LOCALAPPDATA%\Zxy\ContinuityWidget\continuity.db`。
- 首次启动在数据库不存在时一次性导入旧 `output/todo*.md` 和 `output/continuity.md`，成功后运行时不再读取这些文件。
- `output/continuity.md` 改为每日整理结束后由 CLI 导出的只读快照；旧 Todo Markdown 和对应模板在导入确认成功后退出 Schema。
- Project 是最大实体，使用 UUID 主键，保留 `legacy_id`；候选项目确认后才进入主组件。
- Item 同时具有 `kind`、`horizon`、`status` 三个独立维度，不再靠 Markdown 标题或所在区块推断状态。
- “现在做”是查询结果，不复制 Todo；默认 3 项，可设置 1–5 项，完成后自动补位。
- “每日确认”只显示达到阈值的 `daily + open` 事项，项目无活跃事项时不出现。
- “本周方向”覆盖全部 `active + paused` 项目，每个项目在主组件只占一行。
- 主组件和伴随预览永远不出现滚动条；空间不足或内容极端超量时分页。
- 编辑器独立成窗，自动保存、可改每日/本周/稍后、可改事项类型、可绑定并打开项目目录。
- 自动整理不能覆盖人工字段；只能新增来源、更新未锁定字段，或创建待确认建议。
- 本轮只实现 CLI/API 扩展点，不实现项目总结 Skill，不启动 HTTP 服务。

## 2. 最终目录结构

```text
C:\Code\rust\continuity-widget\
├─ Cargo.toml                         # continuity_core 与 continuity-cli
├─ src\
│  ├─ lib.rs                         # 对外导出共享核心
│  ├─ models.rs                      # Project/Item/DTO/枚举
│  ├─ database.rs                    # 连接、事务、revision、备份
│  ├─ migration.rs                   # Schema 迁移与一次性 Markdown 导入
│  ├─ scoring.rs                     # 0–100 活跃度计算
│  ├─ service.rs                     # 唯一业务入口
│  ├─ snapshot.rs                    # 只读 Markdown 快照
│  └─ bin\continuity-cli.rs          # JSON CLI
├─ src-tauri\
│  ├─ Cargo.toml                     # path 依赖 continuity_core
│  ├─ tauri.conf.json                # main 窗口；editor/preview 运行时创建
│  ├─ capabilities\default.json
│  └─ src\
│     ├─ lib.rs                      # 窗口、托盘、Tauri commands
│     └─ main.rs
├─ ui\
│  ├─ index.html
│  └─ src\
│     ├─ main.ts                     # 按 window label 路由入口
│     ├─ api.ts                      # invoke + revision polling
│     ├─ types.ts                    # 与 Rust DTO 对齐
│     ├─ icons.ts                    # Lucide 图标注册
│     ├─ widget.ts                   # 主组件
│     ├─ editor.ts                   # 独立编辑器
│     ├─ preview.ts                  # 左/右伴随预览
│     └─ styles.css                  # 三窗口统一 token 与布局
├─ tests\
│  ├─ fixtures\legacy-*.md           # 一次性导入样例
│  └─ cli-contract.ps1               # CLI JSON 合同测试
└─ docs\plans\                       # 本计划
```

现有 `src/parser.rs`、`src/lifecycle.rs`、`src/continuity_store.rs`、`src/todo_store.rs` 和根 `src/main.rs` 属于旧 Markdown/Win32 运行时。它们只在迁移测试落稳前用于对照，最终从构建路径和源码中删除，不保留双写或回退开关。

## 3. SQLite 数据模型

### 3.1 表与约束

| 表 | 关键字段 | 约束与用途 |
|---|---|---|
| `meta` | `key`, `value` | 保存 `schema_version`、全局递增 `revision`、旧数据导入标记 |
| `projects` | `id`, `legacy_id`, `name`, `status`, `summary`, `project_path`, `threshold_override`, `created_at`, `updated_at`, `revision` | `id` 为 UUID；`status` 为 `candidate/active/paused/completed/archived`；规范化后的非空 `project_path` 唯一 |
| `items` | `id`, `project_id`, `fingerprint`, `title`, `details`, `kind`, `horizon`, `status`, `priority`, `activity_score`, `last_evidence_at`, `manual_override`, `pinned`, `auto_daily`, `sort_order`, `closed_at`, `revision` | `kind=question/action`；`horizon=daily/weekly/later`；`status=open/done/dismissed`；同项目精确 fingerprint 唯一 |
| `item_evidence` | `id`, `item_id`, `source_ref`, `summary`, `short_quote`, `occurred_at`, `fingerprint` | 只存结构化摘要、短引用和来源引用；不保存完整聊天；同事项 fingerprint 唯一 |
| `item_suggestions` | `id`, `item_id`, `batch_id`, `proposed_json`, `status`, `created_at` | 自动整理遇到人工锁定字段时写建议；`status=pending/applied/rejected` |
| `update_batches` | `id`, `kind`, `source_ref`, `before_json`, `created_at`, `reverted_at` | 记录一次自动批量更新，允许撤销最近一次未撤销批次 |
| `project_history` | `id`, `project_id`, `revision`, `change_kind`, `snapshot_json`, `created_at` | 每项目仅保留最近 50 次修改 |
| `settings` | `id=1`, `daily_threshold`, `now_limit`, `poll_seconds`, `always_on_top`, `auto_snap`, `autostart` | 全局阈值默认 30；现在做默认 3、范围 1–5；外部 revision 检查固定 1 秒 |
| `window_state` | `label`, `x`, `y`, `width`, `height`, `snap_edge`, `height_mode`, `monitor_name` | 记住主窗尺寸、显示器、停靠边和 `fit/full` 高度模式 |

数据库必须启用：

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 3000;
```

所有写操作必须放在 `IMMEDIATE` 事务中，同时完成：业务写入、项目历史、批次记录、历史裁剪和 `meta.revision + 1`。任何一步失败则整笔回滚。

### 3.2 活跃度计算

事项活跃度为确定性 0–100 分，每次证据或优先级变化时重算：

```text
activity_score = recency_score + frequency_score + priority_score

recency_score   = max(0, 50 - floor(days_since_last_evidence) * 2)
frequency_score = min(30, evidence_count_last_7_days * 6)
priority_score  = clamp(priority, 0, 20)
```

- 没有证据的事项 `recency_score = 0`。
- `last_evidence_at` 只由实质证据更新，界面编辑和格式变化不算证据。
- 项目活跃度取其所有 `open` 事项的最高分；无 open 事项时为 0。
- 每日确认使用项目 `threshold_override`，为空时使用全局 `daily_threshold=30`。
- 活跃度 0 的项目不能出现在每日确认，但仍可按状态出现在本周方向。

### 3.3 派生视图

“现在做”候选只取 `kind=action AND status=open`：

1. `pinned=true` 的事项优先，不受阈值和 horizon 限制。
2. 其余只取达到项目阈值的 `horizon=daily` 事项。
3. 依次按 `pinned DESC`、`priority DESC`、项目活跃度 `DESC`、`last_evidence_at DESC`、稳定 `item.id ASC` 排序。
4. 截取设置中的 1–5 项；完成一项后同一事务提交，刷新时自然补位。

“每日确认”项目：

- 仅包含至少一条满足阈值的 `horizon=daily AND status=open` 事项。
- 项目内同时展示 question/action，但标题和类型必须可辨认。
- 项目排序按符合条件事项最高分、数量、最近证据、稳定 UUID。

“本周方向”项目：

- 包含全部 `status IN (active, paused)` 项目。
- 主组件一项目一行；`paused` 使用弱化样式，不从列表消失。
- 行摘要优先取人工项目摘要，其次取最高优先级 weekly action，再取最高活跃 open item。

## 4. 自动整理写入规则

`continuity-cli.exe project upsert` 与 `item upsert` 是每日整理和未来 Skill 的唯一写入口。

- 新名字或 legacy id 无法匹配现有项目时，创建 `candidate`，不进入主组件。
- 精确 `fingerprint` 自动复用已有事项；语义近似只返回 `possibleDuplicates`，不自动合并。
- 新优先行动可写为 `daily`，并标记 `auto_daily=true`。
- 同一项目上轮 `auto_daily=true`、本轮未再次出现的 open 事项自动降为 `weekly`；若本轮明确给出 `later` 则降为 `later`。
- `manual_override=true`、`pinned=true` 或由用户手动设为 daily 的事项不参与自动降级。
- 自动输入不得把 open 事项伪造成 done，不得自动完成或归档项目。
- 人工修改字段后设置 `manual_override=true`；后续自动更新对冲突字段写入 `item_suggestions`，只可追加新 evidence。
- 项目 `completed/archived` 只能由编辑器操作或显式 CLI 命令触发；完成项目时保留 open 事项，重新激活后恢复可见。

批次撤销只撤销最近一次 `kind=automatic` 且 `reverted_at IS NULL` 的更新，使用 `before_json` 恢复项目、事项和建议；撤销本身产生新 revision 和历史记录。

## 5. CLI JSON 合同

CLI 不启动守护进程或 HTTP 端口。所有命令支持 `--input <json文件>` 或 `--input -` 从 stdin 读取，stdout 只输出 JSON，诊断信息写 stderr。

统一响应：

```json
{
  "ok": true,
  "revision": 42,
  "data": {},
  "warnings": []
}
```

失败响应仍为 JSON，并使用非零退出码：

```json
{
  "ok": false,
  "error": {
    "code": "revision_conflict",
    "message": "数据已更新，请刷新后重试",
    "details": { "expected": 41, "actual": 42 }
  }
}
```

本轮实现命令：

```text
continuity-cli.exe project upsert
continuity-cli.exe project confirm
continuity-cli.exe item upsert
continuity-cli.exe item complete
continuity-cli.exe project summarize
continuity-cli.exe snapshot export
continuity-cli.exe batch undo-last
continuity-cli.exe database status
```

`project summarize` 本轮只接收已经由外部整理出的结构化摘要和事项，不调用模型、不读取完整会话。

最小 `item upsert` 输入：

```json
{
  "project": { "id": "uuid-or-null", "legacyId": "stable-old-id", "name": "项目名" },
  "item": {
    "id": null,
    "fingerprint": "stable-fingerprint",
    "title": "确认 SQLite 导入结果",
    "details": "检查项目数、事项数和来源映射。",
    "kind": "action",
    "horizon": "daily",
    "priority": 18,
    "source": {
      "ref": "codex:session-id",
      "summary": "用户确认改造方案",
      "shortQuote": "直接全量改造",
      "occurredAt": "2026-07-26T12:00:00+08:00"
    }
  },
  "batch": { "id": "daily-2026-07-26", "kind": "automatic" }
}
```

## 6. 界面与窗口规格

### 6.1 视觉方向

定位是常驻桌面的安静工作工具，不做营销页、不堆卡片。视觉使用接近 Windows 工具面板的中性石墨色文字、雾白背景、冷灰分隔线，琥珀色只用于当前项目和优先行动，青绿色只用于完成反馈，红色只用于错误。

```text
背景       #F7F8FA
主文字     #20242A
次级文字   #6B7280
分隔线     #D9DEE7
当前琥珀   #C88718
完成青绿   #16846B
```

字体优先使用系统 `Segoe UI Variable Text`，数字与状态使用 `Segoe UI Variable Small`。卡片圆角不超过 6px；主界面只使用分区和细分隔线，不套娃卡片。签名元素是每日确认项目名下方的细琥珀游标，它同时承担当前项目和切换进度提示。

### 6.2 主组件

默认宽度 450px，可调 420–520px。移除固定 `maxHeight: 860`，最大高度实时取当前显示器工作区高度减去上下各 8px。

```text
┌ 今日行动                 刷新  设置 ┐
│ 现在做                                │
│ □ 最高优先行动                        │
│ □ 第二行动                            │
│ □ 第三行动                            │
│ ＋ 添加到当前项目                     │
├───────────────────────────────────────┤
│ 每日确认                ‹ 1/2 ›       │
│ 项目甲   项目乙   项目丙               │
│ ─────                                 │
│ 待确认 / 下一步详细内容                │
├───────────────────────────────────────┤
│ 本周方向                    1/3  ‹ ›   │
│ 项目甲  一行提纲                      │
│ 项目乙  一行提纲（暂停）              │
│ 项目丙  一行提纲                      │
└───────────────────────────────────────┘
```

- 三个分区始终存在，不因内容为空而整体消失；空态只占一行。
- 高度为 `full` 时，先给三分区各自完整的目标容量，再计算分页。
- 现在做严格按设置显示 1–5 项，不滚动。
- 每日确认顶部一次最多显示 3 个项目小标题，左右箭头翻项目组；当前项目有琥珀下划线；键盘左右键切换。
- 每日确认项目内事项若超出可用高度，在内容区使用上一页/下一页，不产生滚动条。
- 本周方向根据可用高度计算每页行数，页码稳定；悬浮或键盘聚焦项目行时打开伴随预览。
- 右键菜单包含：打开编辑器、适应内容、充满高度、立即刷新、始终置顶、自动贴边、开机启动、现在做数量、退出。
- `适应内容` 使用当前页所需高度并受工作区上限约束；`充满高度` 使用工作区最大高度。
- 拖动结束后在 32 物理像素阈值内自动吸附，保存显示器和边；重启时恢复并校正到当前工作区。

### 6.3 伴随预览

- 独立无边框窗口，默认出现在主组件左侧，保留 8px 间隙；左侧空间不足时改到右侧。
- 预览窗口不覆盖主组件，显示位置必须 clamp 到当前显示器工作区。
- 展示项目名、状态和摘要，并按 `今日 / 本周 / 稍后` 分组，每组再区分 `待确认 / 下一步`。
- 主组件指针进入项目行 180ms 后打开；从主窗移动到预览窗时保持；离开两窗 250ms 后关闭。
- 内容超量时分页，不出现滚动条。

### 6.4 独立编辑器

默认 960×720，可调整大小，普通任务栏窗口，不强制始终置顶。

```text
┌ 项目 / 候选 ┬ 事项列表与编辑             ┬ 项目详情             ┐
│ 搜索        │ 标题、详情                  │ 状态 / 项目路径       │
│ 活跃        │ 待确认 | 下一步             │ 打开文件夹            │
│ 暂停        │ 每日 | 本周 | 稍后           │ 活跃度 67 = 42+12+13  │
│ 候选        │ 优先级 / pinned / 状态       │ 阈值覆盖              │
│ 已完成      │ 来源 / 相似项建议            │ 最近 50 次修改         │
└─────────────┴─────────────────────────────┴──────────────────────┘
```

- 左侧可确认候选项目；确认默认进入 `active`。
- 中间可新增、编辑、排序事项，并立即切换 `kind`、`horizon`、`status`、`pinned`。
- 文本输入停顿 500ms 或失焦自动保存；分类、状态、排序和开关立即保存。
- 每次保存携带 `expected_revision`；冲突时不静默覆盖，显示“数据已更新”，提供刷新和保留当前草稿两种动作。
- 右侧可编辑项目摘要、状态、`project_path`、项目阈值覆盖，并展示分数拆解和来源。
- `project_path` 保存前执行绝对路径、目录存在和唯一绑定校验。
- “打开项目目录”只调用 Windows 文件资源管理器：`explorer.exe <规范化绝对路径>`。
- 历史面板最多 50 条；done/dismissed 默认隐藏，可筛选查看，超过 90 天由维护任务清理。

## 7. 实施任务

项目当前不是 Git 仓库，因此计划不包含虚假的 commit 步骤。每个任务完成后以测试和构建结果作为检查点；若后续初始化 Git，再按任务边界使用中文 commit message。

### Task 1：建立共享核心与数据库骨架

**文件：**

- 修改：`Cargo.toml`
- 新建：`src/lib.rs`
- 新建：`src/models.rs`
- 新建：`src/database.rs`
- 新建：`src/migration.rs`
- 测试：以上模块内 `#[cfg(test)]`

**步骤：**

1. 添加 `rusqlite = { version = "0.40.1", features = ["bundled"] }`、`serde`、`serde_json`、`uuid`、`tempfile`（dev）。
2. 先写临时目录数据库测试：首次打开建表、重复打开幂等、外键开启、WAL 开启、revision 从 0 开始。
3. 运行 `rtk cargo test database`，确认测试先失败。
4. 实现路径解析、连接 pragma、v1 schema 和事务辅助方法。
5. 实现 `with_write_transaction`，保证每笔写事务只增加一次 revision。
6. 运行 `rtk cargo test database migration`，预期全部通过。

### Task 2：实现模型、活跃度与派生视图

**文件：**

- 修改：`src/models.rs`
- 新建：`src/scoring.rs`
- 新建：`src/service.rs`

**步骤：**

1. 为 Project/Item 枚举、阈值回退、活跃度边界写单元测试。
2. 为“0 活跃项目不进每日确认”“paused 仍进本周”“pinned 优先”“完成后补位”写查询测试。
3. 运行定向测试确认失败。
4. 实现分数计算和 SQL 查询，所有排序末尾使用稳定 UUID。
5. 实现 `DashboardView`、`EditorView`、`ProjectPreview` DTO。
6. 运行 `rtk cargo test scoring service`，预期全部通过。

### Task 3：实现人工优先、历史、裁剪和撤销

**文件：**

- 修改：`src/database.rs`
- 修改：`src/service.rs`
- 修改：`src/models.rs`

**步骤：**

1. 写人工字段不被自动更新覆盖的测试。
2. 写项目历史第 51 次修改后只保留 50 条的测试。
3. 写 done/dismissed 超过 90 天清理、open 永不被清理的测试。
4. 写自动批次撤销后状态恢复、revision 继续递增的测试。
5. 实现 suggestions、history、maintenance 和 undo。
6. 运行 `rtk cargo test history maintenance undo`。

### Task 4：实现一次性 Markdown 导入和数据库备份

**文件：**

- 修改：`src/migration.rs`
- 保留至本任务结束：`src/parser.rs`
- 新建：`tests/fixtures/legacy-todo.md`
- 新建：`tests/fixtures/legacy-continuity.md`

**步骤：**

1. 固化当前 Markdown 的项目合并、Todo、full/brief、来源和候选样例。
2. 写“同一 legacy project id 只生成一个 Project”的导入测试。
3. 写“重复执行导入不重复创建”的幂等测试。
4. 导入映射：full/brief 不再成为状态；根据所在周期映射 horizon，根据标题映射 kind，并将原 marker 保存为 source ref。
5. 数据库不存在时创建迁移前备份目录，导入成功后写 `legacy_imported_at`。
6. 每次每日整理前使用 SQLite backup API 生成时间戳备份，只保留最近 7 份。
7. 运行所有 migration/backup 测试。

### Task 5：实现 JSON CLI 与快照导出

**文件：**

- 新建：`src/bin/continuity-cli.rs`
- 新建：`src/snapshot.rs`
- 新建：`tests/cli-contract.ps1`
- 修改：`Cargo.toml`

**步骤：**

1. 为成功、验证失败、revision 冲突和不存在实体写 CLI 合同测试。
2. 实现统一 JSON envelope、stdin/文件输入和退出码。
3. 实现计划第 5 节列出的命令。
4. 实现稳定排序、UTF-8、幂等的 `output/continuity.md` 快照。
5. 快照包含生成 revision、项目状态、今日/本周/稍后、question/action、来源引用，正文明确标注“只读快照”。
6. 重复导出相同 revision 时不重写文件。
7. 运行 `pwsh -File '.\tests\cli-contract.ps1'` 和 `rtk cargo test snapshot`。

### Task 6：将 Tauri 后端切换到共享 service

**文件：**

- 修改：`src-tauri/Cargo.toml`
- 重写：`src-tauri/src/lib.rs`
- 修改：`src-tauri/src/main.rs`
- 修改：`src-tauri/capabilities/default.json`

**步骤：**

1. 移除四个 `#[path = "../../src/..."]` Markdown 模块引用。
2. 添加根 crate path dependency，AppState 保存共享 service，不保存 Vault 路径。
3. 暴露 dashboard、editor、project/item mutation、settings、history、undo commands。
4. 每次同进程写入后 emit `continuity://revision`。
5. 实现每秒 revision polling command，只在 revision 变化时返回新数据。
6. 用参数数组而非命令字符串调用 `explorer.exe`，只接受数据库中已校验的 project_path。
7. 为 Tauri command 参数验证和窗口边界计算写 Rust 测试。

### Task 7：重建主组件

**文件：**

- 重写：`ui/src/main.ts`
- 新建：`ui/src/api.ts`
- 新建：`ui/src/types.ts`
- 新建：`ui/src/icons.ts`
- 新建：`ui/src/widget.ts`
- 重写：`ui/src/styles.css`
- 修改：`ui/index.html`

**步骤：**

1. 先定义与 Rust 完全一致的 DTO 类型和无数据/错误/冲突状态。
2. 实现 window label 路由，main 只挂载 widget。
3. 实现现在做勾选、快速添加和自动补位。
4. 实现每日项目三标签分页、事项分页、箭头键切换和焦点样式。
5. 实现本周一项目一行、项目分页、paused 弱化和预览触发。
6. 用 `ResizeObserver` 计算各分区容量，设置显式行高和页数，CSS 全局禁止主窗滚动。
7. 实现右键菜单命令、fit/full 高度模式和 revision 事件刷新。
8. 运行 `rtk npm run build`，确保 TypeScript 无错误。

### Task 8：实现独立编辑器

**文件：**

- 新建：`ui/src/editor.ts`
- 修改：`ui/src/main.ts`
- 修改：`ui/src/api.ts`
- 修改：`ui/src/styles.css`
- 修改：`src-tauri/src/lib.rs`

**步骤：**

1. 后端创建或聚焦唯一 `editor` 窗口，默认 960×720。
2. 实现项目分组、搜索、候选确认和状态切换。
3. 实现事项新增、排序、kind/horizon/status/pinned/priority 编辑。
4. 实现 500ms debounce 与 blur 保存；结构字段立即保存。
5. 实现 expected revision 冲突提示，不覆盖未提交草稿。
6. 实现 project_path 绑定、唯一约束错误和文件资源管理器打开。
7. 实现活跃度拆解、来源、建议版本和最近 50 次历史。
8. 运行前端构建与 Rust service 定向测试。

### Task 9：实现伴随预览、尺寸记忆和贴边增强

**文件：**

- 新建：`ui/src/preview.ts`
- 修改：`ui/src/widget.ts`
- 修改：`ui/src/styles.css`
- 修改：`src-tauri/src/lib.rs`
- 修改：`src-tauri/tauri.conf.json`

**步骤：**

1. 为左侧优先、右侧回退、工作区 clamp 写纯函数测试。
2. 创建唯一 `preview` 窗口并根据主窗和 monitor work area 定位。
3. 实现 180ms 打开和 250ms 跨窗关闭容错。
4. 实现预览分组与无滚动分页。
5. 移除 Tauri 固定 `maxHeight`，按当前 monitor 工作区动态限制。
6. 保存 main 的尺寸、位置、显示器、snap_edge、height_mode；启动时校正恢复。
7. 验证多显示器、任务栏位置变化和显示器断开后的边界回退。

### Task 10：移除旧运行时并同步 Vault/工具文档

**文件：**

- 删除：`src/parser.rs`
- 删除：`src/lifecycle.rs`
- 删除：`src/continuity_store.rs`
- 删除：`src/todo_store.rs`
- 删除或替换：`src/main.rs`
- 修改：`README.md`
- 修改：`THIRD_PARTY_NOTICES.md`
- 修改：`C:\Code\codex\dev-tools\continuity-widget.md`
- 修改：`C:\Code\codex\dev-tools\dev-tools.md`
- 修改：`C:\Code\notes\zxy\AGENTS.md`
- 修改：`C:\Code\notes\zxy\SCHEMA.md`
- 修改：`C:\Code\notes\zxy\templates\continuity-template.md`
- 删除：`C:\Code\notes\zxy\templates\todo-template.md`
- 删除：`C:\Code\notes\zxy\templates\todo-candidates-template.md`
- 删除：`C:\Code\notes\zxy\templates\todo-archive-template.md`

**步骤：**

1. 确认数据库导入计数、抽样内容和快照导出均正确后，移除旧模块构建引用与源码。
2. 将 `AGENTS.md` 的每日整理改为调用 CLI upsert/recompute/snapshot，不再编辑运行时 Todo Markdown。
3. 将 `SCHEMA.md` 的 `continuity.md` 定义改为只读快照，移除三类 todo Markdown schema 和旧生命周期 marker 规则。
4. 删除旧 Todo 模板；continuity 模板改为导出格式示例并标注不可人工编辑。
5. 更新 README、工具说明、稳定入口、数据库路径、CLI 示例和备份策略。
6. 更新第三方许可中的 rusqlite/SQLite/uuid 信息。
7. 运行 Vault changed-scope 校验，确认 Schema 注册表和模板同步。

### Task 11：全量测试、生产构建和运行

**文件：**

- 构建产物：`src-tauri/target/release/continuity-widget-tauri.exe`
- 稳定入口：`bin/continuity-widget.exe`
- CLI：`bin/continuity-cli.exe`

**步骤：**

1. 运行 `rtk cargo fmt --check`。
2. 运行 `rtk cargo test --all-targets`。
3. 运行 `rtk cargo clippy --all-targets -- -D warnings`。
4. 运行 `rtk npm run build`。
5. 运行 `rtk npm run tauri -- build --no-bundle`，确保启用 `custom-protocol`。
6. 将两个 release exe 更新到 `bin`，再次执行 CLI `database status` 和 `snapshot export`。
7. 运行 Vault Manager changed-scope 校验，要求 0 error；现有无关工作区改动不回滚。
8. 停止旧 `continuity-widget.exe` 进程，启动新的稳定入口。
9. 不使用 Computer Use；通过进程路径、窗口标题、CLI revision、数据库查询、构建日志和前端自动化测试完成验收。
10. 最终把主组件实际运行给用户看，并报告 exe、数据库、快照和验证结果路径。

## 8. 验收标准

### 数据正确性

- 数据库不存在时能一次性导入当前项目和事项；重复启动不重复导入。
- 同类任务通过 project UUID + item fingerprint 聚合，不再生成多个同级项目。
- 候选项目不进入主组件；确认后立即进入 active。
- 人工修改不会被下一次自动整理覆盖。
- 每项目历史始终不超过 50 条；done/dismissed 超过 90 天可清理；open 永不被维护任务删除。
- 最近一次自动批量更新可撤销，撤销后 revision 继续递增。
- 数据库备份最多 7 份。

### 主组件

- 现在做默认恰好最多 3 项，配置范围只能是 1–5。
- 勾选完成后自动补入下一项；快速添加默认绑定当前每日确认项目，无当前项目时进入系统“临时收件箱”并 pinned。
- 每日确认最多显示 3 个项目小标题，左右箭头和键盘均可切换。
- 每日确认不显示 0 活跃项目，不混入 weekly/later。
- 本周方向覆盖全部 active/paused 项目，同一项目只占一行。
- 最大高度下三个板块均完整可用；任何主组件内容量下都没有滚动条，超量只分页。

### 编辑器与预览

- 编辑器可以随时调整每日/本周/稍后和待确认/下一步，可确认候选，可绑定并打开项目目录。
- 文本 500ms 或失焦保存，类别立即保存；冲突不静默覆盖。
- 本周项目预览在主组件左侧独立展开，左侧空间不足才到右侧，绝不遮挡主组件。
- 预览按今日/本周/稍后和待确认/下一步展示，超量分页。

### 工程与运行

- Rust test、clippy、TypeScript build、Tauri production build 全部通过。
- 正式版不访问 `127.0.0.1:1420`。
- `bin/continuity-widget.exe` 和 `bin/continuity-cli.exe` 可直接运行。
- 外部 CLI 写入后主组件在 1 秒内刷新；同进程编辑立即刷新。
- 不开放本地 HTTP 端口，不保存完整聊天，不使用 Computer Use 验收。

## 9. 明确不做

- 不实现项目总结 Skill，只实现它未来需要调用的 CLI/API。
- 不做 Markdown 反向同步、实时双写或旧存储回退。
- 不开放 HTTP/WebSocket 服务。
- 不做语义自动合并，只返回近似候选。
- 不自动完成、归档或删除项目。
- 不保存完整对话正文。
- 不提供 VS Code 打开方式，只使用 Windows 文件资源管理器。

## 10. 实施前检查点

审核通过后，按 Task 1 到 Task 11 顺序执行。Task 4 的一次性导入通过后才允许删除旧 Markdown 运行时代码；Task 10 修改 Vault 前必须再次执行 `rtk git status --short` 并只处理本计划列出的文件。由于用户已明确“不需要可回滚”，不额外保留旧运行时分支或兼容开关，但仍保留已确认的最近 7 份 SQLite 日常备份。
