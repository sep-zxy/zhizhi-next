# 目标设计图的生成说明

这三张图描述产品构想，不表示当前代码完成度。设计依据见 [产品方向](../../../.agents/notes/proposed/2026-11-16-product-direction.md)。使用内置 imagegen；参考上一版知枝信息图的配色与图标风格，重新组织信息。

| 文件 | 用途 |
|---|---|
| [target-overview.png](target-overview.png) | 项目目标全景 |
| [knowledge-cards-relations.png](knowledge-cards-relations.png) | 学习卡、长期主题及关联建立 |
| [learning-feedback-loop.png](learning-feedback-loop.png) | 地图、雷达、知返与 Wiki 回流 |

重绘时可以把同目录现有图片作为风格参考。下列提示词为本轮实际使用内容，示例关系不是最终 Schema 枚举。

## 产品目标全景

```text
Use case: infographic-diagram.
Asset type: Chinese open-source README product vision diagram, landscape 3:2, very high resolution, readable Chinese typography.
Primary request: Completely redesign the prior Zhizhi diagram to describe the intended future product, not implementation status. Input images are STYLE REFERENCES ONLY: retain cream background, dark navy text, restrained emerald green and soft teal, elegant thin line icons and rounded cards. Replace all old content and layout. Do not show completed/planned badges, checkmarks, percentages, source commit numbers, backend folder paths or current implementation claims. Clear flow and generous spacing matter more than decorative elements.

Title exactly: “知枝 · 我们想实现的个人知识系统”
Small subtitle exactly: “目标设计 · 不代表当前完成度”

Organize an instantly understandable product flow around a large central knowledge asset:
LEFT two entry sources:
“读到的内容” with “文章 · 视频 · 链接 · 笔记”
below it “做过的实践” with “Git 提交 · 项目问题 · 主动探索”
Their paths respectively say “采集 → 摘要预览 → 本人收藏” and “知返：理解 · 练习 · 实践”.

TOP show “微信 / Telegram” and “Web 工作台” connecting into “知识 Agent”, with small label “自然语言交互 · 调用知识与学习能力”. Agent participates in collection, search and learning, without bypassing review.

CENTER show the incoming update path “匹配已有主题 → 增补或新建提案 → 分级审核” leading into a visually dominant card titled “同一套 Wiki 知识体系”.
Inside that card show two clearly separated equal sections:
“知识整理” — “定义 · 机制 · 边界 · 来源”
“我的理解” — “本人解释 · 类比 · 实践心得”
Bottom of central card: “相关知识 · 来源证据 · 修订历史”.
Near review path show a concise note: “普通有据增补可自动；个人观点与重大改动本人确认”.

RIGHT show three derived uses:
“知识地图” — “浏览主题与已确认关系”
“学习雷达” — “提出候选关联与学习建议”
“按权限发布 Wiki” — “私有阅读 / 合格公开投影”
A clear feedback arrow from learning radar to 知返 learning path says “本人采纳后学习，再回填”.

BOTTOM place “Obsidian · 主要编辑界面” connected bidirectionally to the central Wiki, label “Git 同步 · 版本追踪 · 冲突处理”.
A small calm foundation note: “正文：Markdown / Git　流程状态：SQLite　原件与索引分层保存”.

All displayed text in simplified Chinese as specified. Product relationships must be clear: all lasting outcomes converge into the SAME Wiki, knowledge organization and personal understanding remain visibly distinct, Obsidian remains primary editor, raw materials are distinct from formal knowledge, private knowledge does not automatically become public. Do not include a status-colored function tree or invented mastery scores. This is an explanatory product illustration, not a software microservice topology.
```

## 知识卡与关联

