# Vault Manager

本地 Obsidian Vault 属性管理器与 Schema 校验器。默认管理 `C:\Code\notes\zxy`，规则直接读取 Vault 根目录 `SCHEMA.md` 中的 `vault-schema-registry` JSON 块。

## 能力

- 查看最近新增和最近更新内容
- 修改标签、摘要和公开状态等允许属性
- 使用顶栏弹窗新建 raw 文章、博客和软件条目
- 显示缺失属性和输入占位提示，并按 Schema 一键补齐默认值或统一占位值
- 批量预览、替换或删除标签
- 规范化 raw 固定字段，清理重复图片、twemoji 与 Discourse 多行图片尺寸/文件大小噪声，并按 `raw/YYYY-MM/` 归档
- 校验字段、枚举、标签、日期、公开来源和文章 slug 冲突
- 通过 Obsidian 打开正文，不在管理器内编辑 Markdown 正文

## 命令

双击 `bin\vault-manager.exe` 即可启动界面，发布版使用 Windows GUI 子系统，不弹出控制台窗口。命令行操作使用独立入口：

```powershell
$ErrorActionPreference = 'Stop'
& '.\bin\vault-manager-cli.exe' validate --vault 'C:\Code\notes\zxy' --scope all --json
& '.\bin\vault-manager-cli.exe' validate --vault 'C:\Code\notes\zxy' --scope changed --json
& '.\bin\vault-manager-cli.exe' normalize --vault 'C:\Code\notes\zxy' --dry-run --json
```

服务只监听 `127.0.0.1`。写操作需要当前页面注入的会话令牌，并使用路径白名单、文件锁、修改时间检查、写前备份和同目录原子替换。

界面使用本地内嵌的 Tabler Core 1.4.0（MIT），不依赖 CDN。缺失属性提示与补全逻辑直接读取 `SCHEMA.md`；不能可靠推导的来源或网址写入 `.invalid` 保留域安全默认值，该值明确表示事实尚未记录，但满足直接公开所需的字段完整性。

## 品牌资源

- SVG：`assets/vault-manager-logo.svg`
- PNG：`assets/vault-manager-logo.png`
- Windows 图标：`assets/vault-manager.ico`
- 重建图标：`uv run --with pillow scripts/build_icon.py`
