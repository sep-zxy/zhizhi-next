# 统一收件实现边界

`create_intake_routes(domain, model_runner=None)` 注册以下已认证接口。写操作需要
`Idempotency-Key`；身份仅来自 `request.state.actor`，不会采信请求体的 owner 或批准字段。

- `POST /api/knowledge/v1/intakes`：`text`、`url`，可选 `requested_action: preview`。
  当前不接收附件文件，非空 `attachment_refs` 明确返回不支持。
- `GET /api/knowledge/v1/intakes/capabilities`：逐项能力，不把 fixture 通过作为平台实测。
- `GET /api/knowledge/v1/intakes/{intake_id}`：预览与版本，不暴露缓存路径。
- `POST /api/knowledge/v1/intakes/{intake_id}/decision`：`decision: keep|discard`；收藏必须
  提供当前 `preview_revision`，且已认证身份必须为用户。

HTTP 接收只写入统一 Domain 账本及 `intake.preview` 队列。后台使用
`IntakeService.process_claimed(task, actor=worker)` 或 `process_next`；不另建数据库。
已解析正文的模型等待重试复用同一 RAW，不重复抓取，不把等待状态改称成功。
模型仅在可信服务配置提供 `allowed_provider_ids` 时调用，来源文字是数据而非工具指令。
应用可传 `create_intake_routes(domain, runner, intake_service=intake)`，让 HTTP、队列 worker
及渠道共享同一个已配置服务实例；若实例绑定不同 Domain 则拒绝装配。

## 收藏、幂等与保留

收藏前的 JSON RAW 仅在 Domain 指定的私有 intake cache。只有用户显式收藏后，Domain
才复制至保留对象目录、创建 Source，并在同一事务中创建一个 `knowledge.generate`
任务。重复收藏及相同规范链接/正文重复分享复用 Source 和任务。成功后删除临时副本。
丢弃/过期不创建 Source，清除缓存和产品可读取的原文及幂等重放内容。不承诺 SQLite
旧页、WAL 或备份的取证级安全擦除。部署需要周期调用 `cleanup_expired`。
清理先调用 Domain 的 owner-scoped `expire_intakes`，覆盖尚未领取、没有缓存文件的
received 收件，再扫描清理过期/丢弃/已保留的临时副本，取消未完成的预览任务。

## 解析与外网约束

SafeFetcher 每次跳转重新验证全部 DNS 结果并固定连接 IP，同时保留 TLS 主机验证。
默认 4 MiB、25 秒总时限、4 次跳转，禁止私网/混合 DNS、非标准端口、代理环境、
Cookie、登录、JavaScript、压缩响应和 HTTPS 降级。不会为通过测试而放宽限制。

小黑盒支持公开文章与明确匹配帖子的结构化正文。抖音支持公开 hydration 中单个作品的
标题、文案、图片/视频引用；视频文案不是口述正文，没有媒体下载、ASR 或字幕即明确
`partial`/`needs_input`。这不是“所有平台/所有内容均支持”的声明。解析失败不会生成摘要。

2026-10-19 在隔离运行环境用实际 IntakeService、DomainService、SafeFetcher 读取
以下公开样本，三个均在系统 DNS 解析阶段返回 `gaierror` / `EAI_AGAIN`（errno -3），
没有收到平台 HTTP 响应。真实平台正向验收尚未通过；不能把环境阻塞称为平台拒绝。

- 小黑盒：`https://xiaoheihe.cn/app/bbs/link/185082873`
  公开出处：`https://www.bilibili.com/video/BV1wagy6xEM2/`
- 小黑盒：`https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=7b35719116f7`
  公开出处：`https://github.com/sokoko-org/nonebot-plugin-parser-lite/issues/6`
- 抖音：`https://www.douyin.com/video/7601897831745506560`

## 官方渠道与依赖

Telegram 使用标准 getWebhookInfo/getUpdates 长轮询，仅允许服务配置中的准确
账号、chat、sender 映射；先持久收件再推进 offset。已有 webhook 时拒绝启动，不删除
其设置、不创建 bot，不默认回复或发送消息。每个账号只运行一个 poller。

