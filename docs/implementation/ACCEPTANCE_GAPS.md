# 实施验收覆盖与未闭环事项

## 当前状态：便携验收包已在云端备齐，Windows 尚未运行（2026-10-20 05:01 UTC）

本节覆盖下方较早阶段的“当前”措辞。当前源码主线为 `0d7e2ab`；本次只更新验收记录，不改变源码、测试门槛或权限。Windows 仅运行云端预制结果，不在 Windows 安装开发依赖或编译。

- **已准备的便携包**：204,815,907 字节，Library 文件 `libfile_606c56d4aba88191a3b39d44adc34aec`；SHA-256：`f326f38b3c5ef8df821fa3b8d8f8c48d8bfa8bc18fbb12d1e74931ef91a473d0`。包已持久保存不代表目标机器已下载或运行。
- **后端隔离**：`cc8948c` 的云端回归 **942 passed / 0 skipped，32.40 秒**，含真实 Git 隔离、实际 tiny.en 识别合成语音；远端模型与网络仍有 fixture。Pi/ASR 子进程的缓存和身份路径受包内约束，ASR 使用 `-I -B`。
- **Pi 与 Wiki 便携组件**：Pi `d665c08`、Wiki `f8d113a` 均在云端实际使用 Node **22.23.3 和 24** 验证。不是 Windows 原生成功，也不是正式模型订阅调用。
- **最新完整旧后端回归**：在正确的 `projects/growth` 工作目录执行，**4300 passed / 16 原生 OS skipped / 0 failed，301.12 秒**。证据文件：`libfile_b7ccb666d98c819182612888f54da273`。16 项跳过不算通过。15、7、942、4300 等不同范围存在交叉，不能累加成总通过数。
- **必须保留的过程失败**：此前一次运行因 registry 策略停止；另一次从错误的主仓工作目录运行导致 2 项 claim_extract 失败。随后仅修正执行环境与工作目录，未改产品源码、未删测试、未降低门槛。最终结果不抹去这些失败记录，也不把环境修正说成产品缺陷修复。

### 尚未完成，不能称为全交付

1. Windows 验收任务尚未下载、解压或运行。文件传输助手遇到扩展属性兼容故障；另一下载方式不能指定保存路径，正在等待用户对一次默认下载目录的例外许可。截至 05:01 UTC，目标 ZXY 设备离线。未绕过目录限制，也没有真实 Windows 截图。
2. 正式账户、真实渠道和模型订阅 E2E 仍未通过。fixture、合成键盘、合成语音与云端组件测试不能替代这些证据。
3. ASR 资源仍是独立的 `license_pending` 组件，不计入已经完成的正式分发。实际合成音识别不能证明真实人声/渠道旅程或时间轴精度。
4. 应用管理的数据、日志、缓存与浏览器配置按包内隔离设计；不承诺 Windows 系统事件日志、杀毒记录或其他系统服务完全不写磁盘。Windows 原生 Git/NUL、junction、嵌入式 Python 和整包启动仍待验。

完整源码备份已由独立复核重新下载、恢复并通过 Git bundle verify 与 fsck，恢复的 HEAD 为 `0d7e2ab`。这证明源码可恢复，不证明 Windows 运行通过。

下一步仅在目标设备恢复在线、下载目录授权明确后接收预制包，验证校验值并执行既定黑盒验收；不在目标设备开发或编译。

## 较早恢复阶段证据（2026-10-20，非最新便携包验收）

本轮是从已核实、已持久保存的 `33b4dc1` 基线**重新应用**目录精简、ASR及Pi修复和发布内核精简；不是找回丢失的 `50d145`、`933926a` 或其测试产物。当前冻结代码为 `16dc18c`，在uv.lock对齐的环境下串行验收，不与其他重CPU测试并发。首轮 `406ca2e` 结果保留为过程记录，不能替代本版本最终结果。

独立复核者已从Library重新下载基线bundle与本轮增量，实际恢复到 `406ca2e`，Git fsck通过。这证明该版本可以从持久文件恢复，不仅是原工作区还存在。后续文档提交另行保存，不用旧丢失对象代替备份。

