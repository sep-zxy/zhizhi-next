# 目标设计图的生成说明

这三张图描述已确认的产品方向，不表示当前代码完成度。设计依据见[产品方向](../../../.agents/notes/proposed/2026-11-16-product-direction.md)。本轮使用内置 imagegen，以同名旧图作为视觉风格参考，按原子知识卡、独立学习记录、主题组织与每日预算重绘。旧版内容由 Git 历史保留。

| 文件 | 用途 |
|---|---|
| [target-overview.png](target-overview.png) | 输入、审核、原子卡与主题组织的全景 |
| [knowledge-cards-relations.png](knowledge-cards-relations.png) | 丰富的原子卡、独立学习记录、多主题引用与卡间语义关系 |
| [learning-feedback-loop.png](learning-feedback-loop.png) | 聚合输入、优先补旧卡、正式新增与每日学习预算 |

图中候选分配数字仅为示意；每天 0～3 张正式新增、一条主线约 15～20 分钟是可调整的起始默认。关系名称为解释用示例，未固定为 Schema 枚举。

以下为本轮实际使用的提示词；输入图仅作风格参考，图中旧对象定义已被新方案取代。

## 产品目标全景

```text
Use case: infographic-diagram.
Asset type: Chinese open-source README vision diagram, high-resolution landscape 3:2.
Input image: STYLE REFERENCE ONLY. Keep its cream background, navy Chinese typography, emerald/soft teal rounded cards, thin line icons, occasional amber review notes. Completely redesign content and composition to reflect the new model below. Large readable simplified Chinese, strong hierarchy, generous whitespace.
Title exactly “知枝 · 从日常输入到自己的知识网络”
Subtitle exactly “目标设计 · 不代表当前完成度”
Top slim entry bar: “微信 / Telegram · Web 工作台 → 知识 Agent”.
Main left-to-right flow:
Left two input cards “读到的材料” with “采集 → 摘要预览 → 本人收藏”, and “做过的实践” with “Git 提交 · 项目问题 · 主动探索”.
Both flow into a compact gateway “聚合工作意图 → 匹配已有卡 → 增补或新建提案 → 分级审核”. This gateway leads into a large central container “同一套 Wiki 知识资产”.
Within central container, show TWO clearly distinct layers:
Bottom/main content layer “原子知识卡” — “一个可独立解释、验证、迁移的问题”; draw three rich small document cards connected to one another, not isolated single-word tiles. Beside these use “知识整理 | 我的理解”, then “来源 · 边界 · 版本 · 卡片关系”.
Top organization layer “主题 Wiki” — “引用卡片 · 组织上下文 · 综合理解”; a thin connector labeled “组织与引用” from the theme layer to the atomic card layer, NOT a content-copying arrow.
Outside the central knowledge asset, underneath left, a distinct process card “知返 · 学习记录” with “阅读 · 练习 · 解释 · 实践”, small note “多次学习可指向同一张卡”. It points through “成果提案 → 分级审核” into knowledge cards. Learning records themselves remain separate from lasting card content.
Right derived uses: “知识地图” with “浏览卡片关系与主题概览”; “学习雷达” with “发现候选联系与待证问题”; “按权限发布” with “私有阅读 / 合格公开投影”.
From 学习雷达 a clearly labeled separate return arrow “本人采纳 → 学习任务” goes ONLY to 知返学习记录, NEVER directly to knowledge assets.
Bottom bar “Obsidian · 主要编辑界面 ↔ Markdown / Git” with “双向同步 · 冲突保留与合并”.
Small governance note “普通有据增补可自动；个人观点与重大改动本人确认”.
Small footer “正文、原件、流程记录与索引分层保存”.
Semantic constraints: atomic cards and topic Wiki belong to one set of knowledge assets; learning history is separate process evidence. Relationships primarily connect cards, topics organize them. Do not label a knowledge card merely as a learning entry. No completion marks, percentages, status legend, repository paths, or current delivery claims. Avoid tangled arrows; omit decorative minor connectors rather than inventing shortcuts.
```

## 原子知识卡与关联

