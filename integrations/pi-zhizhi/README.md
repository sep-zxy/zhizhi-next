# 知枝 Pi 订阅集成

P15 新主验收入口使用独立的 [Codex CLI 原生 Provider 薄桥](CODEX_PROVIDER.md)，
固定 `launch-codex-interactive.mjs`、`zhizhi-codex-cli / gpt-6.1-sol`，不走下文旧 Pi OAuth 路线。
下文已有显式路线与后台 model-guard 保留；两条路线之间没有自动回退。

## 本次恢复与依赖精简

从持久基线 `33b4dc1` 重新应用最小清理，不能视为找回遗失提交或继承其测试结果。
删除全仓无消费者的 `ZHIZHI_MODEL_ID` 环境变量写入；模型仍通过 `--model` 精确选择，
并用 RPC `get_state` 核验实际模型，不匹配时拒绝执行。
Pi 的两个锁定运行依赖、四个生产 TypeScript 模块、许可证、审核与恢复测试仍是实际入口所需，未为缩小体积删除安全保障。

这是现有知枝领域服务的 Pi 1.3.0 扩展，不是通用代理或替代演示应用。Python 入口与精确协议见 `../../docs/implementation/MODEL_API.md`；主应用由领域 workflow 调用生成、独立复核，再由独立服务身份提交 Markdown/Git。

## 安全边界

- 固定官方 `@earendil-works/pi-coding-agent` / `pi-ai` 1.3.0，Node >=22.19.0。依赖版本及完整性由 package-lock 锁定。
- OpenAI 仅官方 `openai` provider 的 “Sign in with ChatGPT” OAuth，不支持旧 `openai-codex` 名称，不读取/提取 token，不回退 API key。
- `zai` 仅官方 Coding Plan endpoint；`zai-coding-cn` 须部署时确认账号、地区及条款资格。不得将 Coding Plan key 转为普通按量 API。
- 没有账号授权、额度耗尽或未批准数据分类时返回 blocked/waiting_auth/waiting_quota，保留任务，不偷偷切换供应商或付费。
- 后台使用独立空 cwd、禁用默认工具/MCP/扩展/skills/上下文发现、禁用会话落盘、重试与压缩，仅显式加载 model-guard.ts。模型不能 shell/read/write，也不能批准或假冒用户。
- 原生 provider 分发在调用上游前校验。不能依赖 Pi 事件异常阻断，因为运行时会捕获异常。
- CLI 选项不等同 OS 沙箱。部署仍须隔离工作用户、文件系统、出口以及独立受保护认证目录。不得共享开发者默认目录或挂载 models.json。

## 部署连接点

后台通过 `build_runner_from_config` 读取管理员配置，默认关闭。配置需要独立 agent_dir、此目录下 src/model-guard.ts 的绝对路径、已批准 provider 和实际精确 model ID。不得把凭证写入 JSON；ZAI 环境变量仅按白名单引用安全部署环境。

本变更不发起任何账号登录，也不配置新的持续授权。部署者必须通过官方登录流程单独完成授权。运行时只使用官方 `pi auth check --json --no-refresh` 检查非敏感状态；失败不输出凭证。

交互扩展入口 `src/index.ts`，必须由独立受控 Pi 启动器关闭其他工具/扩展并提供：

- `PI_CODING_AGENT_DIR`：独立且无 models.json 的受保护目录。
- `ZHIZHI_ALLOWED_PROVIDERS`：明确批准的官方 provider ID 列表，逗号分隔。
- `ZHIZHI_ALLOWED_CLASSIFICATIONS`：交互会话允许提交给该订阅的数据分类，默认 `public,personal`，第三方/雇主分类必须另行明确授权；secret 永不允许。领域响应须有明确的 `data_classification`，未知/缺失标签或更严格的嵌套分类被拒绝，不会进入模型上下文。服务端 agent 的分类 scopes 同时限制读取。
- `ZHIZHI_API_URL`：领域服务地址，只接受 HTTPS 或本机 loopback HTTP。
- `ZHIZHI_AGENT_TOKEN`：领域 agent 的有限作用域令牌，由部署环境注入，不进入模型上下文。
- `ZHIZHI_USER_TOKEN`：仅真实 TUI 人工命令使用的独立用户身份，由部署环境注入。模型工具无法取用。
- 中国版另需 `ZHIZHI_CHINA_TERMS_VERIFIED=true`；优先用 `ZHIZHI_PI_CLI_JS` 指向固定版本的 `dist/cli.js`，认证检查将使用当前 Node 可执行文件，不经过 shell。也可用 `ZHIZHI_PI_EXECUTABLE` 指定真正的 pi 可执行文件；Windows `.cmd` 启动器应改用前者。