| 本轮证据 | 实际结果 | 证据边界 |
|---|---|---|
| **冻结最终组合回归** | `16dc18c`、uv.lock环境：`tests/e2e tests/repository projects/growth/tests/knowledge_platform`及7项字体安全测试，**541 passed / 0 skipped，18.68秒** | 与完整旧后端在同代码同依赖环境下串行执行。实际tiny.en识别合成语音，其他远端模型/页面仍为fixture；不是全部真实E2E通过。 |
| 首轮组合回归 | `406ca2e`：`tests/e2e tests/repository projects/growth/tests/knowledge_platform`，**534 passed / 0 skipped，24.93秒** | 实际tiny.en识别合成语音；页面/媒体下载和远端文本生成/独立复核仍为fixture。随后发现共享Python依赖偏离锁文件，故此行是首轮实测，不是重新锁定环境后的最终结果。 |
| Agent回归 | **101 passed** | 本轮重新执行，不与组合套件相加；不能代称正式订阅provider成功。 |
| Pi扩展 | TypeScript typecheck通过，**15测试通过** | 本轮重新执行，不与TUI路径或组合套件相加。 |
| 目录与可选AI Writer | 布局**5/5**，npm ci/typecheck通过，插件Vitest **50/50** | 可选插件不加入主平台安装；未测试真实Obsidian宿主。 |
| 冻结完整旧后端 | `16dc18c`、uv.lock环境、串行运行：**4300 passed / 16原生OS skipped，312.30秒** | 先前首轮4298/2失败在本轮整套未复现；没有降低500ms阈值或删测试。16项仍需对应原生环境，不能计为通过。 |
| Pi真实TUI | 本轮**6路径全部通过**；真实loopback HTTP与SQLite | `fixture_owner=true`、`synthetic_keyboard=true`、`model_calls=0`；不是实际用户授权，也不是正式模型订阅旅程。 |
| Viewer | typecheck及本轮授权构建通过，**521项非浏览器测试通过** | 显式排除6个需Playwright浏览器的suite，不计为通过，也不叫全部UI通过；未重试此前浏览器拒绝。gzip初始JS280,817字节，首路由368,785字节，脚本只观测、无硬上限。 |
| 裁剪后Quartz/Node | 31插件源码构建、typecheck通过，Quartz核心**123测试通过**；两次真实构建均54文件；锁环境证书43项、字体7项通过 | 最终artifact hash为 `5a420192e6994afeb7af62ec94019c679a8149b77fec66d6e3e249364a85d585`，两次只读领域门禁通过。真实浏览器导航另行验收；旧过程hash不替代最终成品。 |
| 必要字体可恢复性 | 12,659,672字节字体包持久保存，仓库保留manifest及恢复校验脚本；清空cache后从包恢复再构建成功 | 官方字体及原SIL OFL取得HTTP200，不是绕过403。必要资产不依赖易失cache；仍非公开部署或浏览器通过。 |

### 本轮已知边界

- 旧后端首轮4298通过/2失败/16跳过（323.62秒）的失败分别是 `test_benchmark_scripts::test_run_all_uses_configured_project_python`（serve read-route performance gate failed）与 `test_install::test_skill_template_curated_commands_match_cli_help[args4-snippets4]`（export preview --help 缺RUN_ID）。诊断发现依赖漂移，包括Typer实际0.27.3而锁定0.24.1；重新对齐后目标137项及最终串行完整旧套件通过。不降低500ms性能硬门槛，50ms目标仍有warning；不声称锁版本因果解决性能，也不删除首次失败记录。
- ASR使用合成语音，不是官方JFK样本、实际人声或真实抖音网络材料。越界时间片段明确不能用于定位，保留识别文本与诊断，不静默裁剪成看似精确的时间。该修复属于本轮组合回归；未证明强制对齐或时间轴精度。
- 所有操作仅云端，使用用户本轮允许的临时Bash/Python替代项目CLI；没有重试或绕过PowerShell下载403，没有使用用户本机、创建凭据或扩大权限。
- 真实账号J01/J02/J04、实际安装版J03和浏览器J05不能由上述fixture或组件测试代替；没有新增公网部署授权。仍需合法授权测试账号/素材/provider、私有可达的云浏览器与服务环境，以及对应系统的原生云安装环境。
- 下方全部内容是恢复基线内保留的**2026-10-19历史验收记录**。旧数字、hash、当时的“当前”或“已通过”只描述旧版本，不证明本轮重新应用代码已测；若与本首节冲突，以本首节为准。

