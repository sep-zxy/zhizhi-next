# 独立 Pi 只读检索验收槽

本文件描述默认关闭的代码合同，不构成真实 Windows 身份派生、注册、持久激活或模型调用的批准。实施这些动作前须由主协调取得针对具体 release、固定材料、独立只读身份和预算的明确批准。现有 owner 初始化及五阶段 fixed-material activation 都不会自动启用本槽。

## 固定输入与能力

- 独立 purpose：`pi_read_search_acceptance_v1`，不修改五阶段 manifest v1。
- 精确 prompt，无结尾换行：`合成 Pi 验收 v1：请检索“正式正文与原始来源证据”，只查询当前授权知识目录，报告实际匹配条目；不要收藏、批准、修改或发布。`
- 仅 `zhizhi_search`，参数恰为 `{"query":"正式正文与原始来源证据","limit":5}`。
- 固定 `codex_cli / openai / gpt-6.1-sol / 0.163.0-alpha.11`，复用既有官方 CLI 登录，不新增 OAuth，不复制认证，不回退。
- 必须已有同一五阶段 activation 中真实生成、复核和 Writer 落盘的来源条目；当前实现不是空库假成功。只读回查现有 SQLite 和 Git，不初始化、迁移或导入任意库。
- 源绑定包含 entry ID、修订、正文摘要、标题、语义摘要、Writer commit SHA 和递归完整分类标签；每次读取按独立 agent 重做分类授权。即使正文/修订不变，来源升密也阻断。授权、尝试占用、模型前后、搜索和回执签封均重新核对。

独立 agent 固定 owner/device，只有 `knowledge:read` 与 public/personal 分类。它不是浏览器/桌面 owner 用户，不能复用 owner bearer。服务端仅通过 `PiSearchBoundary` 注册该单条来源的 search 能力，不将此 token 加入通用认证表；同一 bearer 访问其他路由、写入、批准、公开发布均拒绝。普通 user 请求继续原应用策略。

## 明确启动入口

1. `owner-pi-activate.cmd --approve-owner-pi-search --expected-source <完整 source SHA> --expected-release <精确 release ID>`。仅在独立批准后运行。校验完整发行来源及哈希、既有 DPAPI 身份和五阶段 activation，再保存独立激活回执。不启动服务或模型。
2. `owner-pi-start.cmd`。只读现有 activation，启动固定材料服务并增加受限 search 边界，再启动独立 `app/pi/launch-codex-interactive.mjs`。不能传任意 URL、路径、owner、模型或批准开关。
3. 原 `start.cmd`、`local-test-start.cmd`、`owner-acceptance-start.cmd`、`owner-model-start.cmd` 不改变默认权限，不自动启动此 Pi 槽。

平台固定 `--serve-activated-pi-scope`。Python 桥固定包内 `runtime/python/python.exe -B -s -X utf8 -m ahadiff.knowledge_platform.model.pi_bridge --owner-acceptance`。旧显式其他 provider 入口保持独立，新槽不回退至旧入口。

## 凭据与文件边界

### 固定单次 CLI 入口

`owner-pi-once.cmd` 是同一既有激活下的可测试单次入口，不接受 prompt、模型、路径或批准参数，也不自动激活。原 `owner-pi-start.cmd` TUI 不变。单次入口持有相同 launcher 锁，只启动已有受限平台的 `--pi-search-only` 服务（五阶段 worker 关闭），不启动浏览器或学习 viewer。

同一 Node 包装仅额外接受内部 `--once-json`，使用锁定 Pi 1.3.0 官方 `--mode json` 单次 agent loop。父进程先把等待门闩的 helper 纳入现有 Windows kill-on-close Job，再通过私有 stdin 管道发送源码固定 `PI_PROMPT` 的原样 UTF-8 字节并关闭输入。Pi 自己生成工具建议、执行真实 search 并完成第二轮；不得手工造 assistant/tool 消息或直接替换为模型 runner。

stdout/stderr 持续有界读取，模型文本和诊断不写日志、不回显；仅在内存核对两条 assistant 消息及一次 search 完成事件。正常进程退出还必须独立验证同 release 的签封模型/检索回执与当前源，匹配非空才写 `reports/pi-once.json` 的固定状态、计数和来源摘要。错误、超时、取消及输出过量均失败关闭并清理本任务 Job。两槽与一次 search 的既有占用规则完全不变；已有任一尝试会阻止重新执行，启动失败不是预算退款。此结果只证明 Pi 源检索，不代表解释链或最终交付。

新发行版本不能借该入口重新使用旧五阶段/Pi 预算或迁移激活。真实新版本重测仍须独立有界授权。相关测试使用合成身份与模拟 transport，不是 Windows 原生或真实模型 E2E。