微信仅支持有资格且已确认 API 管理权限的官方微信客服，不是个人微信机器人或群发送
webhook。默认禁用。可选 SDK 是官方 PyPI 的 `wechatpy[cryptography]==1.8.18`，仅包装
经核对的 `wechatpy.enterprise.crypto.WeChatCrypto`，未供应 SDK 时明确报不可用。
账号凭据由已有安全服务注入，不新建或保存凭据。

仅安装启用后的精确路径 `/api/knowledge/v1/channels/wechat-kf/callback` 可以免 bearer
认证，适配器自行校验签名、时间窗、解密与主体，再持久化 `channel.wechat_kf.sync`
任务。后台调用 `WechatKFAdapter.process_claimed`。sync token 仅存在内存，不进账本。
API 响应有大小/时间限制，httpx 官方请求日志会脱敏含令牌的 URL。

核心网络层使用 Python 标准库及项目已有 httpx/Starlette；无额外解析器运行时依赖。
未集成未经验证的签名、设备指纹、Cookie 获取脚本，未盲目执行第三方下载器。
协议测试明确区分合成 API/model fixture 与真实 SDK：已安装的 wechatpy 1.8.18 实际
执行合成密钥加解密、签名、URL challenge、CorpID 不匹配拒绝回归，未联网。
这些测试不代表真实账号、真实消息发送、付费模型或真实平台验收已通过。

## 可运行生命周期装配

`ChannelSupervisor` 是真正调用已配置适配器的生命周期组件，不仅是测试协议定义。
只注入已存在且获授权的客户端实例；这里不会加载、注册或保存凭据。以下为应用中的
最小装配结构，不是可用凭据配置。

```python
from ahadiff.knowledge_platform.channels import ChannelSupervisor

supervisor = ChannelSupervisor(
    domain,
    actor=service_actor,  # 同 owner，knowledge:read/write 与 tasks:work
    telegram_pollers=configured_telegram_pollers,
    wechat_adapters=configured_wechat_adapters,
)
stop = asyncio.Event()
background = asyncio.create_task(supervisor.run_forever(stop))
try:
    yield  # ASGI lifespan 服务期
finally:
    stop.set()
    await background
```

Telegram 启动先检查 webhook 冲突；长轮询失败使用有界指数退避并持续恢复。微信领取
已有 `channel.wechat_kf.sync` 任务，用账号及客服 ID 精确匹配，续租并提交成功/等待结果。
认证问题持久化为 `waiting_auth`；其他临时错误为带 `next_run_at` 的 `retry_wait`，达到
Domain 最大尝试次数后停下，不无限重试。来源正文与错误异常全文不会写入状态。
`supervisor.health` 及独立 `supervisor:<channel-key>` Domain 状态仅含脱敏类别，不覆盖
消息 offset/cursor。停止会取消并等待子轮询，未完成任务靠原租约到期后恢复。
微信回调路由与本 supervisor 需一起装配，否则只接收到队列而不会同步正文。
一个 owner 的每个账号只配置一个接收器，暂不声称支持多副本水平扩展。

## 显式启用的原生回执与决定

默认只接收。只有装配方在已经获得账号发送授权后显式传入
`ChannelSupervisor(..., native_notifications_enabled=True)`，才建立
`NativeNotificationDispatcher`，并启动消息回执任务。构造本身不发送消息；测试使用
合成 Bot API / 微信客服 API，未向真实账号发送任何消息。

Telegram 仅给已绑定的本人私聊发送收件、预览与状态模板，提供带 intake ID 和预览版本
的收藏/丢弃按钮；再次核对 callback 的账号、chat、sender，旧版本不能收藏。
微信客服使用官方 `kf/send_msg` 文本接口，预览提供“知枝收藏 <ID> <版本>”或
“知枝丢弃 <ID> <版本>”的独立命令。普通正文不会被当成决定。微信会话窗口、配额与
账号资格仍由官方 API 决定；拒绝时等待认证、额度或人工处理，不绕过限制。

