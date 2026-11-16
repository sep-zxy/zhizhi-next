# P10/P12 真实只读接线审查

日期：2026-10-23（UTC+08:00）。范围：云端源码与隔离 fixture 测试；不代表 Windows、真实模型、真实渠道端到端验收。

## P10 关系投影接口

新增桌面 GET 接口：

- `/api/growth/local/knowledge/relations/{entry_id}`
- `/api/growth/local/knowledge/relations/{entry_id}/traverse`

桌面层继续要求 `X-AhaDiff-Token`，随后由既有 `desktop_backend()` 使用原已配置的远端认证访问 `/api/knowledge/v1/relations/...`。不接收 owner 参数，不新增身份，不回退离线图、不写 outbox、不生成边。响应 `Cache-Control: no-store`。

查询字段：

- `kind` 可以重复：`navigation`、`confirmed`、`candidate`，省略代表全部三类。
- `direction`：`in` / `out` / `both`，默认 `both`。
- `visibility`：`all` / `public`，默认 `all`（仍仅本人可见/获准分类，不是绕过授权）。
- 普通邻居查询：`limit` 1–1000，默认 100。
- traverse 查询：`max_depth` 1–3，默认 2；`max_nodes` 1–200，默认 100。
- 未知参数、重复非 kind 参数、非整数或越界参数均拒绝。traverse 不接收 limit，普通查询不接收深度/节点参数。

响应保留正式后端原样 `{data, task_id, trace_id, data_classification, classification_labels}`。

`data` 包含 `nodes`、`edges`、`root_entry_id`、`projection_revision`、`visibility`、`inference: "none"`、`truncated` 和分类。traverse 额外包含 `distances`、`max_depth`。

节点包含 entry_id、revision、title、visibility 和分类。边以 `kind` 区分三类，包含 edge_id、source_entry_id、target_entry_id、source_revision、target_revision、is_fact、relation_type 和分类。结构化边另含 statement、evidence_refs、verification、review_provenance、declared_target_revision。

只有现有正式服务验证了精确引用、审核覆盖、目标版本等条件的边才是 confirmed。目标版本漂移等情况现有服务降为 candidate，verification 为 pending_verification，review_provenance 为 null。桥和前端不得提升它，也不能把所有 candidate 的原因一概写为“版本漂移”。不存在单独 drift 布尔字段。

错误保留 code、retryable、HTTP status，隐藏上游 detail，避免路径/内容泄露。PROJECTION_STALE 保留 409 + retryable。WAITING_CONFIGURATION 为 503，不用示例数据掩盖缺配置；WAITING_AUTH 为 401。

## P12 复用决定

已有真实 Git 提交列表：`GET /api/growth/local/bindings/{binding_id}/commits`，由 `serve/routes_growth.py` → `growth/history.py` 提供。已有卡片与提交关联能力。前端应消费既有列表与详情，不新增一套历史存储/代理。

尚未发现可作为职责演进图、职责迁移分数的正式接口；本次不捏造此类数据。不将普通提交次数当职责变化或掌握程度。详情错误/无绑定/无历史应显式呈现。

## 验证范围

新增测试使用桌面完整 app、真实 RemoteKnowledgeBackend、ASGI HTTP transport、正式 RelationService、隔离 Git/SQLite。认证 token 与数据均是测试 fixture，不是用户身份或真实内容。

覆盖：两层认证与其他 owner 隔离、public 过滤、三类边与已核验证据、重复 kind、邻居/遍历边界、截断、只读无 outbox/Git 变更、目标版本漂移降级、缺配置、非法路径、未知参数、上游冲突错误与脱敏。

桌面既有 catch-all 对未注册 POST 返回 404；缺 Origin 的 POST 先被原安全中间件拒绝 403。未修改这些原有规则。

### 实际测试结果

- 首轮新桥 + 原关系 + RemoteBackend：54 passed，11.49 秒。
- 扩展至全部 `tests/knowledge_platform/zhizhi`，加新桥与原关系：222 passed，30.20 秒。
- 命令：`PYTHONPATH=projects/growth/src UV_CACHE_DIR=/tmp/learning-bridge-uv-cache uv run --no-project --python /tmp/owner-test-env/bin/python python -m pytest projects/growth/tests/knowledge_platform/zhizhi projects/growth/tests/knowledge_platform/domain/test_desktop_relation_bridge.py projects/growth/tests/knowledge_platform/domain/test_relations.py -q`。
- `git diff --check` 通过。共享测试环境没有 ruff，未声称 lint 全绿，也未为此更改共享依赖环境。
- 尚待前端工作树合并后的浏览器联调与 GitHub 构建、Windows 真机验收。未运行真实模型或平台通知渠道，不能视为完整端到端全部通过。

## 本人交互 CLI 学习入口的生产启动接线

先前实际缺口：Viewer 在 `cli.py` 内生成临时 token，便携 launcher 虽已知道 Viewer loopback 端口，但仅向 Pi 注入 domain agent token。直接复用 agent token 是错误设计，学习命令因此只能显式禁用。

本次最小接线将同一个 Viewer 临时会话 token 的随机生成位置上移到便携 launcher，仅适用于现有 `owner-pi-start` 本人交互分支：

1. 父启动器在内存生成一次原有长度的随机 Viewer 会话 token，不创建 domain owner/device/长期凭据。
2. Viewer 子进程仅收到 `AHADIFF_SERVE_SESSION_TOKEN`；`serve` 入口先取出并从 `os.environ` 删除，再做工作区/Git 探测或启动其他进程。格式不合法或非 portable 注入明确拒绝，不回显内容。
3. 原有启动器等待 Viewer 健康检查成功后，仅向本人交互 Pi 注入 `ZHIZHI_LEARNING_VIEWER_URL=http://127.0.0.1:<实际端口>` 与 `ZHIZHI_LEARNING_VIEWER_TOKEN`。
4. domain agent token 仍独立；不改身份、DPAPI、预算、模型工具、模型子进程环境白名单。模型桥白名单不接收上述字段。
5. 不通过 HTTP bootstrap 抓 token，不写配置、状态文件、日志或 argv；不把这三项从调用者环境转发给无关进程。退出后无持久化 token。
6. 普通 `serve` 仍自己生成原长度随机 token。owner acceptance/model、local-test、普通订阅与 `owner-pi-once` 不注入学习能力。固定 once 仍仅使用原八项环境；原启动环境隔离会移除外部传入学习 token。

云端使用无效 fixture token 验证，未读取或使用任何实际 Windows token。Windows 实际启用与最终产物验收仍待用户开机并完成后续 GitHub 构建。

启动范围测试首批：39 passed、99 subtests passed（新会话/token启动测试 + owner Pi/model/acceptance/local-test 原回归），0.76 秒。测试通过不代表真实模型/Windows E2E。
