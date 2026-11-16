# 知枝公开发布内核裁剪（重新应用）

这是从可靠 baseline 33b4dc1 重新应用的改动，不是找回旧 SHA 5a8424c。新环境中的运行结果必须重新验证；历史成功不作为本次通过。

删除列表见 deleted-files.json。删除4个明确禁用插件、通用create入口及模板、上游docs演示和上游专用部署打包资料。保留31个当前启用插件及必要src/types/锁文件/单元测试/构建配置/维护文档/版权。插件动态加载器、组件注册、语言表和维护升级逻辑不能只凭当前页面未触发而删除。

原manifest、原锁和原index保存在本目录，运行时严格验证其SHA和删除型精确子集；除确定性删除4包聚合导出外，不允许改写任何保留源码摘要。离线构建仍禁止自动fetch，不因缺依赖绕过门禁。

licenses.json记录完整许可证清单和hash；4个移除插件的LICENSE移到licenses子目录，根与31保留插件许可仍保留。NOTICE若存在同样保留。原标题、语言、颜色、字体和footer不变。

执行pnpm10.33.0 typecheck和test:core、Python发布/证书测试，准备已验证源码的dist后重建独立合成模板基线，再重复实际离线Quartz及只读领域SQLite门禁。新环境尚未执行时必须标为未测试，不能引用旧环境的122/43通过及54文件输出。

最终资格仍要求readonly领域resolver。不得声称真实账号、模型或公开部署E2E通过。原始来源资料是运行时验证依赖，不可作为普通冗余docs移走。

## 本次新环境已执行结果

validation.json仅记录本轮结果：pnpm10.33 typecheck与核心123项、Python发布/证书43项、离线字体7项均通过；31插件按原锁安装并从源码构建。两次真实Quartz各54文件逐hash相同且readonly领域门禁通过。清空字体缓存后，从独立持久字体资产包恢复，再次两构建和门禁通过，不能把缓存残留当恢复完成。

独立字体包与OFL/来源hash/恢复步骤见 ../build-assets/README.md。37份许可=原36份+新明确字体OFL。PowerShell包装脚本因官方403未在本环境执行，本轮按用户临时许可执行同样的Node和Python阶段；不将其称为PowerShell端到端通过。无真实账号、付费模型或生产部署。before.json/after.json为范围明确的Git blob统计，包含新增审计/许可但不含独立字体包。

最终已在共享venv按uv.lock对齐后复验43项发布/证书与7字体测试，并再次两次实际Quartz构建与readonly门禁；结果与锁摘要见validation.json。字体manifest已绑定publicBuildId，显式资产更新不会复用旧cache key。
