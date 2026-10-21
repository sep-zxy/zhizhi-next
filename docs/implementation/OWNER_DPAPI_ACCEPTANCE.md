# 独立 Windows operational owner 验收路径

状态：代码与合成验证，尚未在 Windows 初始化真实身份或验证 DPAPI。以下初始化开关只是程序防误触门禁，不等于用户同意。操作者仍必须先取得明确的身份创建、目录、权限及测试范围批准。已有 local-test 授权不自动覆盖此路径。

## 独立保护与范围

- 仅当前 Windows 用户 CurrentUser DPAPI，禁止 LOCAL_MACHINE；加载 crypt32/kernel32 只从 System32，禁止 UI。与 local-test 复用加密原语但格式、熵、owner/actor、目录全独立，不能互相导入。普通 Windows 明文 owner 文件方式继续拒绝。
- 根只允许 C:\Code\zhizhi-e2e-20261020 或其 git-delivery/releases/<12hex>-<run>-<attempt>。便携环境和实际 Python/Git/写路径仍须通过原 portable 检查。逐祖先拒绝 symlink/junction/重解析路径，不从外部路径导入身份。
- 固定 data/owner-acceptance，与 data/local-test、默认 data/platform、个人知识库隔离。初始化只在全新目录写 identity.json 密文、launch.json、desktop.json 无秘密配置，以及空 platform/desktop 目录；已有完整同配置只读确认，部分状态或不同端口拒绝，不覆盖或采用外来账本。
- 唯一用户 actor：portable-operational-acceptance / portable-acceptance-person，权限只有 knowledge:read/write/review，设备 ID 持久化在密文中。user 的分类字段不是读取沙箱，隔离靠独立 owner/空账本/范围批准。后台 worker、generator、independent reviewer、Writer 仍由原 WorkflowSettings 分别建立，不把 tasks:work/apply 或通配权限授给用户 bearer。
- DPAPI 不是抵御已控制同一 Windows 用户账户的恶意进程的完整沙箱，也不是 Windows DACL 验证。真实运行仍要验证当前用户解密、根绑定、配对与访问边界，不能用合成 crypto fixture 代替。

## GitHub 包内命令合同

只应由独立 owner-acceptance 包装入口调用，不能修改默认 start/local-test start 的行为。

1. 初始化模块 ahadiff.knowledge_platform.owner_identity，显式 --approve-owner-acceptance-identity，可选 --port；默认不会运行、CI 不调用初始化。
2. 已初始化服务模块 ahadiff.knowledge_platform：--owner-dpapi-identity 指向固定密文；--owner portable-operational-acceptance；--owner-browser-actor portable-acceptance-person；--state-directory 固定 data/owner-acceptance/platform；host 127.0.0.1、port 与密文一致。没有身份时拒绝，不自动初始化。
3. 批准模块 ahadiff.knowledge_platform.owner_pairing --owner-dpapi-identity 指向固定密文，20 位一次性码仅 stdin；与普通受保护文件工具共用固定 HTTPConnection approve 请求，没有任意URL、代理、重定向、重试或秘密输出。
4. 学习服务 repo 固定 data/owner-acceptance/desktop，因此 state 是其 .ahadiff；读取 data/owner-acceptance/desktop.json，identity_format=zhizhi-owner-acceptance-dpapi-v1。每个请求从同一密文读取既有 token，并检查 device_id/service_url/state 绑定，不生成明文 token 副本。

launch.json 恰为三键：mode=owner-acceptance、owner_dpapi_identity=data/owner-acceptance/identity.json、platform_port。desktop.json 初始 model.enabled=false、providers=[]；不得因 owner 身份存在就启动模型或后台。普通会话仍采用独立内存 proof/CSRF/120秒配对/900秒会话。

## 后续真实激活的批准范围建议

请由 root 合并成一次明确请求：是否允许仅在获准 Windows 便携 release 的 data/owner-acceptance 内生成上述 CurrentUser DPAPI 服务用户令牌与设备身份，建立独立空账本，并批准本机浏览器会话；只使用事先列明的合成文本/公开测试材料执行真实模型→独立复核→Writer→私有正式条目闭环。令牌不进 Git/GitHub、不读取或导入其他凭据/个人知识库，不对外公开条目。

模型仍须明确选择已审查的 codex_cli adapter 和 openai provider，以及现有 ChatGPT CLI 授权；不得新 OAuth/新付费 API 或失败回退。owner-acceptance 装配拒绝其他 provider/adapter、渠道、ASR 和 employer/third_party_private 分类。personal 标签可用于本人合成原话的隐私保护，不意味着准许发送真实私人资料。实际模型、worker、自动 Writer 开关都独立于初始化，只有范围获准后显式开启。Pi 和真实渠道仍是各自验收/授权事项，不借此配置自动接入。

## 测试证据

合成加密 fixture + 真实文件/ASGI 装配验证：不覆盖/不读 local-test、空状态、角色/路径/端口/格式/熵差异、密文payload防改、缺保护失败无写、非Windows拒绝、严格根、桌面目标/设备绑定、初始化需开关、start不初始化、默认模型worker关闭、独立reviewer/Writer、固定错误摘要。真实 Windows DPAPI、真实浏览器、模型和全部端到端仍待运行；不能据此交付。