不能用该扩展给已有任意扩展的开放式 Pi 会话补一个“安全模式”。未经审核的插件能绕过工具层，必须在宿主配置中关闭。

模型工具只提供接收预览、查询任务/条目/搜索、提交变更提案、查看审核队列/成长及雷达候选。keep/discard 需 `/zhizhi-keep` 或 `/zhizhi-discard` 的真实 TUI 确认与最新 preview_revision。`/zhizhi-review` 展示旧文/新文/风险/证据，用户批准绑定单次 nonce 和内容版本；RPC 无权完成上述命令。

只读关系工具 `zhizhi_relations` / `zhizhi_traverse_relations` 调用领域 `/relations/{entry_id}` 与其 `/traverse` 路由，不能创建关系、批准或扩大权限。支持 kinds（navigation/confirmed/candidate）、direction（in/out/both）、visibility（all/public）；邻接最多 100 条，遍历深度最多 3、节点最多 200。保留服务端 kind/is_fact/inference 与证据，不把导航、候选或多跳可达性说成新的确定事实或用户掌握程度。实际目标 revision 漂移后，旧 confirmed 关系会被领域降为 candidate。每个响应仍经过嵌套全标签分类和大小检查。

### 后台配置示例（默认不调用模型）

`examples/model-config.disabled.json` 是可被实际配置加载器读取的模板，不含凭证；`enabled=false` 会产生 `DisabledModelRunner`。部署者须替换路径和精确 model ID，并在账号资格、数据接收方及传输范围已获授权后，才可设置 `enabled=true` 和该 route 的 `account_qualified=true`。不能把填写这些字段等同于已经取得授权。model ID 应从固定版本的官方模型目录选择，不能用通配符或自动回退列表。

以下 PowerShell 7 示例针对 Linux 隔离部署，假设项目依赖已经按项目锁文件安装，且身份文件和认证目录由已授权部署流程预先提供。本例不创建令牌、不登录账号、不修改权限，也不启用公开发布。

```powershell
$repo = '/opt/zhizhi'
$env:PYTHONPATH = "$repo/projects/growth/src"
Set-Location "$repo/projects/growth"
uv run --no-sync python -m ahadiff.knowledge_platform `
  --state-directory '/var/lib/zhizhi/knowledge' `
  --identities-file '/etc/zhizhi/identities.json' `
  --model-config "$repo/integrations/pi-zhizhi/examples/model-config.disabled.json" `
  --owner 'REPLACE_WITH_OWNER_ID' `
  --provider openai `
  --allow-classification public --allow-classification personal `
  --input-classification personal `
  --host 127.0.0.1 --port 8766 --run-worker
```

- `--provider` 和 `--allow-classification` 可重复。后者省略时为 public/personal；允许的值另含 employer/third_party_private，但必须单独获得相应数据外发许可。未知及 secret 分类始终拒绝。
- 身份 JSON 顶层是 `identities` 数组，各项含 `token`、`owner_id`、`actor_id`、`kind`、`scopes`，可含 `allowed_classifications`（默认 public/personal）。它是敏感部署输入，不应提交到仓库；每个 owner 必须匹配 `--owner`，文件不得为符号链接，POSIX 下不得向组或其他用户开放。为模型工具提供有限 agent 身份，为人工命令提供独立 user 身份，不能复用用户令牌给模型。
- route 的 `allowed_classifications`、workflow 的分类选项及身份允许范围是不同检查，必须同时满足，任一更严格限制都有效。当前单标签模型契约遇到 employer 与 third_party_private 混合时保守拒绝，不丢弃其中一个标签。
- 默认 `--run-worker` 生成和复核，但不自动提交；部署明确选择自动提交已通过门禁的提案时才增加 `--auto-commit`。它不能跳过四类重大变更的人类审批，且必须与 `--run-worker` 同用。
- 正式 IM 配置通过独立 `--channel-config` 传入，参照渠道部署文档及 OpenBao 引用，不把渠道凭证写进模型配置。
- 使用 `zai` 时只可引用 `provider_env_names: ["ZAI_API_KEY"]`；中国版使用 `ZAI_CODING_CN_API_KEY` 和显式资格确认。这里是环境变量名称，不是 key 值。OpenAI 不接受 `OPENAI_API_KEY` 作为订阅替代。

