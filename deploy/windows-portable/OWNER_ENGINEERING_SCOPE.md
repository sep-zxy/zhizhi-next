# 单次真实工程分析入口（默认关闭）

本入口只补齐“既有 Owner + 已审真实工程输入 + 一次 Codex runner 执行”的正式接线。它不是新的通用模型授权，不修改原固定材料/Pi 额度，也不生成凭证。

代码、离线 fixture 测试通过不代表真实模型、Windows 或完整 P08/V1 验收通过。新 GitHub 候选仍需单独构建、Git 同步和实机校验。

## 固定边界

- 候选 source/release/distribution 必须完整校验；同一 release 只有一份不可替换的工程 activation。
- 复用已经登记的 Owner anchor；原 `desktop.json` 保持 `providers=[]`、`model.enabled=false`。
- 工程配置只由 `owner-engineering-start` 选择。平台仍无模型/worker；只有 Viewer 的既有桥接调用被单次 gate 包裹。
- 精确绑定项目、binding、snapshot、源码路径、commit/parent、source refs、请求 UUID、分类、完整脱敏 payload、schema、实际 provider/model 配置摘要及 ModelTask 指纹。
- 仓库必须在原获准根目录 `source-samples` 下。用 Git 对象重新计算 commit 内容；不把手写数据库记录或文件 patch 当作真实提交。
- 运行前重新验证同一请求的正常产品 analysis 状态：唯一 live run、固定 UUID、首次 attempt、精确预览/hash、项目本地处理和模型允许、云同步关闭。
- 独立外部 `data/owner-runs/<release>/engineering-receipts` 保留原子 HMAC 回执。先消费再执行。异常、超时、中断、失败、不合格输出、重启均不退额度。普通重试、schema 修正和 reanalysis 不能追加调用。
- 一次指 **一次 runner.run 委派**，不是对供应商内部网络重试次数的保证。4000 tokens 是输出目标和完成后验收阈值，**不是供应商硬性计费/消耗上限**。时间 120 秒、输入 200000 bytes、输出 262144 bytes 是本适配器边界，不能撤销已发生消耗。
- scope 回执不会保存原 prompt、模型输出、token 或新的密钥；private preview 文件包含待发送的完整脱敏文本，仍属私人测试材料，不得公开发布。
- 同一 Windows 用户已被完全控制或原获准目录被任意恶意篡改不属于本地 HMAC/文件锁的 OS 安全承诺。部分预留或回执损坏必须停止，不能删除记录“恢复额度”。

## 操作顺序

### 1. 新候选内准备真实零模型输入

按正常产品客户端建立项目、绑定、feature、精确 commit snapshot，并核对 diff。只使用同一获准源码仓库，不在 Windows 编码/构建。不复制旧数据库、身份密文或旧额度到新候选。

新的候选有独立空 run；534 的测试记录不能假定已迁移。Git 源码样本可以在获准树内保留，但新 release 的产品项目/快照应走正常接口。

将以下 **实际返回的值** 写入新候选内 `data/engineering-spec.json`，所有 ID 均为 canonical UUID。`request_id` 是为唯一一次分析预先指定的新 UUID，后续提交必须原样使用：

```json
{
  "project_id": "<实际项目 UUID>",
  "binding_id": "<实际绑定 UUID>",
  "snapshot_id": "<实际快照 UUID>",
  "source_ref_ids": ["<实际选定来源 UUID>"],
  "repository_path": "C:\\Code\\zhizhi-e2e-20261020\\source-samples\\<已核验源码样本>",
  "commit_sha": "<完整 40 位目标源码 SHA>",
  "parent_sha": "<完整 40 位父提交 SHA>",
  "request_id": "<为此次分析预留的新 UUID>",
  "classification": "personal"
}
```

不能填示意值直接运行；不允许工作区快照、未跟踪文件、跨项目 binding 或任意其它目录。

### 2. 只预览，不调用模型

使用已有 Codex 可执行文件和认证 home 的**明确路径**，不创建登录、密钥或拷贝认证文件：

