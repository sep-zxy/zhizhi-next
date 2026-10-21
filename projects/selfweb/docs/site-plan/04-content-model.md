# 内容模型

## 公开内容基础字段

所有公开页面建议至少包含：

```yaml
---
title: 标题
publish: true
section: wiki | software | blog | article | app-note
summary: 一句话摘要
tags: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## section 定义

### wiki

用途：长期知识卡片。

适合：

- 概念
- 实体
- 主题
- 方法
- 工具
- 工作流

来源：

```text
wiki/concepts/
wiki/entities/
wiki/topics/
```

注意：wiki 页面仍应遵守 Vault 的 `SCHEMA.md`。

### software

用途：软件库详情页。

来源：

```text
notes/software/
```

适合：

- 软件发现
- 下载入口
- 使用状态
- 相关 wiki / 博客 / 应用笔记关联

### blog

用途：观点和复盘。

来源：

```text
notes/blog/
```

适合：

- 观点文章
- 项目复盘
- 长期写作
- 阶段总结

边界：博客写“我怎么看、我怎么做”，wiki 写“这个东西是什么”。

### article

用途：从 raw 中筛选出的正式文章或整理稿。

来源：

```text
raw/
```

要求：

```yaml
publish: true
section: article
```

### app-note

用途：具体应用体验和配置笔记。

来源：

```text
notes/apps/
```

适合：

- 新应用体验
- 旧软件心得
- 配置记录
- 工作流笔记

## 内容边界

```text
daily/raw/notes 是源头
wiki/blog/software/article/app-note 是公开表达层
```

不要把整个 Vault 直接暴露到线上。
