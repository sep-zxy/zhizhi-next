# 私有 Wiki 工作台

独立 React 应用，base=/wiki/，使用 hash 路由展示目录、正文、收件、预览、重要确认和任务。所有领域请求走同源 /api/knowledge/v1，前端不自行生成身份或保存 bearer。

已有单条预览保留/丢弃、补料、文本附件、版本化修改/删除提案、任务继续/取消和证据阅读。它们仍须后端授权与独立复核；任务成功不表示正式写入或公开。

依赖使用 npm ci，GitHub 构建使用统一 wiki-private 目标。未配置认证时显示待连接；本机 fixture 配对不等于生产身份。工程学习跳转由宿主提供并重新授权读取，不能假设 Obsidian 已完成双向同步。

浏览器 fixture 与真实账号验收分开，默认不做截图比对。见 [配置](../../docs/configuration.md)、[验证规则](../../docs/testing.md)。