## 历史基线记录（不计入本轮通过数）

更新时间：2026-10-19 20:45 UTC。**阶段审查，不是最终交付通过报告。**

冻结代码基线为 `3e0f2f10c4c379d54d6293e6d78db729c108aa30`，包含 A 原创摘要证书、B 匿名正文隔离、C 独立审核和 D `83e5ca9` 只读发布重验。本文仅更新文档，没有新增代码。

**模型生成、独立模型复核、额度与 provider 成功输出均使用显式 fixture。真实 SQLite/Git/HTTP/子进程/TUI/Quartz 的执行，不等于真实模型订阅、渠道账号、实际浏览器或安装版用户旅程已经验收。**

路径缩写：`K = projects/growth/tests/knowledge_platform/`；`S = projects/growth/src/ahadiff/knowledge_platform/`。层级：T0 为单元/策略 fixture；T1 为隔离真实组件或进程集成，模型和外部平台仍可为 fixture；T2 为已授权真实账号、平台、素材和安装产物的用户旅程。

## 1. 已实际运行的证据边界

| 证据 | 版本与实际范围 | 结果与边界 |
|---|---|---|
| **冻结组合回归** | 父协调在 `3e0f2f1` 执行 `tests/e2e projects/growth/tests/knowledge_platform`；JUnit `final-platform-3e0f2f1.xml` | **522 passed / 1 skipped，17.10 秒**。组件及合成数据集成回归，包含最新 A/B/C/D 资格链。唯一 skip 为 `K/intake/test_transcripts.py` 的真实 offline ASR，缺单独核验的官方语音样本；不是全部真实账号 E2E 通过。 |
| A 前一组合回归 | `1da5f3d` 上相同新平台范围 | **502 passed / 1 skipped，16.21 秒**。A 实跑，尚未合 D `83e5ca9`；仅作历史证据，不再称当前最终结果。 |
| A 领域回归 | `956e9d1`，`K/domain` | **152 passed**，含 40 项原创摘要证书正反例；ruff 通过。真实对象、SQLite、Git、只读 resolver，没有真实模型调用。 |
| B 摄取回归 | B 报告 `9966904`，已合冻结基线 | **157 passed / 1 ASR skipped**；其中 30 项匿名正文测试含真实 Domain broker、私人附言分离、分类去重隔离与重复 keep。平台与模型响应仍为 fixture。 |
| C 新摘要及公开增量 | `K/agent/test_domain_publication_integration.py`；C `91dc168`，已合冻结基线 | 真实 Domain→独立复核→Git→resolver 的新摘要和已有 public 增量通过；旧内部 metadata 保留另有 unit 回归。生成和审核输出为 **FixtureModelRunner**。 |
| D 新资格目标回归 | D 报告 `83e5ca9`，已合冻结基线 | **84 项 D+C 目标测试 passed**，包含真实 C helper→Git→只读 resolver；撤回、来源/策略/证书/正文漂移拒发。不与 522 项相加冒充一次更大运行。 |
| 原有后端回归 | 父协调完整旧测试范围 | **4300 passed / 16 原生 OS skipped**。16 项仍需相应原生环境，跳过不算通过；旧回归与新平台合成集成范围分别报告。 |
| 真实 Pi TUI | C 报告锁定 Pi 1.3.0 + loopback HTTP + SQLite/Git，六条交互路径 | keep 取消/确认、discard 确认、重大复核取消、策略变化拒批、nonce/hash 绑定批准、旧 revision 读取等通过。使用 fixture 身份、临时本地 token、合成键盘输入；**零模型调用，不是用户真实批准或订阅推理**。 |
| **新资格双 Quartz 构建** | D 实现 `83e5ca95`，父确认 `3e0f2f1` 为其纯合并；知识 Git revision `1d3f58e62c17cfe1bb76643b74f5dedb0b5d218e` | **两次真实离线构建、最终只读账本重验与成品扫描通过**，每次 54 文件；artifact hash 均为 `0a44b1adb5daafb5d7b857ddb5b06ba805fe3f6b7b562123c7ad2678675f916f`。审查者已只读核对 `acceptance-d-certificate/gate-one.json` 与 `gate-two.json`，revision/input/policy/public manifest/artifact hash 完全一致。内容为隔离合成来源、模型为 fixture，无公网部署；浏览器导航仍待验。 |

