# 图片与附件处理

## 结论

图片和附件必须纳入发布规则。

原因：

- Obsidian 笔记可能使用 `![[image.png]]` 嵌入图片
- Markdown 也可能使用 `![alt](path/image.png)` 引用图片
- 公开页面如果引用图片，但构建时没有复制图片，会出现线上破图
- 私有笔记里的图片不能因为被放在 `assets/` 里就自动公开

## 基本规则

图片不按目录默认公开，按引用关系公开。

也就是说：

```text
只有被 publish: true 页面引用到的图片，才进入站点构建结果。
```

不要直接把整个 `assets/` 目录发布到线上。

## 支持的引用形式

第一版至少支持两种形式：

```md
![[image.png]]
![[assets/image.png]]
![说明](assets/image.png)
```

后续如果出现更多 Obsidian 嵌入形式，再逐步扩展。

## 推荐 Vault 图片目录

建议统一把公开可用图片放在：

```text
assets/public/
```

私有或临时图片放在：

```text
assets/private/
assets/raw/
```

如果暂时不调整 Vault 结构，也可以先保留现状，但同步脚本必须按引用关系复制图片。

## 发布时怎么处理图片

发布同步流程应做：

1. 扫描所有 `publish: true` Markdown
2. 解析其中的图片引用
3. 在 Vault 中定位图片真实路径
4. 只复制这些被引用图片到 Quartz `content/` 或构建可访问目录
5. 保持相对路径可解析
6. 输出图片检查报告

## 破图检查

发布前必须检查：

- 图片文件是否存在
- 图片路径大小写是否匹配
- 图片是否来自私有目录
- 图片是否被公开页面引用但未复制
- 图片文件名是否包含空格、中文或特殊符号导致构建风险

## 私有图片规则

以下图片不得发布：

- daily 中的截图
- 私人聊天截图
- 账号、密钥、后台、订单、付款、邮箱截图
- 未授权的人物图片
- raw 素材里的未筛选截图
- output 中生成报告的中间图片

如果公开页面必须引用这类图片，应先复制一份脱敏版本到 `assets/public/`。

## 软件库图片

软件库第一版不强制使用截图。

如果使用图片，建议字段：

```yaml
cover: assets/public/software/obsidian-cover.png
screenshots:
  - assets/public/software/obsidian-01.png
```

图片要求：

- 优先使用自己截取或官方允许使用的图片
- 不存账号信息
- 不截私人 Vault 内容
- 文件名使用英文小写连字符

## Quartz 阶段建议

Quartz 支持 Obsidian 风格 wikilinks 和图片嵌入，但前提是图片能被构建系统找到。

第一版不要依赖“整个 Vault 附件目录都公开”。

推荐策略：

```text
公开 Markdown 白名单
+ 被公开 Markdown 引用到的图片白名单
```

## 后续增强

后续可以增加：

- 自动压缩图片
- 自动生成缩略图
- 自动检测大图
- 图片 alt 文本检查
- 软件库封面图规范
- Open Graph 分享图
