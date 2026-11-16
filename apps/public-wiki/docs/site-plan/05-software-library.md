# 软件库设计

## 定位

软件库不是大型软件下载站，而是个人使用、测试和收藏的软件目录。

目标：

- 统一记录软件发现
- 管理官网和下载入口
- 记录使用状态
- 关联 wiki、博客、应用笔记
- 后续支持分类和筛选

## 收录范围

收录：

- 已经用过的软件
- 正在测试的软件
- 收藏待试的软件

不建议第一版大量搬运软件信息。

## 状态字段

推荐使用：

```yaml
status: used | testing | bookmarked | abandoned
```

含义：

- `used`：已经实际使用，有基本判断
- `testing`：正在测试
- `bookmarked`：收藏待试
- `abandoned`：弃用或不再推荐

## 软件 frontmatter

推荐模板：

```yaml
---
title: Obsidian
publish: true
section: software
software_id: obsidian
status: used
category: knowledge-management
platforms: [windows, macos, linux, ios, android]
pricing: freemium
open_source: false
official_url: https://obsidian.md
download_url: https://obsidian.md/download
summary: 基于 Markdown 的本地优先知识管理软件。
related:
  wiki: [wiki/entities/obsidian.md]
  blog: []
  app_notes: []
created: 2026-07-08
updated: 2026-07-08
---
```

## 软件库总览页

路径：

```text
/software/
```

第一版展示字段：

- 软件名
- 一句话简介
- 使用状态
- 分类
- 平台
- 价格模式
- 是否开源
- 官网
- 下载入口
- 详情页
- 最近更新

## 软件详情页

路径：

```text
/software/<software_id>/
```

必须包含：

- 一句话简介
- 官网
- 下载地址
- 平台
- 价格模式
- 是否开源
- 相关 wiki / 博客 / 应用笔记

可选包含：

- 我的使用笔记
- 配置技巧
- 替代品
- 截图
- 常见问题

## 第一版样例数量

第一版至少准备 3-5 个软件样例。

优先选择已经在 Vault 中出现过的工具，例如：

- Obsidian
- Claude Code
- NotebookLM
- Open Web UI
- draw.io