较早 `e0e46ca9` 的 402/1、`96b8f6b` 的 334 项以及旧版 60 文件 Quartz hash 都是历史结果，不替代上表当前版本证据。

本地持久性证据包括真实服务子进程重启、三个 SIGKILL 故障点后单次提交、三进程 quota 到期自动恢复仅复核而不重生成、索引故障后重建、备份恢复后真实搜索命中。这些已是 T1 实测，不再列为未实现，但不能代称 T2。

用户已允许本次隔离云电脑构建/测试；未授权公网部署、真实凭据迁移、创建账号权限或付费模型调用。测试目录名含 `e2e` 不自动提高层级；假 RPC、MockTransport、伪平台 HTML、模拟 OpenBao 与 FixtureModelRunner 必须保留标识。

## 2. 当前用户闭环状态

1. **默认公开的原创摘要路径已补实现和 T1 证据。** RAW 权利仍 unknown/private，私人附言不进入净化正文输入。新原创事实摘要需独立七项审核、完整来源/输入/候选/政策/任务证书绑定；Writer 不改已审正文。显式 private、学习原话、雇主/私密/secret/unknown 输入及已有 private 条目不能自动升级。D 只把净化 artifact 交给 Quartz，构建后重新核对真实只读账本；当前撤回/删除阻断旧 Git 版本发布。正式模型内容质量不能由 fixture 证明。
2. **雷达、三类关系和只读工具已完成本地集成。** 既有学习页面可提交开放原话，outcome/建议/条目绑定，双设备唯一成果。导航、逐条证据和可信复核覆盖的 confirmed、待验证 candidate 分开；目标 revision 漂移降候选，索引可重建，无掌握分和传递事实推断。认证 HTTP/Pi 只读入口有回归，不再标为“接口尚未实现”。浏览器可达测试环境、实际安装和正式模型旅程仍未验收。
3. **真实渠道及正式模型订阅仍未验收。** 未有官方微信资格/合法发送者、Telegram 授权绑定、真实小黑盒/抖音素材和正式 provider 正向组合证据。B 早期探测为 DNS `EAI_AGAIN`，未取得 HTTP 正向正文。原生 keep/discard/回执代码与 fixture 已有，不能代称真实账号成功。
4. **真实语音 ASR 仍跳过。** 字幕/WebVTT、PyAV 解码、faster-whisper 子进程已实现，官方 tiny.en 固定版本/hash 模型已实际加载。官方语音样本下载 403 后停止；合成 WAV 解码和模型加载不是实际语音识别正例，更不是抖音 J02。
5. **浏览器、桌面安装和原生 OS 旅程仍待验。** UI typecheck/API、真实 HTTP 与 Pi TUI 合成输入不等于浏览器实际可达、安装、首次真实作答、离线重放与回填。原生 OS 的 16 项旧测试仍需对应环境。
6. **quota T1 恢复缺口已关闭。** 已跑“复核 waiting_quota→重启→维护到期恢复仅复核→单次 Git 提交→再重启零调用”，保留同一 change checkpoint。正式 provider 等待/恢复仍未验，不得人为耗尽额度或调用按量 fallback 造绿。
7. **真实秘密服务恢复仍缺证据。** OpenBao 精确引用、无重定向与脱敏有 fixture；sealed 导致启动不可用不同于模型 OAuth 的 task.waiting_auth。没有真实 sealed/unseal 后运行中自动恢复全链证据。
8. **确定性发布构建已通过，J05 浏览体验仍未过。** 新证书双构建和最终账本重验已从“进行中”更新为成功。下一项是浏览器真实分类、条目、公开来源和中文搜索；无需也不得擅自公网部署。

