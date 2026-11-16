# zhizhi-next 仓库迁移

迁移基线、上游和精确路径映射维护于 [layout.json](layout.json)。原始 Git 历史及 migration/zhizhi-platform-62397c2f 标签保留；新的私有远端为 sep-zxy/zhizhi-next。

本轮仅整理职责目录、相对路径、分层规则、开发入口、配置示例和 GitHub Actions。旧底座、可选组件、测试和许可保留；目录搬迁不实现产品提案，也不继承历史模型、设备或生产部署授权。

原样冻结的来源校验文件与 vendored 锁文件不随文本路径替换改写。历史验收与退役交付退出默认 CI；现有便携 runtime 和通用装配代码保留，固定旧发行授权槽不转为新仓库默认功能。

本次生成的说明图片位于本地 output/imagegen，作为非运行产物保留并忽略，不上传为源码。完整检查和产物以新仓库 Actions 记录为准；本地只报告实际执行的语法解析。
