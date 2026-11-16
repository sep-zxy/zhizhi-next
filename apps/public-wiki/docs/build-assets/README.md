# 明确的离线字体构建资产

字体不放入源码仓，避免21MB字库抵消源码精简；仅保留实际配置用于OG图的 Noto Sans SC 400/700。原品牌字体未改变。两个字体经官方 Google Fonts CSS 和 fonts.gstatic.com HTTP200独立准备，来源、字节数和SHA256见 fonts-manifest.json；完整SIL OFL1.1及Adobe版权见 NotoSansSC-OFL.txt。

持久资产包：selfweb-offline-fonts-v1.zip，Library ID `libfile_818531386384819188b2b57e5ae2a4b5`，version 0，12,659,672 bytes。包含两字体、完整OFL和一致manifest，不依赖旧云端缓存。它不是源代码bundle的一部分，恢复完整构建环境时必须同时取回此附件。

使用Library受支持下载流程取得本地zip后，从selfweb运行：

`uv run --no-project --python <已准备Python> scripts/offline-fonts.py --archive <资产包绝对路径>`

日常无网络验证：

`uv run --no-project --python <已准备Python> scripts/offline-fonts.py`

脚本要求zip精确文件集合、每份字体hash/尺寸、原始许可证和manifest一致，拒绝额外路径、改写字体、symlink。所有数据通过验证后才恢复Quartz约定cache位置。缺失或修改缓存明确失败，不自动联网替代。正式build-public-site.ps1在Quartz前调用此验证；直接运行Quartz CLI的调用方也应显式先验证。测试覆盖成功恢复、缺失/变更、路径注入、许可证和symlink拒绝。

源码npm依赖及31插件仍按各原锁文件单独准备/源码构建，字体资产包不替代它们。配置或字体升级须独立重新审核来源、许可/hash、更新asset包与manifest，并重建无用户知识模板基线，不从待发布输出生成可信资产摘要。