### 不扩大本轮范围

v0.3 的 `docs/execution/00-范围与决策.md`、03、06 明确允许首版 FTS5/中文 n-gram，并暂缓 embedding/向量混合召回。它不是必交缺口，也不能宣称已实现；不新增图数据库、掌握分或其他范围。

## 3. E01–E20 逐项映射

| ID | 实际测试路径与层级 | 已证明什么 | 未证明/下一验收动作 |
|---|---|---|---|
| E01 渠道重放 | `K/intake/test_channels_routes.py`、`test_supervisor.py`；T0/T1 fixture | 发送者绑定、durable cursor、消息/任务幂等、原生 keep/discard 和回执控制流 | 真实官方微信/Telegram 收件及用户实际回执；合法账号、授权配置与网络正例 |
| E02 未 keep | `K/intake/test_service.py`；`K/domain/test_sources.py`；`tests/e2e/test_knowledge_flow.py` unretained 用例；T1 | 未收藏无永久 Source/Wiki；到期清原文、preview及历史回执副本，敏感分类也可受限清理 | 真实小黑盒长帖进入预览的正向材料路径 |
| E03 抖音不完整 | `K/intake/test_extractors_cache.py`、`test_transcripts.py`、`test_service.py`；T0/T1 | 描述不冒充字幕/全文；partial/needs_input 明示；字幕、解码、ASR 路径已有 | 真实 ASR 样本仍 skipped；实际抖音完整媒体正例未过 |
| E04 重复 keep | `K/domain/test_sources.py` duplicate/content_identity；`K/intake/test_service.py` concurrent_keep；`test_external_document.py`；T1 | 一次 Source/task，preview CAS、首份 RAW 保留；不同分类/显式private不复用clean来源 | 真实平台短长链归一化后的 J02 正例 |
| E05 常规增量 | `K/domain/test_writer_reviews.py`；`K/agent/test_domain_generation_integration.py`、`test_domain_publication_integration.py`；`tests/e2e/test_knowledge_flow.py`；T1 fixture模型 | 真Git/SQLite提交一次；L2独立复核；新原创摘要和已有public增量可获具体证书，RAW不变 | 正式订阅生成、独立复核和真实材料质量 |
| E06 四大风险 | `K/domain/test_writer_reviews.py` all_four_major/semantic_rewrite/deletion；`K/agent/test_generation_review.py`；C真实TUI报告；T0/T1 | AI不可越权；用户nonce/exact patch/base；墓碑恢复；TUI取消/失效/批准展示完整差异 | 合成键盘/fixture身份不是实际用户批准或正式provider旅程 |
| E07 无原话 | `K/domain/test_writer_reviews.py` personal_understanding；`K/agent/test_generation_review.py` fabricated_personal_view；T0/T1 | 无真实LearningOutcome原话不得冒认“我的理解”；公开证书不洗白学习原话 | 安装版首次真实作答与正式引用归属 J03 |
| E08 身份冒充 | `K/domain/test_http.py`、`test_ledger.py`、`test_classification.py`；`K/intake/test_service.py`；`tests/e2e/test_knowledge_flow.py`；T0/T1 | 401/403、owner隔离、agent无keep/apply/人工审批、分类与幂等重放守卫；HTTP自报证明拒绝 | 正式部署已授权身份配置与真实账号验证 |
| E09 审批失效 | `K/domain/test_writer_reviews.py` review_invalidated/nonce；`test_publication_certificate.py`；`K/zhizhi/test_publication_certificates.py`；T1 | base/patch/policy失效；来源/输入/正文/引用/内部patch/证书漂移拒发；当前撤回阻止历史版本 | 实际部署policy须来自可信配置，不能从artifact自报；真实运营变更待验 |
| E10 Git/DB崩溃 | `K/domain/test_writer_reviews.py` real_process_kill/ancestor/concurrent_processes；T1 | 三处真实SIGKILL后一次Git提交/事件；后续HEAD、并发恢复正确，已含冻结组合回归 | 原生环境与实际部署故障演练仍需单列 |
| E11 索引故障 | `K/domain/test_writer_reviews.py::test_committed_body_survives_projection_failure_then_rebuild`；`K/zhizhi/test_search.py`、`test_real_domain_bridge.py`；T1 | FTS故障不回退正文；恢复后同revision命中；旧目录标stale_index；关系投影可重建 | 浏览器/安装客户端错误展示和恢复体验 |
| E12 额度耗尽 | `K/agent/test_model_runner.py`；`tests/e2e/test_workflow_waits.py`；`K/domain/test_quota_resume.py::test_real_process_restart_auto_resumes_review_without_regenerating`；T0/T1 | waiting_quota、零收费fallback、到期保留checkpoint；三真实进程仅复核并提交一次 | 正式订阅额度等待/官方恢复窗口；不人为消耗额度造测试 |
| E13 auth/秘密 | `K/agent/test_model_runner.py`；`K/zhizhi/test_model_bridge.py`；`tests/e2e/test_secret_boundary.py`；T0/T1 fixture | OAuth失败/等待、秘密引用allowlist、无重定向/日志泄漏/收费回退 | 真实OAuth与OpenBao sealed/unseal运行中恢复 |
| E14 SSRF/注入 | `K/intake/test_normalize_fetch.py`、`test_extractors_cache.py`、`test_external_document.py`；Pi extension/模型policy；T0/T1 | 逐跳/DNS/IP固定、大小超时、防内网；附言与正文隔离；认证/能力URL/限制信号阻断；材料不能授工具或证书 | 正式provider对抗素材；不能宣称任意prompt injection已消除 |
| E15 无模型发布 | `K/zhizhi/test_publish.py`、`test_publication_certificates.py`；D双Quartz构建/gate；T1 | 新证书净化artifact确定性投影；固定知识revision两次真实成品54文件hash一致、模型网络关闭、只读资格重验 | 浏览器真实导航；构建成功不等于J05全过 |
| E16 公开泄漏 | `K/domain/test_publication_certificate.py`；`K/zhizhi/test_publication_certificates.py`、`test_publish_artifacts.py`；D实际成品gate；T0/T1 | 自报资格不授公开；private/deleted/撤回/漂移拒发；Quartz不读RAW/私聊；两次成品扫描通过 | 合成指纹不是已授权真实存量库扫描；浏览器待验；未公网发布 |
| E17 离线冲突 | `K/zhizhi/test_backend.py`、`test_real_domain_bridge.py` stale_desktop_original；`K/domain/test_writer_reviews.py` external_formal_edit/unindexed_file；T1 | 双方请求/草稿保留、外部Git hash漂移阻断、无force/reset | 安装版离线编辑与既有Obsidian/Git同步、冲突展示；bare库不是用户同步工作树已接通 |
| E18 雷达回填 | `K/zhizhi/test_real_domain_bridge.py`；`K/domain/test_learning_task.py`、`test_relations.py`；`K/agent/test_domain_generation_integration.py`；T1 | 证据问题→复核→Git→Radar；一次接受/成果回填；三类关系/目标revision/可信覆盖/有限多跳；不造掌握分 | 浏览器/安装新入口与正式模型同条目闭环；作者问题不冒充用户薄弱项 |
| E19 备份恢复 | `K/domain/test_writer_reviews.py::test_backup_restores_git_objects_ledger_and_sources`；`test_ledger.py::test_sqlite_backup_includes_wal`；T1 | 一致DB/Git/对象快照；恢复后搜索同entry/revision；不覆盖旧备份 | 安装客户端重连；没有生产备份操作授权 |
| E20 旧模型入口 | `K/zhizhi/test_model_bridge.py`、`test_architecture_focus.py`；旧provider factory统一适配；T0/T1 | 工厂经ModelRunner、缺配置fail-closed、strict_local不降级、等待无fallback；旧后端4300通过 | 安装产物全部学习/聊天/反馈/复习UI逐入口记录零旧收费请求；原生OS16项仍skip |