```text
Use case: infographic-diagram.
Asset type: a readable Chinese product-design infographic for Zhizhi's open-source README; landscape 3:2 at high resolution.
Input image is a STYLE REFERENCE ONLY: match its cream background, dark navy type, emerald/teal outlines, softly tinted cards, clear line icons and generous spacing. Create a new information architecture, not a status chart.

Main title exactly: “知枝 · 知识卡与关联如何长出来”
Subtitle exactly: “目标设计 · 让关联有理由、有证据、可追溯”

The main purpose is to explain the detailed intended knowledge model and relationship-building process, not list software modules.
TOP small source band: “素材 A” “素材 B” “项目实践” converging through “匹配已有主题”.
A note exactly: “一份材料可支持多个主题；一个主题可由多份材料持续补充”.
A compact decision band: “同主题优先补充 · 独立主题再新建 · 重复与冲突先处理”.

MIDDLE LEFT a compact card titled “学习卡 · 学习入口”, with short lines “问题与讲解” “理解与练习” “本人总结与实践证据”.
An arrow labeled “学习成果补充主题” leads to the CENTER dominant permanent topic card:
header “Wiki 主题：HTTP 身份认证”
small stable-identity tag “稳定主题 ID”
four visibly separated rows:
“知识整理” — “机制 · 来源 · 适用边界”
“我的理解” — “本人原话 · 解释 · 实践心得”
“相关知识” — “关联类型 · 理由 · 证据”
“演进记录” — “来源版本 · 修订历史”
Keep 知识整理 and 我的理解 visually distinct, but in one topic card. Do not invent a personal opinion.

MIDDLE RIGHT draw an intelligible sample knowledge network with nodes “Cookie”, “Session”, “JWT”.
Mark the network “关系示例”.
Use a solid emerald edge between Cookie and Session labeled “配合机制”; an amber dashed edge between Session and JWT labeled “取舍待验证”; a gray dotted edge for “阅读跳转”.
Provide explicit line-style legend “导航链接” “候选关联” “已确认关系”. These colors refer to RELATION TYPE, not completion status.
Do not make every edge a confirmed fact. Clearly show that the candidate edge is provisional.

BOTTOM a large, legible relationship establishment pipeline:
“本人 / AI 提出候选” → “写清类型、理由、证据与版本” → “核对证据 · 本人确认” → “确认 / 待证 / 拒绝”.
Keep the middle pipeline as the main takeaway rather than tiny text.
Footer exactly: “双链用于导航；AI 建议和学习完成，不会自动变成已确认关系”.

Simplified Chinese must be crisp and correctly rendered. No completion badges, checkmarks, mastery scores, dates, source SHAs, backend directory paths, or production-ready claims. Relationship names are illustrative. The composition should make clear that learning cards are learning entries while lasting knowledge and relationships are organized around stable Wiki topics.
```

## 学习反馈闭环

```text
Use case: infographic-diagram.
Asset type: Chinese open-source product vision image, landscape 3:2, high resolution.
Style reference: retain the cream background, dark navy typography, emerald/teal outlines, clean rounded cards and subtle warm amber accents of the reference. All visuals must communicate FUTURE DESIGN, never software completion.

Title exactly: “知枝 · 让知识网络推动下一次学习”
Small subtitle exactly: “目标设计 · 地图 → 雷达 → 知返 → Wiki 回流”

Create a clear circular product-learning loop, with numbered stages only to indicate FLOW ORDER:
1. “知识地图” — “查看已有主题、已确认关系与关联路径”
2. “学习雷达” — “发现候选关联、缺失环节与待证问题”
3. “本人选择” — three options “采纳 / 延后 / 忽略”; only the 采纳 branch proceeds
4. “知返学习与实践” — “解释概念 · 探索联系 · 做项目验证”
5. “整理成果与分级审核” — “优先补充已有主题，必要时提出新主题”
6. “更新 Wiki 与关系” — display two separate sections “知识整理” and “我的理解”; then return to the knowledge map.

Show a visually distinct evidence panel beneath learning stage with three equally weighted small cards:
“理解证据” — “能解释概念与边界”
“关联证据” — “能说明为什么相连”
“应用证据” — “项目、练习与复盘”
These are evidence categories, not scores or achievements.

A concise governance callout: “普通有据增补可自动；个人观点与重大改动本人确认”.
A small secondary path labeled “月度回顾”: “本人选材料 → AI 提议 → 本人审阅 → 更新我的理解”, joining the same Wiki rather than a second isolated knowledge store.
Footer exactly: “孤立节点是探索线索；连线数量与任务完成率，不等于掌握度”.

Prioritize a readable feedback loop over a dense module tree. Arrows must show that adopting a radar suggestion creates a learning task, that learning produces evidence and a proposal, and that formal Wiki/relationship changes still pass review. No auto-confirmation of an AI relationship merely because a lesson was finished. No status badges, completion colors, percentages, code paths or claims that the design is already delivered.
```

## 定点校正：vision_cards_final

```text
Edit the provided Chinese Zhizhi knowledge-card infographic with ONE precise text correction. The small gray label above the gray dotted navigation edge between Cookie and JWT must read exactly “阅读跳转” (last character 转), with no misspelling. Preserve every other word, card, color, icon, line style, arrow, spacing and layout exactly. Do not add content or reinterpret the model. Keep the title and future-design subtitle unchanged.
```

## 定点校正：vision_loop_final

```text
Edit the provided Chinese Zhizhi feedback-loop infographic with ONE precise diagram correction. Remove the short horizontal left-pointing arrow from the LEFT EDGE of the stage 3 “本人选择” box into the central “同一套 Wiki 知识体系” card. There must be NO direct arrow from stage 3 user choice to the formal Wiki: adopting a suggestion only creates a learning task. Preserve the existing downward curved arrow from stage 3 to stage 4, its “采纳后 生成学习任务” label, the entire stages 1→2→3→4→5→6→1 cycle, the stage 5 review path, all text, icons, colors, spacing and remaining arrows exactly. In particular the monthly-review approved path and all three evidence cards remain. Fill the removed arrow area seamlessly with the original cream background. Do not add another arrow or remove any other connection.
```
