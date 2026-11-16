# 应用入口

| 应用 | 用途 | 安装方式 |
|---|---|---|
| learning-web | 工程学习 React 界面 | pnpm 锁定安装 |
| wiki-private | 私有知识工作台 | npm ci |
| desktop | Electron 宿主与 sidecar | pnpm 锁定安装，GitHub 托管打包 |
| public-wiki | Quartz 净化公开投影 | site 内 npm ci，保留插件独立锁文件 |

业务授权在后端执行。公开站与私有正文分离；桌面版仍使用 growth-companion-desktop 用户目录。完整命令见 [开发指南](../docs/development.md)。