## 4. J01–J05 用户旅程判定

| 旅程 | 已有最接近证据 | 当前判定与缺口 |
|---|---|---|
| J01 微信→小黑盒→keep→Pi引用 | 官方签名/发送者/渠道交互fixture、正文fixture、HTTP→Git→search、Pi受限工具 | **未通过。** 缺合格官方账号、合法发送者、真实小黑盒正文与正式provider组合证据。 |
| J02 Telegram→抖音→字幕/ASR→去重条目 | Bot游标/短长链/去重fixture；字幕/解码/ASR路径及离线模型加载 | **未通过。** 缺真实授权Bot、平台素材/网络、真实ASR样本和正式模型正例。 |
| J03 安装知枝→首次作答→outbox→正式回填 | 桌面配置、真实ASGI RemoteBackend/outbox、开放作答/雷达HTTP、UI typecheck/API | **未通过。** 浏览器可达测试环境及实际安装旅程仍无通过证据；合成作答不替代真实安装操作。 |
| J04 正式Pi订阅→工具→任务终态→重启查询 | 真实Pi offline get_state、TUI合成输入、loopback重启、受限工具与分类守卫 | **未通过。** 正式订阅provider正向生成和独立review缺失，fixture模型或零模型调用TUI不可替代。 |
| J05 固定revision→无模型→Quartz可浏览 | 新证书真实只读resolver；固定revision双Quartz构建、54文件成品扫描/hash一致 | **尚未全过。** 确定性发布构建阶段通过；浏览器真实分类/条目/安全来源/中文搜索仍待验。不得为验收擅自公网部署。 |