仅可信接收器可登记原始消息绑定目标；HTTP 和模型不能传入任意接收者/消息正文。
任务只保存模板类型与 intake 引用，从本人的账本读取对应内容，保护类别正文不回传。
发送前持久化 dispatch_started；进程中断或网络结果不明时标记 DELIVERY_UNCERTAIN，
即使任务被重新排队也不会盲目再次发送。API 成功只记 api_accepted，不声称用户已阅读
或 Telegram/微信提供严格 exactly-once 投递。重复决定使用稳定幂等结果。

`IntakeService(..., input_classification="personal")` 是保守部署默认值，可配置得更严；
单条 `data_classification` 与嵌套分类标签会合并，不能通过声明 public 降级部署或证据
限制。employer 与 third_party_private 同时出现时保守阻止模型调用。RAW 始终私有；
抓取成功不产生转载授权，公开意图与派生内容资格须由独立审核与发布门禁决定。

## 字幕与离线音轨转写

页面解析器收集标准 HTML track 字幕，以及明确的 audio/video src 或作品媒体引用。
`MediaTranscriptProcessor` 先经同一每跳 SSRF 安全层下载 VTT/SRT，再调用维护中的
webvtt-py 0.5.1；字幕限制 512 KiB、5000 cues、10 万字符，保留毫秒时间戳和原始文件。
没有字幕时，可显式注入 `LocalWhisperASR`：

```python
from pathlib import Path
from ahadiff.knowledge_platform.media.transcripts import LocalWhisperASR, MediaTranscriptProcessor

intake = IntakeService(
    domain, runner,
    media_processor=MediaTranscriptProcessor(
        asr=LocalWhisperASR(Path("/srv/verified-models/whisper-tiny")),
    ),
)
```

ASR 是可选的 faster-whisper 1.2.1 + PyAV/CTranslate2 本地实现，不借模型订阅调用付费
语音 API。模型目录必须是已有绝对路径，必须含 model.bin、config.json、tokenizer.json，
避免库缺 tokenizer 时隐式联网。子进程启用离线模式、禁遥测、不继承应用令牌/代理；
不接受模型 ID 或 URL，不自动下载。缺配置在 preview.document.metadata 中明确记录
ASR_NOT_CONFIGURED / waiting_configuration，预览任务保持 waiting_input。

媒体下载至多 8 MiB，每次 20 秒；默认最多 300 秒音轨、90 秒进程墙钟时间，配置上限
为 600 秒音轨/300 秒墙钟。解码只接受内存字节与固定格式，禁止网络协议/播放列表；
PyAV 解码按重采样样本数限制时长，进程超时/取消会终止并等待。Linux 子进程另有
4 GiB 地址空间和 CPU 上限。这是资源隔离，不等同于完整操作系统安全沙箱。

下载字节、SHA256、时间戳与转写引擎进入同一私有 RAW bundle，用户收藏前不保留来源。
失败后明确重排任务可复用缓存媒体继续，不重复抓已捕获页面/媒体。字幕或真实 ASR
成功仍显示 body_complete=false / visual_coverage=not_processed，不能把音轨变成“已
理解完整视频画面”。字幕测试用人工编写文件但执行真实解析库；解码测试执行真实 PyAV；
真实 ASR 测试只有提供经验证的模型与官方语音样本后才执行，缺文件明确 skip，不能把
fixture 或静音解码计为模型正向验收，也不代表抖音平台 live E2E 已通过。

2026-10-19 另在隔离测试目录核验了官方 Systran/faster-whisper-tiny.en 的 MIT 模型卡、
revision `0d3d19a32d3338f10357c0889762bd8d64bbdeba` 与模型 LFS SHA256
`1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a`。
CPU/int8 的真实权重加载通过，不计为语音识别正例。官方 OpenAI Whisper jfk.flac
固定 Git blob 下载返回 HTTP 403，原因未判定，已停止该下载，没有换源绕过；环境也未
安装 espeak/espeak-ng，故真实语音 ASR 正例仍阻塞。子进程明确关闭 UCX 可选 VFS
诊断服务线程（UCX_VFS_ENABLE=n），不为它申请或扩展 socket 权限。

## 公开意图、获取事实与资格门禁

