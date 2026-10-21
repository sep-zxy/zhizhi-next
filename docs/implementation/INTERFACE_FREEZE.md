# 共享接口冻结 v1

代码主路径 projects/growth/src/ahadiff/knowledge_platform/。Python 命名空间保留 ahadiff；Starlette 领域 HTTP API 前缀 /api/knowledge/v1。现有桌面 API 不破坏。

## 所有权
A impl/domain：knowledge_platform/{contracts.py,errors.py,domain.py,store.py,tasks.py,auth.py,sources.py,changes.py,reviews.py,writer.py,migrations,core_routes.py} 与 tests/knowledge_platform/domain/；可添加其中细分子包。
B impl/intake：knowledge_platform/{intake,channels,extractors,media}/ 与 tests/knowledge_platform/intake/。
C impl/agent：integrations/pi-zhizhi/ 与 knowledge_platform/{model,generation,ai_review}/ 与 tests/knowledge_platform/agent/。
D impl/zhizhi：knowledge_platform/{learning,search,radar,publish}/、既有 growth 学习/Wiki/模型调用必要适配、projects/selfweb/ 发布脚本与 tests/knowledge_platform/zhizhi/。
主协调：根配置、依赖/lockfile、knowledge_platform/__init__.py/app.py/__main__.py、服务装配、跨模块 E2E、CI与部署定义。不得私自改共享文件；提出依赖请求。

## 跨路规范
统一返回 {data, task_id, trace_id}；错误 {error:{code,retryable,detail},trace_id}。IntakeID等在data中。写操作认证owner从服务上下文派生，不能信任请求体owner；幂等键冲突返回409。API不允许模型伪造user批准。所有公共方法/模型需有类型、docstring、最小可跑契约测试。

A首先确定 DomainService 实际接口并在 docs/implementation/DOMAIN_API.md 写清方法与参数，尽早给其他三路共享该契约。DomainService 使用一个domain SQLite写入口及GitCAS writer，输入输出以JSON可序列化dict为边界，避免四路自行建数据库。关键操作 create/read/update_task、create/read_intake、retain_source、get_source、propose_change、record_review、apply_change、get_entry、list_entries、event查询。同步领域函数；HTTP/模型/媒体I/O由async适配负责。Actor对象身份只来自已鉴权上下文。

B 导出 create_intake_routes(domain, model_runner=None)（Starlette Route列表）及IntakeService；只通过DomainService写账本，保留preview→keep边界。
C 导出 ModelRunner async run(task:ModelTask)->ModelResult（协议先写docs/implementation/MODEL_API.md），支持正式provider流程、fixture runner显式隔离、auth/quota/invalid_output状态。工具调用领域HTTP不能裸写库。跨路无真实provider时只标blocked，不把fixture算真实E2E。
D 导出 create_read_routes(domain, model_runner=None)、KnowledgeBackend协议/RemoteKnowledgeBackend；原有模型调用也走C ModelRunner（统一模式），禁止收费旁路。发布确定性，无模型。

每路可为现有源码适配提出接口修订，但必须向其他路和主协调报告，不在隐式约定下重写他人模块。单模块fixture允许并行开发；最终整合必须真实端到端全部通过。

## 里程碑
导入快照hash已校验。16份保守排除文件待本机安全补包，不可用删除依赖或测试掩盖。设计图已发送，再开始代码。构建/测试执行遵守AGENTS，当前云构建许可待用户答复。
