# 知枝公开知识库：Quartz v5 发布内核

保留31个启用插件、原品牌配置与必要维护/安全测试。正式入口为 ../scripts/build-public-site.ps1，边界见 ../docs/DETERMINISTIC_PUBLICATION.md，裁剪与来源许可见 ../docs/pruning-v1/README.md。

使用 config/toolchains.json 登记的 npm 执行 npm run typecheck 和 npm run test:core。依赖解析仍以package-lock及插件各锁为准。通用create和上游演示模板不再交付；离线构建不自动fetch或部署。

Quartz作者Jacky Zhao和Quartz Community的MIT版权保留于LICENSE.txt和各插件LICENSE。上游 https://github.com/jackyzha0/quartz ，文档 https://quartz.jzhao.xyz/ 。
