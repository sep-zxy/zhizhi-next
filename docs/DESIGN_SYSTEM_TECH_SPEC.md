# 知枝（Zhizhi）绿色玻璃设计系统与 Design Token 技术文档

> **版本**：v0.3（Green Glass · 对齐新版 6 页原型）
> **Figma 规范源**：[知枝 Zhizhi · 设计规范](https://www.figma.com/design/rD3fYZVSpzQV52atQPNhky/)
> **参考方法论**：腾讯 TDesign 两层 Token 架构（基础原语层 + 语义角色层）与组件状态矩阵规范
> **适用范围**：`apps/learning-web/` Web 界面与桌面端界面研发、UI 设计协同及前端验收

---

## 1. 体系概览与设计原则

### 1.1 单一事实源（Single Source of Truth）
- **设计源**：Figma 文件内的 5 个正式页面：
  - `00 · 设计规范总览`
  - `01 · 基础规范`
  - `02 · 组件规范`（17 个组件族 + 3 个图标，共 69 个定义）
  - `03 · 布局与交互规范`
  - `04 · 绿色版应用示例`（全部 6 个核心页面的 `1280 × 900` 可编辑设计稿）
- **变量分层原则**：
  1. **`Zhizhi / Primitives`（底层原语，41 个）**：仅存放无业务语义的色阶和尺寸数值，在 Figma 中已设为**隐藏发布**（`hiddenFromPublishing = true`），业务组件和页面禁止直接消费。
  2. **`Zhizhi / Semantic`（语义层，49 个）**：100% 通过引用（Alias）指向 `Primitives`，按**用途 + 角色 + 状态**命名，统一输出 `--zz-*` 全小写短横线 CSS 变量，供设计与前端直接消费。
  3. **组件层**：当前知枝为单一产品，**不强制引入第三层 Component Token**；常规组件直接消费 `Semantic` 变量，保持最低维护成本。

### 1.2 核心视觉特征
- **柔和绿白玻璃底**：主面板使用 3 段浅绿渐变，卡片使用 `76%` 白色 + `20px` 背景模糊 + 顶部 `1px 88%` 白内高光；正文与图标始终保持实色墨色，不随玻璃透明度变淡。
- **炭黑承载视觉重心**：当前学习任务、AI 当前上下文卡、代码块、主操作按钮与选中导航使用炭黑底配反白文字及嫩绿进度条。
- **严格防呆约束**：嫩绿（`brand/accent`）仅用于进度、图表与按钮底面（配墨色文字），**禁止作为浅底小字号正文颜色**（Figma 中已关闭其 `TEXT_FILL` 作用域）。

---

## 2. Design Token 完整清单

### 2.1 语义层 Token（`Zhizhi / Semantic` · 前端唯一消费层）

#### A. 背景与面层（Background / Surface）

| Figma 变量名 | CSS 变量 | 引用原语 | Light 模式值 | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| `bg/page` | `--zz-bg-color-page` | `palette/neutral-50` | `#F0F2EC` | 规范页、步骤条与浅灰证据块底色 |
| `bg/container` | `--zz-bg-color-container` | `palette/white` | `#FFFFFF` | 纯白控件、内部内容单元、输入框与高亮行 |
| `bg/container-hover` | `--zz-bg-color-container-hover` | `palette/neutral-100` | `#E9EDE6` | 浅色控件、导航项与 Tab Hover 态背景 |
| `bg/glass` | `--zz-bg-color-glass` | `palette/white-76` | `rgba(255, 255, 255, 0.76)` | 常规模块卡片 76% 白色半透明面层 |
| `bg/emphasis` | `--zz-bg-color-emphasis` | `palette/neutral-850` | `#2B2E2A` | 炭黑重点卡片、主按钮、选中导航与代码块底 |
| `bg/emphasis-hover` | `--zz-bg-color-emphasis-hover` | `palette/neutral-800` | `#3B3F3A` | 炭黑主按钮与深色控件 Hover 态背景 |
| `bg/emphasis-inactive` | `--zz-bg-color-emphasis-inactive` | `palette/neutral-700` | `#464B43` | 进度条轨道与深色块未活跃节点 |
| `bg/disabled` | `--zz-bg-color-disabled` | `palette/neutral-100` | `#E9EDE6` | 禁用态输入框与按钮背景 |
| `bg/showcase-canvas` | `--zz-bg-color-showcase` | `palette/neutral-400` | `#A7ADB5` | 外部预览展示底板（不用于产品正文区） |

#### B. 文字与图标（Text & Icon）

| Figma 变量名 | CSS 变量 | 引用原语 | Light 模式值 | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| `text/primary` | `--zz-text-color-primary` | `palette/neutral-900` | `#262B27` | 标题、正文与主图标 |
| `text/secondary` | `--zz-text-color-secondary` | `palette/neutral-600` | `#59645B` | 辅助说明、占位符与次级图标 |
| `text/disabled` | `--zz-text-color-disabled` | `palette/neutral-500` | `#7D887F` | 禁用态文字与图标 |
| `text/anti` | `--zz-text-color-anti` | `palette/neutral-25` | `#F7F9F3` | 炭黑深底上的主要反白文字与图标 |
| `text/anti-secondary` | `--zz-text-color-anti-secondary` | `palette/neutral-300` | `#CCD2C7` | 炭黑深底上的辅助说明文字 |

#### C. 品牌与强调（Brand & Focus）

| Figma 变量名 | CSS 变量 | 引用原语 | Light 模式值 | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| `brand/primary` | `--zz-brand-color` | `palette/green-800` | `#276342` | 深绿品牌主色：眉标、文字按钮与链接 |
| `brand/focus` | `--zz-brand-color-focus` | `palette/green-800` | `#276342` | 键盘聚焦 `2px` 轮廓描边色 |
| `brand/accent` | `--zz-brand-color-accent` | `palette/green-400` | `#B5E875` | 嫩绿强调色：引导按钮、进度条、当日高亮（禁作浅底小字） |
| `brand/accent-hover` | `--zz-brand-color-accent-hover` | `palette/green-300` | `#C5EE8E` | 嫩绿强调按钮 Hover 态 |
| `brand/accent-active` | `--zz-brand-color-accent-active` | `palette/green-500` | `#9ED95A` | 嫩绿强调按钮 Active 按压态 |
| `brand/light` | `--zz-brand-color-light` | `palette/green-100` | `#E2F1D4` | 浅绿标签底、AI 头像底与用户消息气泡底 |
| `brand/glow` | `--zz-brand-color-glow` | `palette/green-200` | `#D4EDB8` | 装饰性浅绿光感层次 |

#### D. 边框与玻璃高光描边（Border）

| Figma 变量名 | CSS 变量 | 引用原语 | Light 模式值 | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| `border/default` | `--zz-border-color-default` | `palette/neutral-200` | `#DDE3D9` | 浅底分割线与默认控件描边 |
| `border/glass` | `--zz-border-color-glass` | `palette/white-42` | `rgba(255, 255, 255, 0.42)` | 常规玻璃卡片边缘微描边 |
| `border/nav-glass` | `--zz-border-color-nav-glass` | `palette/white-72` | `rgba(255, 255, 255, 0.72)` | 顶部高光玻璃主导航外描边 |

#### E. 功能反馈色（Feedback）

| Figma 变量名 | CSS 变量 | 引用原语 | Light 模式值 | 用途说明 |
| :--- | :--- | :--- | :--- | :--- |
| `success/default` | `--zz-success-color` | `palette/green-700` | `#2D6E49` | 成功反馈文字与图标 |
| `success/light` | `--zz-success-color-light` | `palette/green-100` | `#E2F1D4` | 成功反馈浅底 |
| `warning/default` | `--zz-warning-color` | `palette/amber-700` | `#8A5A14` | 警示提醒文字与边框 |
| `warning/light` | `--zz-warning-color-light` | `palette/amber-100` | `#FBF1DC` | 警示提醒浅底 |
| `info/default` | `--zz-info-color` | `palette/slate-700` | `#2E5963` | 信息提示文字与图标 |
| `info/light` | `--zz-info-color-light` | `palette/slate-100` | `#E6F1F3` | 信息提示浅底 |
| `error/default` | `--zz-error-color` | `palette/red-700` | `#A23D3D` | 错误文字、图标与错误边框 |
| `error/light` | `--zz-error-color-light` | `palette/red-100` | `#F9E7E5` | 错误提示与「待补料」浅底 |

#### F. 间距、圆角与控件高度（Spacing / Radius / Component Size）

| 分类 | Figma 变量名 | CSS 变量 | 引用原语 | 数值 | 典型用途 |
| :--- | :--- | :--- | :--- | :---: | :--- |
| 间距 | `spacing/xxs` | `--zz-spacing-xxs` | `size/1` | `4px` | 极小间距、步骤条内边距 |
| 间距 | `spacing/xs` | `--zz-spacing-xs` | `size/2` | `8px` | 图标与文字间距、按钮组间距 |
| 间距 | `spacing/sm` | `--zz-spacing-sm` | `size/3` | `12px` | 主工作台卡片沟槽（gutter） |
| 间距 | `spacing/md` | `--zz-spacing-md` | `size/4` | `16px` | 内部单元内边距、标准段落间距 |
| 间距 | `spacing/lg` | `--zz-spacing-lg` | `size/5` | `20px` | 按钮水平内边距、紧凑卡片上下边距 |
| 间距 | `spacing/xl` | `--zz-spacing-xl` | `size/6` | `24px` | 主面板内边距（inset）、常规卡片内边距 |
| 间距 | `spacing/2xl` | `--zz-spacing-2xl` | `size/8` | `32px` | 大区块留白 |
| 圆角 | `radius/sm` | `--zz-radius-sm` | `size/4` | `16px` | 内部内容单元、证据块、代码块、追问气泡 |
| 圆角 | `radius/compose` | `--zz-radius-compose` | `size/6` | `24px` | 右侧 AI 底部追问输入框 |
| 圆角 | `radius/md` | `--zz-radius-md` | `size/8` | `32px` | 常规模块玻璃卡片、弹窗 Dialog |
| 圆角 | `radius/lg` | `--zz-radius-lg` | `size/12` | `48px` | 产品主面板容器（App Shell） |
| 圆角 | `radius/round` | `--zz-radius-round` | `size/full` | `999px` | 胶囊按钮、导航条、标签、输入框、圆形按钮 |
| 高度 | `comp/size-s` | `--zz-comp-size-s` | `size/8` | `32px` | 紧凑控件、AI 头像与发送圆按钮 |
| 高度 | `comp/size-stepper` | `--zz-comp-size-stepper` | `size/9` | `36px` | 知返学习 4 步进度条单项高度 |
| 高度 | `comp/size-m` | `--zz-comp-size-m` | `size/10` | `40px` | 导航项、页内 Tab、圆形图标按钮 |
| 高度 | `comp/size-l` | `--zz-comp-size-l` | `size/11` | `44px` | 主按钮、胶囊输入框、步骤条外框 |
| 高度 | `comp/size-xl` | `--zz-comp-size-xl` | `size/12` | `48px` | 顶部高光主导航容器 |

---

### 2.2 字体字阶（Typography Styles）

字体栈约定：
- UI 字体：`--zz-font-ui: "Inter", "Noto Sans SC", "Microsoft YaHei", sans-serif;`
- 等宽代码：`--zz-font-mono: "JetBrains Mono", Consolas, monospace;`

| Figma 样式名 | 建议 CSS Token | 字体 / 字重 | 字号 / 行高 | 特殊排版 | 用途 |
| :--- | :--- | :--- | :---: | :--- | :--- |
| `Zhizhi / Display` | `--zz-font-display` | Noto Sans SC Light (300) | `38px / 52px` | `letter-spacing: -0.025em` | 页面主标语 |
| `Zhizhi / H1` | `--zz-font-h1` | Noto Sans SC Regular (400) | `28px / 40px` | — | 详情页主标题、Wiki 标题 |
| `Zhizhi / H2` | `--zz-font-h2` | Noto Sans SC Regular (400) | `20px / 30px` | — | 卡片标题、二级小节标题 |
| `Zhizhi / Body` | `--zz-font-body` | Noto Sans SC Regular (400) | `15px / 24px` | — | 正文、卡片列表标题 |
| `Zhizhi / Small` | `--zz-font-small` | Noto Sans SC Regular (400) | `13px / 20px` | — | 按钮文案、输入内容、辅助说明 |
| `Zhizhi / Caption` | `--zz-font-caption` | Noto Sans SC Regular (400) | `11px / 16px` | — | 仅限短刻度、元数据与标签（不用于长说明） |
| `Zhizhi / Eyebrow` | `--zz-font-eyebrow` | Inter Regular (400) | `11px / 16px` | `letter-spacing: 0.12em; uppercase` | 页面眉标与区块引导标（深绿） |
| `Zhizhi / Number` | `--zz-font-number` | Inter Light (300) | `48px / 56px` | `tabular-nums` | 首页核心统计大数字 |
| `Zhizhi / Metric` | `--zz-font-metric` | Inter Light (300) | `30px / 38px` | `tabular-nums` | 窄屏统计数字 |
| `Zhizhi / Code` | `--zz-font-code` | JetBrains Mono Regular (400) | `12px / 20px` | — | 代码块、卡片编号、提交哈希 |

---

### 2.3 渐变、玻璃与投影（Paint & Effect Styles）

```css
:root {
  /* 主面板 3 段柔和绿渐变 */
  --zz-bg-app-gradient: linear-gradient(
    168.29deg,
    #e8ede8 14.29%,
    #f5f7eb 52.14%,
    #cfebab 85.71%
  );

  /* 顶部主导航 4 段竖向高光玻璃渐变 */
  --zz-bg-nav-glass: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.02) 0%,
    rgba(255, 255, 255, 0.12) 38%,
    rgba(255, 255, 255, 0.40) 72%,
    rgba(255, 255, 255, 0.82) 100%
  );

  /* 投影与内高光 */
  --zz-shadow-panel: 0 36px 56px -20px rgba(20, 28, 20, 0.22);
  --zz-shadow-nav-glass: 0 6px 20px -6px rgba(38, 43, 39, 0.14),
    inset 0 1px 0 rgba(255, 255, 255, 0.5);
  --zz-shadow-card-glass: inset 0 1px 0 rgba(255, 255, 255, 0.88),
    inset 0 0 0 1px rgba(255, 255, 255, 0.42);
  --zz-blur-glass: blur(20px);
}

/* 降级回退：不支持 backdrop-filter 时提高白底不透明度，保持墨色正文可读性 */
@supports not ((backdrop-filter: blur(20px)) or (-webkit-backdrop-filter: blur(20px))) {
  .zz-card-glass {
    background: rgba(255, 255, 255, 0.94);
  }
  .zz-nav-glass {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.64), rgba(255, 255, 255, 0.94));
  }
}
```

---

## 3. 布局骨架、高度统一与响应式规范

### 3.1 桌面基准与硬性对齐底线
- **统一桌面主面板尺寸**：`1280 × 900`（所有 6 个核心页面设计稿均锁定 `1280 × 900` 主面板 + `1440 × 1008` 外部预览框）。
- **核心布局变量**：
  - `--zz-layout-max: 1280px`
  - `--zz-layout-inset: 24px`（主面板四边统一内边距）
  - `--zz-layout-gutter: 12px`（所有模块卡片沟槽）
  - `--zz-layout-rail: 288px`（右侧常驻可折叠 AI 助手栏宽度）
  - `--zz-layout-schedule: 300px`（首页左侧「今日学习」列宽度）
  - `--zz-layout-nav-height: 48px`
- **4 条强制验收底线**：
  1. **左右底边 0px 齐平**：左侧主工作区（`1fr`）与右侧 `288px` AI 助手栏通过 `align-items: stretch` 等高拉伸，底边误差为 `0px`。
  2. **同排卡片等高齐底**：首页「今日学习」与「下一步建议」齐底；内页左右分栏卡片齐底；设置页左、中、右三卡齐底。
  3. **AI 输入框固定贴底**：AI 栏内聊天消息区弹性占位，底部 `24px` 圆角追问输入框通过 `margin-top: auto` 始终锚定在底部。
  4. **统一底部留白 24px**：主面板底部不留多余空绿边，内容区在 `900px` 视口内无文字裁切；多卡片流页面（如知识雷达）采用前 2 卡完整展开 + 后续折叠摘要或列表内滚动，避免破坏 `900px` 整体基线。

### 3.2 六大页面布局模式

| 页面 | 顶栏激活项 | 页面抬头 | 主工作区结构（左侧 `1fr`） |
| :--- | :--- | :--- | :--- |
| **首页（Home）** | `首页` | 眉标 + Display + 3 指标（仅首页显示） | 上：`300px` 今日学习 + 弹性中列（最近知识 / 下一步建议）；下：通栏「需要处理」条 |
| **材料箱（Materials）** | `材料箱` | 眉标 + Display | 页内 Tab（内容收件 / 项目与提交）+ 状态筛选 + `0.85fr / 1.35fr` 列表/详情分栏 |
| **知返学习（Learning）** | `知返学习` | 眉标 + Display | 页内 Tab（学习候选 / 学习任务 / 学习记录）+ `0.85fr / 1.35fr` 任务/四步学习分栏 |
| **知识库（Knowledge）** | `知识库` | 眉标 + Display | 页内 Tab（Wiki 浏览 / 知识地图 / 修改提案）+ 搜索框 + `0.85fr / 1.35fr` 目录/正文分栏 |
| **知识雷达（Radar）** | `知识雷达` | 眉标 + Display | 页内 Tab（推荐给你 / 已延后）+ 纵向推荐卡片流 |
| **设置（Settings）** | 右侧 `设置` 炭黑态 | 眉标 + Display | 等宽双栏卡片（账号与会话 / 服务与模型连接） |

### 3.3 响应式断点规范

| 断点区间 | 关键布局变化 |
| :--- | :--- |
| `≥ 1280px` | 完整桌面双区布局：主面板居中 `1280px`，左侧主工作区 + 右侧 `288px` AI 栏并排 |
| `1100px – 1279px` | 右侧 AI 助手栏下移至主工作区下方；Display 字阶收敛至 `28 / 40px` |
| `900px – 1099px` | 顶栏改为两行（上行：品牌 + 右侧操作组；下行：主导航居中通栏） |
| `600px – 899px` | 主面板内边距收敛至 `20px`、圆角 `32px`；所有分栏与首页网格转为单列；主导航横向可滚动 |
| `421px – 599px` | 内页列表卡片转为单列；分析指标由 2 列转为 1 列 |
| `≤ 420px` | 主面板内边距 `16px`、圆角 `24px`；隐藏品牌英文副标与「提交材料」按钮文案仅保留 `+` 图标 |

---

## 4. 组件库与前端 Props 对照表

> 建议前端目录分层：通用基础组件置于 `apps/learning-web/src/components/ui/`，业务模式组件置于 `apps/learning-web/src/components/business/`。

### 4.1 通用基础组件（8 个组件族 + 3 个图标）

| Figma 组件名 | 变体数 | 前端 Props 映射约定 | 交互与使用规范 |
| :--- | :---: | :--- | :--- |
| `知枝 / PillButton` | 15 | `variant: 'primary' \| 'accent' \| 'ghost'`<br>`state: 'default' \| 'hover' \| 'focus' \| 'disabled' \| 'loading'`<br>`label: string` | 高 `44px`；同操作区仅保留 1 个 `primary(Dark)`；`accent(Green)` 必须配墨色字 |
| `知枝 / NavPill` | 4 | `state: 'default' \| 'hover' \| 'focus' \| 'selected'`<br>`label: string` | 高 `40px`；选中态带 `aria-current="page"`，Focus 为 `2px --zz-brand-color-focus` |
| `知枝 / SegmentTab` | 3 | `state: 'default' \| 'hover' \| 'selected'`<br>`label: string` | 高 `40px`；用于页内子视图切换，激活项使用纯白胶囊底 |
| `知枝 / TextButton` | 3 | `state: 'default' \| 'hover' \| 'disabled'`<br>`label: string` | 深绿无底文字动作，用于「历史」「打开 Wiki →」「丢弃」「延后」 |
| `知枝 / StatusTag` | 5 | `status: 'review' \| 'learning' \| 'learned' \| 'neutral' \| 'error'`<br>`label: string` | 高 `28px` 胶囊；分别对应浅绿、嫩绿、炭黑、浅灰（未连接）、浅红（待补料） |
| `知枝 / TextField` | 5 | `state: 'default' \| 'hover' \| 'focus' \| 'error' \| 'disabled'`<br>`leadingIcon?: IconName`、`value: string` | 高 `44px`；独立表单须配永久可见外部 `<label>`，Error 态关联修复说明 |
| `知枝 / RoundIconButton` | 8 | `tone: 'light' \| 'dark'`<br>`state: 'default' \| 'hover' \| 'focus' \| 'disabled'`<br>`icon: IconName` | `40 × 40px` 圆形入口，图标通过组件插槽 / `INSTANCE_SWAP` 替换 |
| `知枝 / Feedback` | 4 | `tone: 'info' \| 'success' \| 'attention' \| 'error'`<br>`title: string`、`message: string` | `Info / Success / Error` 分别消费对应语义色；`Attention` 用炭黑重点提示 |
| `知枝 / 图标 / arrow, search, plus` | 3 | 统一 `20 × 20px` 线性 SVG 组件 | 描边继承当前文字颜色，禁止使用旋转矩形拼凑图标 |

### 4.2 业务模式组件（9 个组件族）

| Figma 组件名 | 变体数 | 前端 Props 映射约定 | 业务职责 |
| :--- | :---: | :--- | :--- |
| `知枝 / Stepper` | 4 | `step: 'explain' \| 'quiz' \| 'analysis' \| 'understanding'` | 知返学习 4 步胶囊进度条（外框 `44px` / 单项 `36px`） |
| `知枝 / CodeBlock` | 1 | `code: string` | 炭黑底等宽代码展示块（`16px` 圆角、`JetBrains Mono 12/20`） |
| `知枝 / AiPromptChip` | 1 | `prompt: string` | 右侧 AI 栏快捷追问纯白气泡（`16px` 圆角） |
| `知枝 / KnowledgeRow` | 3 | `status: 'review' \| 'learning' \| 'learned'`、`title`、`subtitle`、`source` | 首页与搜索结果中的知识紧凑列表行 |
| `知枝 / KnowledgeCard` | 3 | `status: 'review' \| 'learning' \| 'learned'`、`title`、`summary`、`source` | 知识卡摘要展示单元 |
| `知枝 / ScheduleItem` | 2 | `state: 'active' \| 'upcoming'`、`title`、`time` | 今日学习计划任务卡（当前任务炭黑底 + 嫩绿进度） |
| `知枝 / EvidenceBlock` | 2 | `state: 'collapsed' \| 'expanded'`、`source`、`path`、`excerpt` | 代码来源证据块，保留项目、提交哈希、路径与行号 |
| `知枝 / Metric` | 1 | `value: string`、`label: string` | 首页核心数字指标（`Inter Light 48/56`） |
| `知枝 / ActivityDot` | 2 | `state: 'inactive' \| 'active'` | 活跃度圆点单元（配套文字图例与统计周期） |

---

## 5. 交互状态、弹窗与无障碍（A11y）规范

1. **统一交互反馈**：
   - **Focus**：所有可聚焦控件统一 `outline: 2px solid var(--zz-brand-color-focus); outline-offset: 3px;`。
   - **Disabled**：统一 `opacity: 0.4; cursor: not-allowed;`（输入框配 `var(--zz-bg-color-disabled)` 与 `var(--zz-text-color-disabled)`），并说明禁用原因。
   - **Loading**：显示进行中文案并阻止重复提交。
2. **弹窗与轻量提示**：
   - **Dialog**：`32px` 圆角、`--zz-bg-color-page` 底色、`24px` 内边距、最大宽 `560px`、背景暗遮罩 `rgba(30, 40, 30, 0.32)` + `blur(6px)`；关闭后焦点必须返回触发按钮。
   - **Toast**：底部居中炭黑胶囊（`--zz-bg-color-emphasis` + `--zz-text-color-anti`），`role="status"`，3 秒自动消失。
3. **辅助功能与动效降级**：
   - 当前导航必须带 `aria-current="page"`；右侧 AI 折叠按钮带 `aria-expanded` 与 `aria-controls="ai-panel"`。
   - 遵循 `@media (prefers-reduced-motion: reduce)` 关闭位移动画与过渡。

---

## 6. 工程化落地与现有仓库迁移指南

### 6.1 推荐 Token 工程流水线

```text
Figma Variables (Zhizhi / Primitives + Semantic)
   ↓  导出 / 同步
apps/learning-web/tokens/zhizhi.tokens.json                 （W3C DTCG 格式，唯一总账本）
   ↓  轻量构建脚本（apps/learning-web/scripts/build-tokens.mjs）
apps/learning-web/src/styles/zhizhi-tokens.css       （全部 --zz-* CSS 变量与玻璃降级规则）
apps/learning-web/src/styles/zhizhi-tokens.ts        （CSS 变量表达式与解析值常量）
apps/learning-web/src/styles/zhizhi-mantine-theme.ts （Mantine 主题适配）
   ↓  组件样式消费
apps/learning-web/src/components/**                  （禁止硬编码色值，仅引用 var(--zz-*)）
```

### 6.2 与现有 `apps/learning-web/src/growth/GrowthBase.css` 的平滑过渡策略
当前仓库中 [apps/learning-web/src/growth/GrowthBase.css](../apps/learning-web/src/growth/GrowthBase.css) 仍在使用既有变量（`--paper`、`--ink`、`--accent` 等），被 `apps/learning-web/src/` 下既有样式直接引用。为避免一次性改动影响存量页面，建议分两阶段迁移：

1. **阶段一（新旧并存，零破坏）**：
   - 新增独立文件 `apps/learning-web/src/styles/zhizhi-tokens.css`，完整声明本规范的所有 `--zz-*` 变量；
   - 在新开发的 6 大绿色玻璃页面与新组件中 100% 使用 `--zz-*`。
2. **阶段二（存量页面统一切换）**：
   - 将旧变量通过别名桥接到新 Token，例如：
     ```css
     :root[data-theme="green-glass"] {
       --paper: var(--zz-bg-color-page);
       --elevated: var(--zz-bg-color-container);
       --ink: var(--zz-text-color-primary);
       --muted: var(--zz-text-color-secondary);
       --hair: var(--zz-border-color-default);
       --accent: var(--zz-brand-color);
       --accent-soft: var(--zz-brand-color-light);
       --danger: var(--zz-error-color);
       --danger-soft: var(--zz-error-color-light);
     }
     ```
   - 保留 `ahadiff` 技术入口、Electron `appId` 与现有数据目录不变；所有构建与打包通过 GitHub Actions（`.github/workflows/ci.yml`）执行，禁止本地打包。

### 6.3 开箱即用的 CSS Token 基准代码（`zhizhi-tokens.css`）

```css
:root {
  color-scheme: light;

  /* 1. Primitives（基础色阶与数值，仅供 Semantic 引用） */
  --zz-white: #ffffff;
  --zz-white-76: rgba(255, 255, 255, 0.76);
  --zz-white-72: rgba(255, 255, 255, 0.72);
  --zz-white-42: rgba(255, 255, 255, 0.42);

  --zz-neutral-25: #f7f9f3;
  --zz-neutral-50: #f0f2ec;
  --zz-neutral-100: #e9ede6;
  --zz-neutral-200: #dde3d9;
  --zz-neutral-300: #ccd2c7;
  --zz-neutral-400: #a7adb5;
  --zz-neutral-500: #7d887f;
  --zz-neutral-600: #59645b;
  --zz-neutral-700: #464b43;
  --zz-neutral-800: #3b3f3a;
  --zz-neutral-850: #2b2e2a;
  --zz-neutral-900: #262b27;

  --zz-green-100: #e2f1d4;
  --zz-green-200: #d4edb8;
  --zz-green-300: #c5ee8e;
  --zz-green-400: #b5e875;
  --zz-green-500: #9ed95a;
  --zz-green-700: #2d6e49;
  --zz-green-800: #276342;

  --zz-red-100: #f9e7e5;
  --zz-red-700: #a23d3d;
  --zz-amber-100: #fbf1dc;
  --zz-amber-700: #8a5a14;
  --zz-slate-100: #e6f1f3;
  --zz-slate-700: #2e5963;

  --zz-size-1: 4px;
  --zz-size-2: 8px;
  --zz-size-3: 12px;
  --zz-size-4: 16px;
  --zz-size-5: 20px;
  --zz-size-6: 24px;
  --zz-size-8: 32px;
  --zz-size-9: 36px;
  --zz-size-10: 40px;
  --zz-size-11: 44px;
  --zz-size-12: 48px;
  --zz-size-full: 999px;

  /* 2. Semantic（业务与组件消费层） */
  --zz-bg-color-showcase: var(--zz-neutral-400);
  --zz-bg-color-page: var(--zz-neutral-50);
  --zz-bg-color-container: var(--zz-white);
  --zz-bg-color-container-hover: var(--zz-neutral-100);
  --zz-bg-color-glass: var(--zz-white-76);
  --zz-bg-color-emphasis: var(--zz-neutral-850);
  --zz-bg-color-emphasis-hover: var(--zz-neutral-800);
  --zz-bg-color-emphasis-inactive: var(--zz-neutral-700);
  --zz-bg-color-disabled: var(--zz-neutral-100);

  --zz-text-color-primary: var(--zz-neutral-900);
  --zz-text-color-secondary: var(--zz-neutral-600);
  --zz-text-color-disabled: var(--zz-neutral-500);
  --zz-text-color-anti: var(--zz-neutral-25);
  --zz-text-color-anti-secondary: var(--zz-neutral-300);

  --zz-brand-color: var(--zz-green-800);
  --zz-brand-color-focus: var(--zz-green-800);
  --zz-brand-color-accent: var(--zz-green-400);
  --zz-brand-color-accent-hover: var(--zz-green-300);
  --zz-brand-color-accent-active: var(--zz-green-500);
  --zz-brand-color-light: var(--zz-green-100);
  --zz-brand-color-glow: var(--zz-green-200);

  --zz-border-color-default: var(--zz-neutral-200);
  --zz-border-color-glass: var(--zz-white-42);
  --zz-border-color-nav-glass: var(--zz-white-72);

  --zz-success-color: var(--zz-green-700);
  --zz-success-color-light: var(--zz-green-100);
  --zz-warning-color: var(--zz-amber-700);
  --zz-warning-color-light: var(--zz-amber-100);
  --zz-info-color: var(--zz-slate-700);
  --zz-info-color-light: var(--zz-slate-100);
  --zz-error-color: var(--zz-red-700);
  --zz-error-color-light: var(--zz-red-100);

  --zz-spacing-xxs: var(--zz-size-1);
  --zz-spacing-xs: var(--zz-size-2);
  --zz-spacing-sm: var(--zz-size-3);
  --zz-spacing-md: var(--zz-size-4);
  --zz-spacing-lg: var(--zz-size-5);
  --zz-spacing-xl: var(--zz-size-6);
  --zz-spacing-2xl: var(--zz-size-8);

  --zz-radius-sm: var(--zz-size-4);
  --zz-radius-compose: var(--zz-size-6);
  --zz-radius-md: var(--zz-size-8);
  --zz-radius-lg: var(--zz-size-12);
  --zz-radius-round: var(--zz-size-full);

  --zz-comp-size-s: var(--zz-size-8);
  --zz-comp-size-stepper: var(--zz-size-9);
  --zz-comp-size-m: var(--zz-size-10);
  --zz-comp-size-l: var(--zz-size-11);
  --zz-comp-size-xl: var(--zz-size-12);

  /* 3. Layout & Typography & Surface Effects */
  --zz-layout-max: 1280px;
  --zz-layout-inset: var(--zz-spacing-xl);
  --zz-layout-gutter: var(--zz-spacing-sm);
  --zz-layout-rail: 288px;
  --zz-layout-schedule: 300px;
  --zz-layout-nav-height: var(--zz-comp-size-xl);

  --zz-font-ui: "Inter", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  --zz-font-mono: "JetBrains Mono", Consolas, monospace;

  --zz-bg-app-gradient: linear-gradient(168.29deg, #e8ede8 14.29%, #f5f7eb 52.14%, #cfebab 85.71%);
  --zz-bg-nav-glass: linear-gradient(180deg, rgba(255, 255, 255, 0.02) 0%, rgba(255, 255, 255, 0.12) 38%, rgba(255, 255, 255, 0.40) 72%, rgba(255, 255, 255, 0.82) 100%);
  --zz-shadow-panel: 0 36px 56px -20px rgba(20, 28, 20, 0.22);
  --zz-shadow-nav-glass: 0 6px 20px -6px rgba(38, 43, 39, 0.14), inset 0 1px 0 rgba(255, 255, 255, 0.5);
  --zz-shadow-card-glass: inset 0 1px 0 rgba(255, 255, 255, 0.88), inset 0 0 0 1px rgba(255, 255, 255, 0.42);
  --zz-blur-glass: blur(20px);
}
```

---

## 7. 日常协作与走查清单（Checklist）

- [ ] **Token 修改顺序**：先改 Figma 变量 / JSON 总表，再同步 CSS，禁止在业务组件中写死色值或像素。
- [ ] **禁止直接引用 Primitives**：业务 CSS 只消费语义层 `--zz-bg-* / --zz-text-* / --zz-brand-* / --zz-spacing-* / --zz-radius-*`。
- [ ] **对比度与可读性**：嫩绿底面配墨色文字（`--zz-text-color-primary`），炭黑底面配反白文字（`--zz-text-color-anti`），玻璃卡片仅背景半透明、文字保持 100% 不透明。
- [ ] **版面对齐验收**：桌面端所有同排卡片、左右分栏及右侧 AI 助手栏底边严格 `0px` 齐平，AI 输入框贴底，主面板底部留白统一 `24px`。
- [ ] **状态完整性**：按钮、导航、输入框均需实现 `Default / Hover / Focus / Disabled` 完整状态，表单具备可见外部标签与错误说明。