默认意图是生成可公开的知识，而不是公开 RAW。收件 POST、预览 GET 和 keep 响应的
`data.publication` 显示 publication_intent、当前 eligibility、reason_codes/reason_details、
RAW 的 private/unknown 状态，以及尚未核验发布完成。原生渠道的收件、预览、收藏和
知识处理通知使用同一只读状态，明确写出“公开意图：公开；当前资格：待审核/受阻”和
原因，不让收藏成功被误认为已经公开。若 knowledge.generate 已绑定 ChangeSet/Entry，
读取领域权威记录的实际意图与资格；B 不修改任何资格、批准或发布门禁。
明确 private 意图的正常结果显示 eligibility=not_requested、publication_eligible=false，
原生回执说明“已按明确私有意图保留，不自动公开”，不误报失败；私有意图覆盖历史
eligible 标志。公开候选则分别使用 pending_review、blocked、eligible 状态。

`preview.acquisition` 及 retained Source 的 descriptor.metadata.acquisition 记录实际
获取观察。只有 SafeFetcher 的匿名成功响应确实解析出文章/帖子正文，且整条重定向链
没有已识别的 token、签名或口令查询参数，才记 publicly_accessible=true、authenticated=false。
仅元数据、失败、粘贴正文、携带能力令牌的 URL 或未验证的替代 transport 均不标为公开
正文；canonical_url 去掉已识别的敏感参数。此观察不证明无隐私、不宣称抓到完整动态页面，
也不授予转载许可。原分享上下文、分类、RAW rights=unknown 与 visibility=private 不变。

### 私聊上下文与外部正文分离

工作器把无 original_text 的 NormalizedShare 传给外部解析器，解析器从入口就看不到
用户附言；私聊原话、账号路由仍只在私有收件/RAW 中。对符合边界的完整外部正文，
工作器另外生成 external_document_manifest，私有缓存保存它，但 HTTP 和 preview 不
接收/暴露这个可信写入凭证。收藏时从经过 SHA256 校验的缓存读取，以 Python-only
`retain_source(..., external_document_manifest=...)` 传给领域，不放入 descriptor 或客户
metadata。领域在同一收藏事务独立复核 RAW 正文和 hash，再创建独立正文记录；只有
领域的 `read_external_document(source_id, actor=...)` 可以提供这份干净候选正文。

manifest 只包含 body_text、精确 UTF-8 SHA256 content_hash、canonical_url、final_url、
captured_at、fetcher_version、extractor_version、text_basis、无认证获取事实、上下文已
分离事实，以及 external_article/external_post 类型。privacy_assessment 固定为
not_assessed，observed_restrictions=[] 仅表示未观察到列明限制，不是技术文档分类、
隐私安全结论或转载许可；没有 title、author、description、媒体、用户附言、发送者或
路由字段。私有 RAW 仍为 rights=unknown，绝不转成 summary_allowed。

此初版候选严格要求所有实际抓取跳转及规范地址都是 HTTPS、无 query/fragment/userinfo，
没有明显认证/私有/令牌或长混合不透明路径；带查询参数的正常公开文章也可能被保守
拒绝候选，但仍可私有收藏。已观察到 noai/noindex/noarchive/nosnippet、private 缓存、
密码输入框、付费墙标记时不进入候选。employer、third_party_private、secret、unknown
收件不会因网页可访问而降级。视频描述、字幕/ASR 和粘贴正文也不走完整文章候选路径。

领域确立独立正文后，回执显示“原创摘要资格及独立审核待完成”，而不是把 RAW 的
私有分类误当作每一份原创摘要的永久禁止。候选尚无公开资格：A/C 仍须只用干净正文
和已有 public 知识生成原创摘要，独立核验隐私/雇主信息、事实、引用和非长段照搬，
绑定精确版本并由领域重验。B 不改 eligibility 门禁，不会因可访问而自动公开材料。

`test_external_document.py` 使用合成 HTTP 字节和真实解析器/缓存/DomainService，验证
带个人附言的文章能产生与附言分离的 broker 正文，以及能力地址、限制信号、受保护
分类、HTTP 自报证明均不能进入候选。它不是平台 live 抓取、真实模型或线上发布证明。