- ledger MAC 与 agent bearer 使用不同的 HMAC key-derivation domain，均基于既有 DPAPI 身份仅在内存派生。不同 release/source/owner/device/正式来源/manifest 得到不同绑定。
- 派生和服务注册仍是新增能力，必须纳入真实激活批准；“未生成随机 key 文件”不等于无需批准。
- agent token 仅传给专用 Pi 子进程环境，不进入命令行、回执、源码、日志、状态报告；不传 owner/user token。平台、学习 viewer 不继承 Pi 子进程环境。Codex runner 使用自身环境白名单，剥离所有 Pi 凭据。
- 状态仅在该 immutable release 的 `data/owner-acceptance/pi-receipts/` 和 `data/owner-acceptance/pi/`；后者是隔离运行目录。新交互入口固定 `--no-session`，不保存 Pi 对话。全部属于 Git 忽略的私有运行状态，不进入产物与源码。
- 根及每级现有祖先拒绝 symlink、junction、任意 Windows reparse 属性。它是路径检查和应用约束，不声称对控制同一 Windows 用户的恶意进程构成 OS 沙箱。

## 预算、真实输出及失败

最多两个应用级模型调用、一次真实 SearchService 检索，跨进程共享不可覆盖签封回执。每次先持久占用预算再调用；失败、崩溃、结果未知不退还。相同进程的准确成功回放仅用原结果；进程重启不自动重发。该预算不是对供应商内部 HTTP 请求的计数承诺。

第一轮必须由真实模型输出精确工具建议；直接回答、其他工具或参数会阻断，不人工补造 toolCall。工具执行由真实 Pi 路径驱动；搜索只索引当前同链源安全投影，并在成功响应前签封实际结果。第二轮只接受该实际回执，不接受客户端篡改或插入其他用户输入，结果必须是实际模型文本。空检索结果如实保留，但不能算“有数据链”通过。模型未提供实际模型 ID 时保持未知，不能以请求 ID 替代证明。

模型原通知阻断、Schema/来源/秘密检查不变。数据库/Git 和回执不是跨存储原子事务；已发生副作用但回执未知时失败关闭，需对账，不能自动删除回执或重置预算。

本槽仅验证 Pi 自然语言→真实 toolCall→受限 search→模型总结。通过它不等于 Pi 写入、人工确认、全部真实模型 E2E 或 V1 交付通过。

## 测试证据边界

离线测试使用明确惰性合成身份、mock transport、真实隔离 Domain/Git、ASGI 与 SearchService，覆盖旧激活不扩权、真实 Writer 源、独立密钥目的、预算不重置、篡改/分类漂移阻断及专用子进程环境。它们不是实际 Windows DPAPI、Codex、账号或 TUI 验收。真实执行与截图另由主协调冻结候选后安排。

GitHub 候选在 Pi 锁内依赖就绪后、Pi 生产构建前强制执行 `pi-tool-schema-contract` 与 `pi-python-bridge-contract`。它们不属于 `npm test` 的通配收集，必须分别记录；Python 子进程绑定同一已安装锁内解释器并禁止下载。缺失、跳过或失败不允许发布。前者验证可信工具 JSON 与 TypeScript schema 一致，后者验证真实 Python JSONL 服务和 TypeScript Provider 互通但使用合成 runner，均不是真实模型验收。


### Verified startup deadline and safe failure observations

The private bridge hello has a separate fixed 90-second startup deadline, within the existing
180-second transport cap. It covers immutable-package verification before Python reads hello.
It does not change signed model-turn limits (120 seconds each), grants, or persistent budgets.
Windows hello-only observations of about 34 seconds demonstrate the former 10-second default
was insufficient; the earlier once failure lacks phase evidence, so its exact cause remains unproven.

After GO and prompt EOF, the managed child journey is bounded to 360 seconds:
90 startup + two existing 120-second model turns + 30 seconds for transport and exit.
This is not a deadline for the entire command: synchronous distribution verification and platform
startup happen before this child timer, and existing bounded cleanup follows it. No integrity checks
are skipped or cached to shorten startup. Startup timeout is sticky; no automatic retry occurs.

Failure reports contain only a fixed phase, cancellation flag, integer child exit code, elapsed
milliseconds, and bounded stdout/stderr byte counts. They never include exception strings,
raw pipe output, prompts, answers, environment values, or credentials. A phase is diagnostic
metadata, not proof of a model/tool call; only the existing signed receipts establish success.
Offline delayed-hello and cleanup tests are explicitly fixtures, not Windows or live-model E2E.
The observations are a pre-cleanup snapshot: cancellation can leave child_exit_code null,
readers may not yet have drained every byte, and elapsed_ms excludes final cleanup time.
The pi_process phase alone does not distinguish deadline, stop request, output limit or
platform exit; it must not be reported as a specific root cause without further evidence.
