# 信息架构

## 一级导航

第一版建议导航：

```text
首页
Wiki
软件库
博客
文章
应用笔记
最新内容
搜索
```

其中第一版必须做扎实：

- 首页
- Wiki
- 软件库
- 搜索
- 最新内容

博客、文章、应用笔记可以先建立入口和规则，不强求内容量。

## 首页结构

首页不要做营销页，直接服务知识访问。

推荐结构：

1. 搜索入口
2. Wiki 导航入口
3. 软件库入口
4. 最新公开内容
5. 常用标签或主题入口

首页第一屏应让用户明确知道：

- 这是一个公开知识库
- 可以搜索
- 可以进入 Wiki
- 可以进入软件库

## Wiki 结构

沿用现有 Vault 结构：

```text
wiki/
├─ concepts/
├─ entities/
├─ topics/
└─ index.md
```

展示方式：

- Concepts：概念、方法、模型、术语
- Entities：工具、产品、人物、组织、书籍
- Topics：主题综述、工作流、问题域

## 软件库结构

推荐路径：

```text
/software/
/software/<software_id>/
```

内容源建议：

```text
notes/software/
```

## 博客、文章、应用笔记

建议路径：

```text
/blog/
/articles/
/app-notes/
```

内容源建议：

```text
notes/blog/
raw/                # 仅 publish: true + section: article
notes/apps/
```

## 最新内容

最新内容不应等同于最新文件。

只展示：

- `publish: true`
- 有 `updated`
- 属于公开 section

排序字段：

```yaml
updated: YYYY-MM-DD
```