```text
Use case: infographic-diagram.
Asset type: high-resolution Chinese README product-design explanatory image, landscape 3:2.
Input image: STYLE REFERENCE ONLY. Retain cream, navy, emerald/soft teal cards, clean line icons and amber candidate accents. Replace all old structure and text; previous “学习卡只是入口/关系只落主题” model has been superseded.
Title exactly “知枝 · 原子卡有深度，关联有依据”
Subtitle exactly “目标设计 · 一个独立问题，多次学习，持续补充”
Main left half, the largest card:
header “知识卡 · 稳定身份”
question in large text “为什么重试需要考虑幂等？”
rich separate rows:
“知识整理” — “机制 · 项目场景 · 方案取舍 · 适用边界”
“我的理解” — “本人解释 · 实践心得 · 后续修正”
“来源与演进” — “代码与原文证据 · 修订版本”
small callout beneath exactly “原子 ≠ 一句话；新例子、新来源优先补旧卡”
Upper right, smaller “主题 Wiki：可靠的请求处理” with “引用多张卡 · 提供上下文与综合理解”. Two thin lines marked “组织与引用” point from this topic to main question card and a right-side second card “幂等键应该怎么设计？”. Show the same second card can also be referenced by a smaller theme tag “支付可靠性”; do not duplicate card content.
Between main card and second card draw an AMBER DASHED semantic edge, label “候选：机制到实现”. A compact note below edge “理由 + 证据 + 版本”. Distinguish semantic edge from gray thin topic-reference lines.
Lower left a separate process strip “学习记录：第一次解释 / 项目实践 / 再次复盘” with multiple slim arrows pointing at SAME main card, caption “换一道练习，不另建一张知识卡”. Explicitly label arrows “形成证据与修订提案” so practice does not auto-write personal views.
Lower right concise three-part panel:
“来源关联：来自哪里”
“主题归属：放在什么上下文”
“卡间语义：为什么有关”
small legend “灰点线：导航　琥珀虚线：候选　绿实线：已确认”.
Bottom full-width semantic relationship process:
“少量相关旧卡 → 提出类型、理由与证据 → 核对并确认 → 确认 / 待证 / 拒绝”
footer exactly “关系有依据 ≠ 我已理解；完成练习不会自动确认关联”
Constraints: primary semantic relations between ATOMIC KNOWLEDGE CARDS; topic wiki organizes and references rather than duplicating content. An atomic card has a useful independently learnable question, not every word/syntax. Source and theme membership never count as confirmed semantic relations. Do not overload diagram or add badges/scores. Use rich content example and clear card references, no invented personal quote.
```

## 每日学习预算与反馈

```text
Use case: infographic-diagram.
Asset type: high-resolution Chinese README product-design infographic, landscape 3:2.
Input image: STYLE REFERENCE ONLY. Cream background, navy readable text, emerald/soft teal rounded blocks, amber provisional candidates, clean thin line icons. Completely redesign prior circular figure as clear funnel with a small feedback loop; fit generous whitespace.
Title exactly “知枝 · 做了很多，每天只学值得学的”
Subtitle exactly “目标设计 · 分开控制候选、正式新增和学习负担”
Top horizontal flow, label it “一天的项目输入 · 数量仅为示意”:
“50 次提交 / 1 万行改动” → “按工作意图归并：4 项工作” → “提炼 12 个候选问题”
From 12 candidates branch cleanly into THREE blocks in middle row:
“7 个匹配旧卡” / “补来源或提出修订”
“3 个暂存候选” / “合并 · 降权 · 延后”
“2 个值得新建” / “入选后才生成完整卡”
Explicit amber small note near new cards “不先生成 100 张，再只展示几张”.
Below three branches, create two large distinct budget cards:
left “正式新增预算” with large “每天 0～3 张” and “可调整的起始默认 · 允许 0 张 · 不凑数”
right “今日学习预算” with large “一条主线 · 约 15～20 分钟” and “新旧卡 + 练习 + 关键关联；示例 2～3 张”
Make clear 0–3 limits FORMAL NEW CARDS, not all learning or candidate extraction. Daily time includes revisiting existing cards and exercises; no daily card-count obligation.
Bottom a compact real feedback path:
“知识地图 / 学习雷达 → 本人采纳 → 知返学习与实践 → 成果提案与分级审核 → 更新原卡或必要时新建”
Loop final outcome back to map/radar with a small discreet arrow; NO direct arrow from adopting to formal Wiki.
A small evidence strip under path “分别保留：理解证据 · 关联证据 · 应用证据”.
Footer in readable two short sentences:
“候选不是必须清空的待办；过期可退出推荐，保留来源线索。”
“漏学不叠加补课；普通有据增补可自动，个人观点与重大改动本人确认。”
All figures are illustrative except adjustable initial defaults explicitly labeled; no current implementation claims, mastery percentages, completion icons, overly complex module tree. Do not make 2 new cards imply all 7 old-card supplements require relearning. Do not imply candidate staging generates full detailed cards.
```

## 定点校正：主题引用连线

```text
Edit this image with ONE precise diagram correction. The short GREEN UPWARD arrow from the topic tag “支付可靠性” to the knowledge card “幂等键应该怎么设计？” must become a thin GRAY topic-reference arrow, matching the other gray “组织与引用” connectors from the upper theme Wiki. Add a compact gray label “主题引用” beside this short arrow if it fits. This arrow expresses theme membership/reference, NEVER a confirmed card-to-card semantic relation; therefore it must not use the solid green confirmed-relation style. Preserve every other word, card, position, icon, color, line, title, amber candidate edge, legend, learning record arrows and footer exactly. Do not redesign or add other content.
```
