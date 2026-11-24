# 应用入口

| 应用 | 用途 | 安装方式 |
|---|---|---|
| learning-web | 工程学习 React 界面 | pnpm 锁定安装 |
| wiki-private | 私有知识工作台 | npm ci |
| desktop | Electron 宿主与 sidecar | pnpm 锁定安装，GitHub 托管打包 |
| public-wiki | Quartz 净化公开投影 | site 内 npm ci，保留插件独立锁文件 |

业务授权在后端执行。公开站与私有正文分离；桌面版仍使用 growth-companion-desktop 用户目录。完整命令见 [开发指南](../docs/development.md)。

学习界面的「个人 Wiki」入口访问同源 `/wiki/`，由宿主提供页面或重定向到已配置的私有 Wiki。学习界面的离线导航回退不接管 `/wiki` 及其子路径，避免缓存的学习页面覆盖宿主跳转；部署修复时须同时更新已安装的 Service Worker。

学习候选的「代表提交」在对应项目和仓库内打开该次提交，候选里的代码片段也从同一仓库读取。旧候选只有在能唯一确定项目和仓库时才提供跳转。归属不明或当前未授权时，页面保留来源摘要并提示原因。
