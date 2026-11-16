# 个人网站计划索引

本目录用于拆分记录个人网站的产品定义、内容规则和落地计划，不把全部规划塞进单个长文档。

## 当前结论

第一版定位为：以 `C:\Code\notes\zxy` Obsidian Vault 为内容源的公开个人知识库，优先用 Quartz 跑通可访问、可搜索、可持续同步的发布闭环，并加入软件库总览和少量软件详情样例。

核心取舍：

- 主身份：公开知识库
- 第一读者：未来的自己，兼顾 AI/PKM 同好和搜索引擎访客
- 公开规则：只公开 `publish: true`
- 第一版范围：Wiki + 软件库
- 视觉路线：第一版使用默认 Quartz，后续再引入 Tailwind 组件库增强前端
- 发布方式：半自动同步，发布前人工确认

## 分类文档

- [01-positioning.md](01-positioning.md)：网站定位、读者、第一版主线
- [02-publish-boundary.md](02-publish-boundary.md)：公开边界、禁区、发布安全规则
- [03-information-architecture.md](03-information-architecture.md)：首页、导航、栏目结构
- [04-content-model.md](04-content-model.md)：公开内容类型和 frontmatter 规范
- [05-software-library.md](05-software-library.md)：软件库模型、字段、页面要求
- [06-technical-route.md](06-technical-route.md)：Quartz、Tailwind 组件库和后续技术路线
- [07-sync-workflow.md](07-sync-workflow.md)：半自动发布同步流程
- [08-v1-acceptance.md](08-v1-acceptance.md)：第一版验收标准
- [09-roadmap.md](09-roadmap.md)：分阶段实施路线
- [10-assets-and-images.md](10-assets-and-images.md)：图片与附件处理规则

## 后续执行原则

1. 先搭建可重复发布链路，再追求视觉和交互。
2. 所有公开内容先通过 `publish: true` 显式进入网站。
3. `daily/` 和 `output/` 默认不公开。
4. `raw/` 默认作为素材层，不直接公开；只有精选内容可通过 `section: article` 发布。
5. 软件库先做结构化样例，不急着做大型软件下载站。
6. 图片和附件按公开页面的引用关系发布，不直接公开整个 `assets/`。