### 受控 TUI 启动示例

以下是需要交互终端的实际 Pi 启动参数。模型 ID、token 和账号认证必须来自已批准的部署配置；示例只检查环境中已存在的身份，不生成或展示凭证。后台与 TUI 使用各自独立的受保护 Pi 认证目录，避免并发刷新或共享开发者默认上下文。

```powershell
$repo = '/opt/zhizhi'
$piCli = "$repo/integrations/pi-zhizhi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
$env:PI_CODING_AGENT_DIR = '/var/lib/zhizhi/pi-interactive'
$env:ZHIZHI_PI_CLI_JS = $piCli
$env:ZHIZHI_API_URL = 'http://127.0.0.1:8766'
$env:ZHIZHI_ALLOWED_PROVIDERS = 'openai'
$env:ZHIZHI_ALLOWED_CLASSIFICATIONS = 'public,personal'
if (-not $env:ZHIZHI_AGENT_TOKEN -or -not $env:ZHIZHI_USER_TOKEN) {
  throw 'Required pre-authorized domain identities are not injected'
}
$modelId = 'REPLACE_WITH_APPROVED_EXACT_MODEL_ID'
& node $piCli --no-session --offline --no-approve --no-tools `
  --no-mcp --no-extensions --no-skills --no-prompt-templates `
  --no-context-files --no-themes `
  --extension "$repo/integrations/pi-zhizhi/src/index.ts" `
  --provider openai --model $modelId
```

`--no-tools` 关闭宿主默认工具；受审计扩展仅激活自己的领域工具。`--offline` 禁止宿主自动更新/模型目录网络刷新，不等于网络隔离，也不阻止已授权的模型调用。身份/认证缺失应正常停在等待状态，而非尝试抓取浏览器 token 或切换付费路径。

## 可重复验证

在获得隔离环境构建许可后：`npm ci --ignore-scripts`，`npm run typecheck`，`npm test`。测试用 Node 的 `--import tsx`，不启动 tsx CLI 的 IPC 服务。

Python tests 位于 `projects/growth/tests/knowledge_platform/agent/`，用原项目 uv 环境运行 pytest。包含真实 subprocess JSONL 协议、SQLite/Git 提案→独立复核→提交，以及明确标记 fixture 的模型结果。扩展测试另启动已安装的真实 Pi 1.3.0，仅空认证目录执行 get_state，不发模型请求。

POSIX 环境还可显式运行真实 TUI 交互验收（从仓库根目录，用原项目 Python 环境）：

```powershell
$env:PYTHONPATH = "$PWD/projects/growth/src"
uv run --project projects/growth --no-sync python `
  projects/growth/tests/knowledge_platform/agent/pi_tui_acceptance.py `
  --pi-cli integrations/pi-zhizhi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js `
  --extension integrations/pi-zhizhi/src/index.ts