```powershell
.\owner-engineering-preview.cmd --expected-source <新候选源码完整SHA> --expected-release <新候选releaseID> --engineering-spec data/engineering-spec.json --codex-exe <既有codex.exe绝对路径> --codex-home <既有Codex-home绝对路径>
```

输出 `reports/engineering-preview.json`：完整脱敏文本、scope 清单、`scope_hash`。预览只读取 CLI/home 元数据，不枚举或读取认证文件、不执行 CLI、不启动网络或模型。实际发送文本可能包含该项目已确认主题和最近用户回答，**必须审阅完整文本，不能只批准 diff**。

### 3. 精确批准后激活

先向用户明确既有订阅 Codex 路线、实际完整脱敏发送范围、一次 runner 执行及无硬性 token 消耗封顶。批准后才能执行：

```powershell
.\owner-engineering-activate.cmd --expected-source <新候选源码完整SHA> --expected-release <新候选releaseID> --engineering-spec data/engineering-spec.json --codex-exe <同一路径> --codex-home <同一路径> --approve-owner-engineering --approved-engineering-scope-hash <已审scope_hash>
```

任何字段或真实输入变化都会使旧 hash 无效。相同激活重复执行不退款，不同 scope 不能替换同 release 的激活。激活本身不启动模型、不自动修改项目 policy。

### 4. 正式启动并只提交固定请求

```powershell
.\owner-engineering-start.cmd --no-browser
```

新包装器转发 `--no-browser`；不自动发现或启动浏览器，复用已批准专用浏览器。旧 `.cmd` 包装器行为不变。

通过正常产品客户端将该项目 `model_allowed=true`，保持 `local_processing=true/cloud_allowed=false`。`cloud_allowed=false` 仅禁止云同步，**不代表模型数据不外发**。

在该工程模式的 Viewer 重新预览：provider 应为 `subscription`，descriptor 模型为固定 `gpt-6.1-sol`。确认 API 的 `approval_hash` 与 scope 内 `approved_payload_hash` 相同。若不同，不提交、更不能手改 scope。

通过该 Viewer 的正常会话鉴权产品接口提交：

```text
POST /api/growth/local/snapshots/<scope.selection.snapshot_id>/analysis
binding_id = scope.selection.binding_id
request_id = scope.selection.request_id
mode = live
provider_name = subscription
source_ref_ids = scope.selection.source_ref_ids
approved_payload_hash = scope.approved_payload_hash
approved = true
reanalysis = false
```

使用现有 Viewer 客户端会话机制，不提取 Owner secret 作为接口 token。不保证普通页面按钮会使用这个预留 UUID；会自行生成新 UUID 的普通点击应被拒绝，不能冒称“点击就能运行”。

outcome 回执只记录 runner 层状态；后续 schema 校验仍可能失败，因此 `outcome.status=succeeded` 不能当作工程分析成功。须同时检查正式 `analysis_runs` 状态、结构校验和产品结果。

一次成功也允许得到零候选。只有真实候选存在且用户确认后才成工程卡；不可直接写数据库、拼造 `published` 或借自由学习结果冒充工程成果。后续生成学习材料、提交理解、独立复核、Writer、发布全部不在本 scope 内。

结束后正常 `stop.cmd`，核对所属进程、端口及旧状态哈希。无法确认结果时先查已有回执，不重跑、不重置。

## 已验证/未验证

本增量测试覆盖真实本地 Git/SQLite fixture、正常 queue/execute/bridge 接线、合成 DPAPI、合成模型适配器、进程并发回执竞争和 launcher mocks。没有真实 Windows、真实订阅模型调用、真实 Owner 激活或完整工程 E2E 证据；这些仍是交付前独立阶段。

2026-10-26 云端离线验证：平台测试与 `test_model_opportunities.py` 合计 **1833 passed / 11 skipped**；最后兼容错误文案微调后的专项复跑 **39 passed**；launcher 回归 **38 passed**。跳过项涉及未提供的真实离线 ASR 资产、可选字幕/媒体和微信加密依赖，不计入真实渠道通过。`git diff --check` 通过。未运行 lint/type checker（当前已有测试环境未安装），未构建或发布新候选。
