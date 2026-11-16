# 收件 HTTP 分类回执合同

本补丁修正 P15 Pi 薄入口与既有收件 HTTP 的合同不一致，不增加模型权限、身份、会话或发布能力。

## 成功响应

所有 `/api/knowledge/v1/intakes` 成功响应，包括接收、单条读取、列表、能力元数据、文本补料、文本附件、保留及丢弃，均携带顶层：

- `data_classification`：由领域 `classify_record` 计算的保守展示分类。
- `classification_labels`：领域 `classification_labels` 收集的完整、去重、排序标签。授权必须核对全部标签，不能只看一个最高等级标签。
- 原有 `data`、`task_id`、`trace_id` 保持。

分类来自已经按身份、owner、read scope 和分类权限授权的正式记录，不从请求直接复制，也不根据脱敏后的字段猜测。白名单投影丢弃 `payload` / `metadata` 时，内部的混合分类仍保留在回执标签中。保留决定的分类来自实际 Source；不改变独立用户决定及 Writer / 审核边界。

接收仍返回 202 和稳定 `receipt_id` / `intake_id` / `task_id`，现复用单条读取的安全记录投影并保留公开资格说明。不会返回原始 payload、owner、内部 metadata、cache 引用、RAW 对象路径或附件 base64。此响应只表示临时收件，不表示已保留、已生成知识或已公开。

列表仅聚合调用者实际可见的项目分类。空页和无用户内容的能力元数据标为 `public`，不会把不可见记录的标签、数量或存在性泄漏到 envelope。能力元数据仍要求认证及 `knowledge:read`。错误响应保持原有错误结构，不伪造可消费的成功分类。

所有返回记录的 HTTP 操作在解析正文前要求 `knowledge:read`，不依赖响应构造阶段才拒绝 write-only 身份。收尾审查曾在未修代码上真实复现：receive、supplement、text-attachment 返回 403 时已产生副作用；keep/discard 已在内部读取前拒绝。现统一前置检查，并对五条写入路径重复请求检查整个 SQLite 账本、任务/租约、幂等回执、事件、版本、缓存目录/字节及 RAW 对象完全不变；另以禁止正文解析的探针覆盖五条路径。

## 回归与跨语言合同

`projects/growth/tests/knowledge_platform/intake/test_http_classification.py` 使用实际临时 DomainService 与 ASGI 路由，覆盖收件重放、各写入回执、完整混合标签、隐藏项分页、空态、secret/unknown、401/403/404 及安全字段投影。

`projects/wiki-private/tests/integration/check_intake_pi_contract.py` 将实际 ASGI 产生的 11 份合成响应交给原封未改的 Pi `DomainClient`。只替换网络 fetch 为捕获响应适配器，仍执行客户端 envelope / 分类校验和审计字段白名单：

- public、personal 的收件及读取成功；删除顶层分类后必须失败。
- employer、secret、unknown、混合 employer + third_party_private 仍按原策略拒绝。
- 只在内存中使用合成数据和测试身份上下文，不保存响应或凭据，不调用模型、不启动网络监听或浏览器。

在已有锁定 Python 测试依赖及 Pi `npm ci` 完成后，从仓库根目录运行：

```sh
uv run --no-project --offline python projects/wiki-private/tests/integration/check_intake_pi_contract.py
```

需使 `PYTHONPATH` 包含本仓库 `projects/growth/src` 及现有测试依赖。脚本默认使用本仓库 Pi 包的锁定 tsx loader；可用 `--tsx-loader` 指向已经安装的同版本测试 loader，只读运行，不自行安装或构建。

以上属于真实领域 ASGI 与客户端合同验证，不是实际 Pi provider / 模型 / 浏览器 / Windows 全链 E2E。

## 本次云端检查

- 知识后端：1053 passed，2 skipped（仍缺独立核验的真实离线 ASR 模型及语音资源）；HTTP 分类及无副作用回归 21 项包含在内。
- 私有 Wiki：175 单元/DOM、类型与格式检查通过。
- 既有认证合同：local-test 17、owner 21 项通过。
- 既有领域合同：23 份实际 ASGI 响应由 12 项前端解析测试通过。
- 新增 ASGI → 未修改 Pi 客户端：11 份响应合同通过；Python Ruff 通过。
- 未运行浏览器、模型、Windows 验收、生产构建或远端写入。