```

该脚本运行真实 Pi TUI、loopback HTTP、SQLite 和 Git，使用临时 fixture owner 与合成键盘输入，检查保留/丢弃、取消、策略过期拒批、绑定审批与读取精确旧修订。它不使用真实账号，不调用模型，不证明真人已批准。脚本不会进入普通 pytest 收集。

上述通过不等于真实订阅端到端通过。最终验收仍需要已授权的实际订阅、部署认证和受控真实来源，验证预览→明确保留→生成→独立复核→重大变更用户审批→提交→重启恢复完整链路。当前不能将未运行的真实订阅、IM/TUI 真人确认或真实账号额度场景标记为通过。

## 本轮统一 CLI 补齐（2026-10-23）

- `/zhizhi-help` 只列实际已注册能力与缺口，不测试网络、不调用模型、不显示凭据，不把 `kb …` 设计占位当已安装命令。
- 只读工具新增 `zhizhi_inbox`、`zhizhi_intake_preview`、`zhizhi_tasks`、`zhizhi_task_events`。仍受服务端 owner/classification 与桥接的当前 allowed_tools 子集约束，注册不会自动扩大已有身份授权。
- `/zhizhi-task-resume <task_id>` 与 `/zhizhi-task-cancel <task_id>` 仅本人 TUI：重新读 revision、展示状态、具体确认、提交 expected_revision。继续只是排队，仍受已有模型/审批/额度限制；取消不撤销正式提交。
- 成功 HTTP 响应须含合法 data/task_id/trace_id；失败保留固定 code/HTTP/trace/retryable，不回显服务端私密 detail。列表数组仍是合法 data。

### 本人学习命令（显式配置后才注册）

只有同时显式注入 `ZHIZHI_LEARNING_VIEWER_URL` 与 `ZHIZHI_LEARNING_VIEWER_TOKEN` 才注册；地址必须带明确端口且是 HTTP(S) loopback origin，不能含路径、凭据、查询或片段。令牌必须是已经存在的 viewer 本人身份，不能复用领域 agent token。此版本不更改启动器环境白名单、不读取身份文件、不调用 token bootstrap、不新建登录、不猜端口。配置缺失或不合规时 `/zhizhi-help` 说明未启用。

- `/zhizhi-learn-plan --entry <entry_id> --revision <n> --hash <sha256> --focus architecture`：只读预览固定架构练习模板；本人在 TUI 确认后才通过现有 Radar 建练习任务。模板不是 AI 解答或本人观点，不调用模型、不创建伪工程卡。正式源始终固定 revision/hash，后续条目更新不替换练习源。
- `/zhizhi-learn-plan-status <plan_hash>`：只读本机 outbox 投递回执，不自动重发；pending 不代表远端未接受。接受不确定时先核对，必要时本人重新预览并确认同一计划，复用持久幂等身份。没有回执不推定成功；不改变已有 outcome 原话流程。
- `/zhizhi-learn <entry_id> <revision> <sha256>`：本人确认精确正式版本，创建复用的学习上下文。
- `/zhizhi-learn-open <context_id>`：只读已有上下文与本人笔记。
- `/zhizhi-learn-note <context_id>`：用本人 TUI 编辑器输入原话，确认后按 context_version 保存；取消不写入、不自动提交。
- `/zhizhi-learn-submit <context_id>`：重新读取已保存原话、版本与 hash，本人确认后提交理解。请求只携带 context_version/note_sha256，不能让模型替写 user_text。

这些不是模型工具，也不把本人输入追加给模型上下文。使用现有 `/api/growth/local/knowledge/entry-learning-contexts` 及其 note/understanding/submissions 路由、已有学习库/outbox，不另建数据库。创建/保存使用 request_id；同进程重试同语义复用 ID，未确认写入不自动重试，跨进程应先在已有学习页核对。每个上下文同一时刻仅允许一个命令。返回 pending/unknown 不展示为正式知识已提交；真实启用与 Windows E2E 待验。

本人复核支持 `/zhizhi-review <change_id> [approve|reject|needs_evidence]`。拒绝/退回补证要求本人输入原因，读取完整旧新正文及元数据并绑定现有一次性nonce/patch/base/policy确认；取消不提交，不增加模型决定工具。

C05/C07只读补齐：`zhizhi_source` 读取保留来源投影；`zhizhi_evidence` 使用恰一个 source/external_document/learning_outcome 引用和必填 content_hash，可带source_revision与文字locator。回执的引用ID、hash、指定来源版本必须匹配；locator仅注释，不把任意定位描述当作服务端已验证摘录。仍执行已有分类门禁，不暴露原始投递包/文件路径。`zhizhi_read_entry` 可选 revision，精确读回版本不符则报错。没有现成版本列表/diff API，不宣称该部分已完成。
固定schema现为16项，当前用户身份allowed_tools子集不自动扩权；4/3/16/180调用预算不变。

计划接线为 `/api/growth/local/knowledge/radar/plans/{preview,accept}` 与只读 `plans/<plan_hash>`，复用已有领域 Radar 决策、learning task 和 durable outbox。仅支持 architecture 的确定性模板，不承诺个性化课程、多阶段规划、自动安排或模型生成。完成练习仍进入已有独立复核/回写流程；不自动登记掌握、不公开。
