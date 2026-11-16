# 技术路线

## 第一版路线

第一版使用 Quartz。

原因：

- 原生适合 Markdown
- 支持 Obsidian 风格双链
- 支持 backlinks
- 支持 graph view
- 支持搜索
- 适合个人 wiki 和知识站

第一版重点是发布闭环，不是复杂前端。

## Tailwind 组件库取舍

用户要求后续前端选择一个好看的 Tailwind 组件库。

推荐取舍：

```text
第一版：Quartz 默认主题 + 少量样式调整
第二阶段：Astro/Next.js + Tailwind + shadcn/ui
```

不要为了第一版引入 Tailwind 组件库而推翻 Quartz 路线。

## 推荐组件库

第二阶段优先考虑：

```text
shadcn/ui
```

理由：

- 组件复制进项目源码，便于长期定制
- 适合工具产品感界面
- 适合软件库卡片、筛选、标签、按钮、输入框
- 与 Tailwind 生态契合

如果未来不使用 React，再重新评估 daisyUI 或其他框架无关组件库。

## 可能架构

### 阶段一

```text
Quartz
Obsidian Markdown
publish: true 过滤
GitHub Pages / Cloudflare Pages / Vercel
```

### 阶段二

```text
Astro 或 Next.js
Tailwind CSS
shadcn/ui
Quartz 继续承载 wiki 或迁移为内容层
```

## 不建议第一版做的事

- 直接上复杂门户系统
- 自研 Markdown 解析和双链系统
- 一次性迁移全部 Vault
- 软件库第一版就做复杂筛选和排序
- 为视觉效果牺牲公开安全规则