## 5. 关闭验收所需的最小顺序

1. 保存冻结代码 `3e0f2f1` 的522/1、旧后端4300/16和新证书双构建的独立报告，不合并不同层级计数，不把skip改为pass。
2. 在浏览器可达的隔离环境检查真实分类、条目、来源、中文搜索；执行桌面安装、真实学习、断网outbox重放和回填。原生OS用例使用对应环境。
3. 仅在具备合法已授权账号、素材和范围后执行T2；至少一个正式订阅provider全链不能由FixtureModelRunner替代。不得把微信/Telegram改成HTTP冒称通过，不绕拒绝、新建权限或用付费端点补绿。
4. 证据逐用例记录PASS/FAIL/BLOCKED/SKIPPED、执行版本、trace与命令，不含凭据和私人原文。只有所有必需项无失败/阻塞/跳过、同版本核心旅程可重复，才可能满足用户“端到端测试完全通过”的交付标准。

## 便携阶段持久证据索引（2026-10-20）

- 完整源码 `0d7e2ab`：Library `libfile_89fac629914481919f0cf97f4505d6e3`；SHA-256 `a25677c135230ce40b3567e481da03a33cc03d66566d2b62774318bb116016e5`。独立下载恢复并 verify/fsck 通过。
- 正确工作目录的完整后端证据：`backend-correct-cwd-offline-0d7e2ab-evidence.tar.gz`，Library `libfile_b7ccb666d98c819182612888f54da273`；SHA-256 `fce00a51e8025a456957afe2034bf3bf3712b7e66ba9a967dc257d0ed9b63b8d`。含完整 JUnit/log、两项 claim 先验和原始 benchmark JSON；执行起止 HEAD 均为 `0d7e2ab`，工作树 clean。
